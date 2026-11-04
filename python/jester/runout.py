"""Read side of the run-outcome ledger — the drift ledger.

``run_outcome`` is a Go-owned table (``go/internal/store/store.go`` creates it
with CREATE TABLE IF NOT EXISTS and deliberately does NOT bump schema_version,
which is a strict cross-language equality at 1). The worker leaves one row per
visited source per clean OR failing run. The console opens the archive with
``migrate=False`` and only reads it — a database the worker has not touched yet
simply does not have the table, which is a normal state here, not an error
(the same rule the ``source_state`` reader lives by).

The drift rules read the rows and say what they see. They never write, move,
re-parse or repair anything: an alarm is a claim about a trend, and the row
history stays the only authority. Two limits are stated where the rules live:

- Anchors=0 alone proves nothing in an anchored profile — the anchor is the
  only way that mode sees tiles at all, so anchor silence is only read inside
  a rule that ALSO requires clean pages or a coverage signal.
- A single quiet run is weather, not climate. Baseline rules need history.
"""
import json

#: How deep a trend card looks back. Twelve runs is roughly a day of the
#: default cadence — enough to see a portal degrade without the card claiming
#: to be a historical report.
SERIES_PER_SOURCE = 12

#: How full a field's baseline coverage must be before a drop may alarm.
#: A field that lived its whole life at 20% has no clear "lost" state to fall
#: from; guarding on a floor keeps a permanently thin field quiet.
COVERAGE_FLOOR = 0.5

#: How far below the baseline a field's latest coverage must fall to alarm.
#: Below this, day-to-day page composition reads as noise, and one page that
#: happens to populate a field on 3 of 10 rows must not page anyone.
COVERAGE_DROP = 0.25

#: Consecutive failed visits before "it broke again" becomes "it stays broken".
REPEAT_FAILURE_RUNS = 3

# positional columns of the run_outcome read — row_factory is toggled between
# sqlite3.Row and the default by other readers on this same connection, so a
# name-keyed access works or raises depending on call order (the acknowledgment
# _source_state already made).
_COLS = ("source", "run_id", "observed_at", "items", "fresh", "dup",
         "pages_ok", "pages_err", "bytes", "latency_ms", "fetch_mode",
         "first_err", "stats_json")


def _row(r):
    out = dict(zip(_COLS, r))
    out["first_err"] = out["first_err"] or ""
    out["fetch_mode"] = out["fetch_mode"] or ""
    raw = out.pop("stats_json")
    # "absent" (NULL, community rows) and "empty" are different facts; a
    # corrupt blob degrades to an empty dict rather than killing the card.
    if raw is None:
        out["stats"] = {}
    else:
        try:
            out["stats"] = json.loads(raw) or {}
        except (TypeError, ValueError):
            out["stats"] = {}
    return out


def load_outcomes(conn, per_source=SERIES_PER_SOURCE, scan_limit=4000):
    """Recent ledger rows grouped per source, each list newest first.

    A flat LIMIT-then-group keeps this portable SQL: window functions would
    push the query into dialect territory for no gain at these sizes, and the
    scan cap bounds the work a very long ledger can cost a dashboard request.
    Returns {} when the worker has not created the table yet — never raises.
    """
    try:
        rows = conn.execute(
            "SELECT source, run_id, observed_at, items, fresh, dup, "
            "pages_ok, pages_err, bytes, latency_ms, fetch_mode, "
            "first_err, stats_json "
            "FROM run_outcome ORDER BY observed_at DESC, run_id DESC "
            "LIMIT ?", (scan_limit,)
        ).fetchall()
    except Exception:  # noqa: BLE001 - table absent until the worker runs
        return {}
    by_source = {}
    for r in rows:
        row = _row(r)
        series = by_source.setdefault(row["source"], [])
        if len(series) < per_source:
            series.append(row)
    return by_source


def _yield(row):
    """What the visit kept, by the counter its platform has.

    Realestate rows carry the parse counters inside stats_json; community rows
    carry only items. Both read as "what the source handed we archived".
    """
    parse = (row.get("stats") or {}).get("parse") or {}
    if "kept" in parse:
        return parse["kept"]
    return row["items"]


def drift_alarms(rows):
    """What one source's trend says, from the rows alone. Newest first in.

    Kinds:
    - all-pages-failed: the walk planned pages and every one of them failed.
      A fetch-level condemnation — the portal refused, timed out or was
      unreachable — and the loudest thing the ledger can say.
    - shape-gone: pages parsed clean but kept nothing, while the same source
      still kept rows on its prior visits. NOT anchor silence alone: in an
      anchored profile the anchor is the only way we see tiles at all, so
      "the page no longer has the wanted shape" needs the clean-pages
      corroboration, exactly the limit siteprofile's counters document.
    - coverage-drop: a field the profile reads stopped arriving — the subtle
      half-drift (prices on a third of the tiles) a plain zero never shows.
    - repeat-failure: this visit and the ones before it all errored. Once is
      weather; three in a row is a source that answers but cannot be read.
    - grade-failed: the gates (internal/grade, carried in the row's stats
      under "grade") failed the portal's floors, or carried a structural
      finding. The ledger's NEWEST graded run decides — a later ungraded
      row (a walk that parsed nothing) does not clear it, and a later
      passing grade clears it; the alarm says which run it is from.
    """
    if not rows:
        return []
    latest = rows[0]
    history = rows[1:]
    out = []
    if latest["pages_err"] > 0 and latest["pages_ok"] == 0:
        out.append({"kind": "all-pages-failed",
                    "note": f"{latest['pages_err']} page(s) failed, none parsed"})
    if (not latest["first_err"] and _yield(latest) == 0 and history
            and any(_yield(h) > 0 for h in history)):
        out.append({"kind": "shape-gone",
                    "note": "visited, nothing kept — but not long ago it kept rows"})
    parse = latest.get("stats") or {}
    if parse:
        baseline = [h for h in history if (h.get("stats") or {})]
        if len(baseline) >= 2:
            drop = _coverage_drop(parse.get("parse") or {}, baseline)
            if drop:
                out.append(drop)
    grade = _grade_failed(rows)
    if grade:
        out.append(grade)
    if latest["first_err"]:
        # A streak, not a lifetime count: one success between failures means
        # the portal answers sometimes, which reads differently to an operator
        # than three in a row.
        streak = 1
        for h in history:
            if not h["first_err"]:
                break
            streak += 1
        if streak >= REPEAT_FAILURE_RUNS:
            out.append({"kind": "repeat-failure",
                        "note": f"{streak} consecutive failed visit(s)"})
    return out


def _grade_failed(series):
    """The grade alarm, or None: the ledger's newest graded run must have failed.

    The verdict is carried in the row's stats_json as {grade: {portals: [...],
    global_failures?: [...]}} — the worker embeds it (see go/cmd/worker), the
    CLI's bulk grade uses the same shape. A grade that passed calls nothing;
    thin samples make no verdict and alarm nothing; the chip's label says HOW
    MANY gates failed so the trend stays one glance readable.
    """
    graded = None
    for row in series:
        if (row.get("stats") or {}).get("grade"):
            graded = row
            break
    if graded is None:
        return None
    g = graded["stats"]["grade"]
    names = []
    findings = []
    for p in g.get("portals") or ():
        for gate in p.get("gates") or ():
            if gate.get("verdict") == "fail":
                names.append(gate.get("name") or "?")
        findings.extend(p.get("failures") or ())
    findings.extend(g.get("global_failures") or ())
    if not names and not findings:
        # The newest grade passed; an older failing run is honoured history,
        # not a live alarm.
        return None
    if names:
        note = f"run {graded['run_id']}: {len(names)} gate(s) failed ({', '.join(names)})"
        label = f"grade: {len(names)} gate(s)"
        if findings:
            note += f"; {len(findings)} more finding(s)"
    else:
        note = f"run {graded['run_id']}: {len(findings)} finding(s) - {findings[0]}"
        label = f"grade: {len(findings)} finding(s)"
    return {"kind": "grade-failed", "label": label, "note": note}


def _coverage_drop(parse_now, baseline):
    """The first field whose coverage fell off its rolling baseline.

    Coverage fraction = covered kept rows / kept rows, per field. The baseline
    pools the history's counters (covered and kept summed across visits), so
    two half-empty pages weigh like one full one; the latest row's fraction is
    compared against that pooled fraction. Needs the baseline good
    (>= COVERAGE_FLOOR) and the drop large (COVERAGE_DROP) so page-composition
    noise stays noise. One alarm per source even if two fields drop — the card
    links to the ledger, not to a page of duplicates.
    """
    now_kept = parse_now.get("kept") or 0
    if now_kept <= 0:
        return None
    fields = parse_now.get("fields") or {}
    base_kept = 0
    base_covered = {}
    for h in baseline:
        parse = (h.get("stats") or {}).get("parse") or {}
        kept = parse.get("kept") or 0
        base_kept += kept
        for name, n in (parse.get("fields") or {}).items():
            base_covered[name] = base_covered.get(name, 0) + n
    if base_kept <= 0:
        return None
    for name in sorted(fields):
        base_frac = base_covered.get(name, 0) / base_kept
        if base_frac >= COVERAGE_FLOOR and fields[name] / now_kept <= base_frac - COVERAGE_DROP:
            return {"kind": "coverage-drop", "field": name,
                    "note": f"{name} covered on {fields[name]} of {now_kept} kept row(s); "
                            f"its baseline is near {base_frac:.0%}"}
    return None


def trend(name, series):
    """The sources-page card for one source, or None when never visited.

    Kept small on purpose: the table cell shows the latest run and the alarm
    chips; anything deeper belongs to the ledger view, not to a 91-row page.
    """
    if not series:
        return None
    latest = series[0]
    walk = latest.get("stats") or {}  # realestate rows carry the walk stats
    return {
        "run_id": latest["run_id"],
        "observed_at": latest["observed_at"],
        "items": latest["items"],
        "fresh": latest["fresh"],
        "dup": latest["dup"],
        "first_err": latest["first_err"],
        "pages_ok": walk.get("pages_ok", 0),
        "pages_err": walk.get("pages_err", 0),
        "drift": drift_alarms(series),
        "runs": len(series),
    }
