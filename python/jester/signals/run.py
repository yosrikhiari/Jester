"""One collection run, and the ledger that proves it happened.

What a run has to prove: that it happened on schedule, that a failure can be
recovered afterwards, that its real unique/relevant/exported/error counts are
saved, and that finding nothing still counts as a successful run. Three of
those decide the shape of this module:

* **"counts saved"** — in a table, not in a report. `signal_run` holds one row
  per run with what it actually collected, so "three runs happened" is a
  `SELECT`, and a run that was never launched cannot be described afterwards.
* **"a zero-result run is valid"** — a run that finds nothing is a successful
  run, and it must be recorded as one. Collecting nothing and failing to
  collect are different facts, and conflating them is how a broken collector
  looks like a quiet week.
* **"+ 1 recovery pass"** — so a failure has to be *re-findable*. A query that
  fails is stored as a counted error record naming the query, and recovery
  re-runs exactly those.

Nothing here knows what a signal means. Classification is the rule set's job,
fetching is the source's, and this module only decides what happens to a record
between the two — which is why swapping Hacker News for the Reddit API later
changes nothing in this file.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from . import FIELD_NAMES, Signal, TABLE, limits, mark_removed, upsert
from .filters import apply_to, load_rules
from .sources import SourceError, get_source

RUN_TABLE = "signal_run"

#: A failed query is stored as a record whose id is derived from the query, so
#: the same query failing twice updates one row instead of growing the archive
#: with every outage. The prefix makes it greppable and impossible to mistake
#: for a real post.
FAILURE_PREFIX = "query-failure"

RUN_DDL = f"""
CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
  run_id TEXT PRIMARY KEY,
  source TEXT,
  mode TEXT,
  kind TEXT,
  started_utc TEXT,
  finished_utc TEXT,
  since TEXT,
  queries TEXT,
  collected INTEGER,
  new INTEGER,
  seen_again INTEGER,
  edited INTEGER,
  relevant INTEGER,
  buyer INTEGER,
  practitioner INTEGER,
  errors INTEGER,
  status TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_{RUN_TABLE}_started ON {RUN_TABLE} (started_utc);
"""


#: The ledger's columns. The returned run row carries a little more than the
#: table does (the list of queries that failed, which the recovery pass needs
#: and which no reader wants as a column), so the writer selects rather than
#: trusting the caller to hand it exactly the right keys.
RUN_COLUMNS = ("run_id", "source", "mode", "kind", "started_utc", "finished_utc",
               "since", "queries", "collected", "new", "seen_again", "edited",
               "relevant", "buyer", "practitioner", "errors", "status", "note")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_run_table(db: sqlite3.Connection) -> None:
    db.executescript(RUN_DDL)
    db.commit()


def _failure_record(source, query: str, message: str, run_id: str, mode: str) -> Signal:
    """A query that could not be read, as a row.

    It is a record because the alternative is a log line, and a log line is not
    counted, not queryable and not visible in the export. Silent failures
    are the thing to avoid; this is what not-silent looks like.
    """
    slug = "".join(ch if ch.isalnum() else "-" for ch in query.lower())[:60].strip("-")
    return Signal(
        source_id=f"{FAILURE_PREFIX}:{slug}",
        platform=source.platform,
        community=f"{source.name}/search",
        kind="post",
        title=f"(query failed) {query}",
        text="",
        query=query,
        mode=mode,
        run_id=run_id,
        run_status="error",
        error=message[:400],
        relevant=0,
        audience="none",
    )


def collect(db: sqlite3.Connection, *, source_name: str = "hackernews",
            queries: Optional[Iterable[str]] = None, rules=None,
            since: str = "7d", limit_per_query: int = 100,
            run_id: str = "", mode: str = "live", kind: str = "scheduled",
            source=None, seen_at: Optional[str] = None) -> dict:
    """Run every query once, classify what comes back, store it, record the run.

    Returns the run row. The same numbers go into `signal_run` and into the
    caller's report, so a report cannot quote a figure the table does not hold.
    """
    rules = rules or load_rules()
    source = source or get_source(source_name)
    queries = [q for q in (queries or rules.queries) if q and q.strip()]
    run_id = run_id or f"signals-{source.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    ensure_run_table(db)
    started = _now()
    signals: List[Signal] = []
    failed_queries: List[str] = []
    # What is left of the source's daily budget becomes this instance's cap.
    if hasattr(source, "budget"):
        source.budget = limits.budget_left(db, source)
    if hasattr(source, "reads"):
        source.reads = {}   # this run's reads only
    limit_hit = None

    for i, query in enumerate(queries):
        try:
            for signal in source.search(query, since_utc=since, limit=limit_per_query):
                signal.run_id = run_id
                signal.mode = mode
                # `apply_to` reads a comment's stored title as its thread's,
                # not its own, so a three-word reply can inherit the voice of
                # the post it answers — flagged inherited, never stated.
                apply_to(signal, rules)
                signals.append(signal)
        except SourceError as exc:
            failed_queries.append(query)
            signals.append(_failure_record(source, query, str(exc), run_id, mode))
            if limits.is_limit(exc):
                # Asked to stop, so stop: every query not yet read is filed
                # as failed too, which is what lets the recovery pass read
                # them once the cool-down is over.
                limit_hit = exc
                for rest in queries[i + 1:]:
                    failed_queries.append(rest)
                    signals.append(_failure_record(
                        source, rest, f"not asked: {exc}", run_id, mode))
                break

    limits.record_requests(db, source.name, getattr(source, "requests", 0))
    note = ""
    if limit_hit is not None:
        until = limits.mark_limited(db, source.name, limit_hit)
        note = f"limit reached: {limit_hit}; sits out until {until}"

    counts = upsert(db, signals, seen_at=seen_at)
    closed = mark_closed(db, source, mode=mode, at=seen_at)
    if closed["marked"]:
        note = "; ".join(filter(None, [note, f"{closed['marked']} listing(s) gone from the "
                                             "board; marked closed"]))
    elif closed["held"]:
        note = "; ".join(filter(None, [note, closed["held"]]))
    elif closed["judged"]:
        # Said out loud: a check that found nothing closed and a check that
        # never ran read the same in an empty note.
        note = "; ".join(filter(None, [note, f"all {closed['judged']} listing(s) checked "
                                             "are still on the board"]))
    row = {
        "run_id": run_id,
        "source": source.name,
        "mode": mode,
        "kind": kind,
        "started_utc": started,
        "finished_utc": _now(),
        "since": since,
        "queries": json.dumps(queries),
        "collected": len(signals),
        "new": counts["new"],
        "seen_again": counts["seen_again"],
        "edited": counts["edited"],
        "relevant": sum(1 for s in signals if s.relevant),
        "buyer": sum(1 for s in signals if s.audience == "buyer"),
        "practitioner": sum(1 for s in signals if s.audience == "practitioner"),
        "errors": len(failed_queries),
        # A run that collected nothing is a valid run, so "ok" is decided by
        # whether every query was READ, not by whether anything came back.
        # "limited" is its own word: the source is up and asked for less,
        # which is neither a failure nor a finished read.
        "status": ("limited" if limit_hit is not None
                   else "ok" if not failed_queries
                   else ("partial" if len(signals) > len(failed_queries) else "failed")),
        "note": note,
    }
    _save_run(db, row)
    # Not a column: the recovery pass needs to know WHICH queries failed on
    # this pass, and it cannot read that back off the table — the previous
    # failure's row is still there, marked error, which is exactly the point
    # of keeping it.
    row["failed_queries"] = failed_queries
    return row


#: If one run would close more than this share of the listings it could
#: judge, it closes none: a board returning a short or empty answer by mistake
#: must not empty the archive. Below the floor the share is not meaningful.
CLOSE_AT_MOST = 0.5
CLOSE_FLOOR = 20


def mark_closed(db: sqlite3.Connection, source, *, mode: str = "live",
                at: Optional[str] = None) -> dict:
    """Mark the listings a board no longer shows as removed at source.

    A job advert closes when the role is filled, and until now nothing
    noticed: `mark_removed` existed, but only the fixtures called it, so the
    brief and the handover slowly filled with filled roles.

    Only COMPLETE reads decide. A listing missing from a search read to the
    end is gone; one missing from a partial read may simply be further down.
    The comparison is against every listing the board returned, kept or
    filtered, so a record the rules now reject is not mistaken for a closed
    one. A record seen again later is reopened by `upsert`.
    """
    reads = getattr(source, "reads", None) or {}
    complete = {q: r["ids"] for q, r in reads.items() if r.get("complete") and r.get("ids")}
    if not complete or mode != "live":
        return {"marked": 0, "held": "", "judged": 0}
    seen = set().union(*complete.values())
    marks = ",".join("?" * len(complete))
    rows = db.execute(
        f"SELECT record_id, source_id FROM {TABLE} WHERE platform = ? AND mode = ? "
        f"AND COALESCE(removed_utc, '') = '' AND query IN ({marks}) "
        "AND source_id NOT LIKE ?",
        (source.platform, mode, *complete, f"{FAILURE_PREFIX}:%")).fetchall()
    gone = [r[0] for r in rows if r[1] not in seen]
    if len(gone) > CLOSE_FLOOR and len(gone) > CLOSE_AT_MOST * len(rows):
        return {"marked": 0, "judged": len(rows),
                "held": f"{len(gone)} of {len(rows)} listing(s) missing from the board -- "
                        "too many to be real; none marked closed"}
    mark_removed(db, gone, at=at)
    return {"marked": len(gone), "held": "", "judged": len(rows)}


def recover(db: sqlite3.Connection, *, source_name: str = "hackernews", rules=None,
            since: str = "7d", limit_per_query: int = 100, run_id: str = "",
            mode: str = "live", source=None, seen_at: Optional[str] = None) -> dict:
    """Re-run the queries that failed, and clear the ones that now succeed.

    The gate asks for three runs *and a recovery pass*, which only means
    something if a failure can be found again. It can: every failed query left
    a row naming itself, and this reads them back.

    A recovered query's error row is marked recovered rather than deleted. The
    outage happened; an archive that erases its own failures cannot show that
    the recovery worked.
    """
    ensure_run_table(db)
    failed = [r["query"] for r in db.execute(
        f"SELECT DISTINCT query FROM {TABLE} "
        "WHERE run_status = 'error' AND source_id LIKE ? AND removed_utc = '' AND mode = ?",
        (f"{FAILURE_PREFIX}:%", mode)).fetchall() if r["query"]]

    if not failed:
        row = {"run_id": run_id or f"recovery-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
               "source": source_name, "mode": mode, "kind": "recovery",
               "started_utc": _now(), "finished_utc": _now(), "since": since,
               "queries": "[]", "collected": 0, "new": 0, "seen_again": 0, "edited": 0,
               "relevant": 0, "buyer": 0, "practitioner": 0, "errors": 0,
               "status": "ok", "note": "nothing to recover"}
        _save_run(db, row)
        return row

    row = collect(db, source_name=source_name, queries=failed, rules=rules, since=since,
                  limit_per_query=limit_per_query, run_id=run_id, mode=mode,
                  kind="recovery", source=source, seen_at=seen_at)

    # Decided by the pass that just ran, not by reading the table back. The
    # old failure row is still there and still says `error` — removal is
    # recorded, not deleted — so asking the table "is this query still
    # failing?" always answers yes and nothing is ever recovered. That was a
    # real bug, caught by the test that asserts a recovered failure is marked
    # rather than erased.
    recovered = [q for q in failed if q not in set(row.get("failed_queries", ()))]
    if recovered:
        db.executemany(
            f"UPDATE {TABLE} SET run_status='ok', error=? "
            "WHERE source_id LIKE ? AND query = ? AND mode = ?",
            [("recovered on a later pass; the failure above is kept as a record",
              f"{FAILURE_PREFIX}:%", q, mode) for q in recovered])
        db.commit()
    row["note"] = f"recovered {len(recovered)} of {len(failed)} failed query(ies)"
    _save_run(db, row)
    return row


def collect_or_fall_back(db: sqlite3.Connection, *, fallback=(),
                         fallback_rules=None, fallback_limit: int = 100,
                         **kwargs) -> List[dict]:
    """Collect from one source; while nothing new comes back, try the next.

    Written for the hiring collector, which reads one monthly thread: between
    the 1st of one month and the next it has nothing new to say, so a daily
    run -- or a button pressed three times -- re-reads the same ~490 adverts
    and stores none of them. `fallback` names other sources of the same kind
    (job boards), walked in order until one adds something.

    The fallbacks are scored with the SAME rules as the first source unless
    told otherwise: they are the same kind of record (a job advert) asking the
    same question. Each is searched with its own `default_queries` -- the
    hiring rules' "12" means twelve months to Hacker News and is a meaningless
    keyword to a job board.

    It is also the orchestrator for the sources' limits (`signals.limits`).
    The chain moves on for three reasons, and the ledger names which:

    * **nothing new** -- a clean zero;
    * **limit reached** -- the source rate-limited us or spent its daily
      budget mid-run. What it did read is kept; the rest waits for recovery;
    * **sitting out** -- the source is still cooling down, or its budget is
      spent, so it is skipped before a single request and gets a "skipped"
      row saying until when.

    A FAILED first run is not "nothing new", it is "could not look", and
    quietly running something else would bury it -- a block page or a 403 is
    a failure, not a limit. A failed fallback is recorded and the walk
    continues: the failure is its own row, and the next board may well be up.

    Returns every run row, first to last.
    """
    if isinstance(fallback, str):
        fallback = [f.strip() for f in fallback.split(",") if f.strip()]
    first = kwargs.get("source") or get_source(kwargs.get("source_name", "hackernews"))
    chain = [first] + [get_source(name) for name in fallback]
    rows: List[dict] = []
    why_first = ""   # why the chain left the first source, for the fallbacks' notes

    for i, source in enumerate(chain):
        if rows:
            prev = rows[-1]
            why = _why_moved(prev, first=(i == 1))
            if not why:
                break
            why_first = why_first or why
            prev["note"] = "; ".join(filter(None, [prev.get("note"),
                                                   f"{why}; fell back to {source.name}"]))
            _save_run(db, prev)

        sitting_out = limits.blocked(db, source)
        if sitting_out:
            rows.append(_skipped(db, source, sitting_out, kwargs))
            continue
        if i == 0:
            row = collect(db, **{**kwargs, "source": source})
        else:
            row = collect(db, source_name=source.name, source=source,
                          rules=fallback_rules or kwargs.get("rules"),
                          queries=list(getattr(source, "default_queries", ()) or ()) or None,
                          since=kwargs.get("since", "7d"), limit_per_query=fallback_limit,
                          mode=kwargs.get("mode", "live"),
                          kind=kwargs.get("kind", "scheduled"),
                          seen_at=kwargs.get("seen_at"))
            row["note"] = "; ".join(filter(None, [
                f"fallback: {rows[0]['source']} {_BECAUSE[why_first]}", row.get("note")]))
            _save_run(db, row)
        rows.append(row)
    return rows


_BECAUSE = {"sitting out": "was sitting out", "limit reached": "hit its limit",
            "could not look": "could not be read", "nothing new": "had nothing new"}


def _why_moved(row: dict, *, first: bool) -> str:
    """Why the chain goes past `row`'s source, or "" when it stops there.

    A limit moves on even when the source added something: it stopped
    reading part-way, so the next board is the rest of today's look.
    """
    if row["status"] == "skipped":
        return "sitting out"
    if row["status"] == "limited":
        return "limit reached"
    if row["status"] == "failed":
        # The first source failing stops the chain; a fallback failing does not.
        return "" if first else "could not look"
    return "" if row["new"] else "nothing new"


def _skipped(db: sqlite3.Connection, source, reason: str, kwargs: dict) -> dict:
    """A ledger row for a source the chain did not ask, and why."""
    now = _now()
    row = {"run_id": f"signals-{source.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}",
           "source": source.name, "mode": kwargs.get("mode", "live"),
           "kind": kwargs.get("kind", "scheduled"), "started_utc": now,
           "finished_utc": now, "since": kwargs.get("since", "7d"), "queries": "[]",
           "collected": 0, "new": 0, "seen_again": 0, "edited": 0, "relevant": 0,
           "buyer": 0, "practitioner": 0, "errors": 0, "status": "skipped",
           "note": f"not asked: {reason}"}
    _save_run(db, row)
    return row


def reclassify(db: sqlite3.Connection, rules=None, *, baseline=None, mode: str = "",
               community: str = "", dry_run: bool = True) -> dict:
    """Re-score every stored record and report what moved.

    This is how a rule change is argued for rather than asserted. W8 asks for
    *"one evidenced fix: before/after compared"*, and the only honest
    comparison runs both rule sets over **the same text**.

    That last point is not pedantry; it is a mistake this function made once.
    Comparing new verdicts against the *stored* ones looks like a before/after
    and is not: the stored verdict was computed from the full post, and the
    archive keeps only a 600-character excerpt (retention §14). Re-scoring the
    excerpt therefore differs from the stored verdict for two unrelated
    reasons — the rules changed, and the evidence is shorter — and reading
    that difference as "my rule change did this" credits the rules with work
    truncation did. So:

    * with `baseline` (another rule set), both are scored over the same stored
      excerpt and the delta is attributable to the rules alone. This is the
      comparison to quote.
    * without it, the stored verdict is the baseline and the result says
      `comparable: False`, because it is not a clean A/B.

    Defaults to a dry run. A stored verdict is evidence of what the archive
    said on a date; overwriting it should cost a second command.
    """
    rules = rules or load_rules()
    # `community` exists because one archive holds rows scored under TWO rule
    # sets: hn/hiring and the Reddit rooms are scored with hiring_rules.yaml,
    # hn/comment and hn/ask with signal_rules.yaml. Without a scope, correcting
    # a hiring rule meant re-scoring 528 forum rows against rules written for
    # job adverts and overwriting verdicts that were never wrong. A tool whose
    # only setting is "everything" cannot fix half an archive.
    clauses, args = [], []
    if mode:
        clauses.append("mode = ?"); args.append(mode)
    if community:
        clauses.append("community = ?"); args.append(community)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args = tuple(args)
    rows = db.execute(
        f"SELECT record_id, kind, title, excerpt, audience, relevant, match_confidence "
        f"FROM {TABLE} {where}", args).fetchall()

    before = {"buyer": 0, "practitioner": 0, "none": 0}
    after = {"buyer": 0, "practitioner": 0, "none": 0}
    changes: List[dict] = []
    updates = []

    for row in rows:
        probe = Signal(source_id="x", kind=row["kind"], title=row["title"] or "",
                       text=row["excerpt"] or "")
        apply_to(probe, rules)
        if baseline is not None:
            old = Signal(source_id="x", kind=row["kind"], title=row["title"] or "",
                         text=row["excerpt"] or "")
            apply_to(old, baseline)
            was = old.audience or "none"
        else:
            was = row["audience"] or "none"
        now = probe.audience or "none"
        before[was] = before.get(was, 0) + 1
        after[now] = after.get(now, 0) + 1
        if was != now:
            changes.append({"record_id": row["record_id"], "from": was, "to": now,
                            "title": (row["title"] or "")[:90],
                            "confidence": round(probe.match_confidence, 2),
                            "reason": probe.match_reason})
        updates.append((probe.relevant, probe.audience, probe.match_confidence,
                        probe.match_reason, row["record_id"]))

    if not dry_run:
        db.executemany(
            f"UPDATE {TABLE} SET relevant=?, audience=?, match_confidence=?, "
            "match_reason=? WHERE record_id=?", updates)
        db.commit()

    return {"records": len(rows), "before": before, "after": after,
            "changed": len(changes), "changes": changes, "applied": not dry_run,
            "comparable": baseline is not None,
            "config_version": getattr(rules, "config_version", "")}


def _save_run(db: sqlite3.Connection, row: dict) -> None:
    cols = [c for c in RUN_COLUMNS if c in row]
    db.execute(
        f"INSERT OR REPLACE INTO {RUN_TABLE} ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    db.commit()


def runs(db: sqlite3.Connection, *, limit: int = 20, mode: str = "") -> List[dict]:
    """The ledger, newest first. `jester signals runs` prints it, and the
    schedule is read off it."""
    ensure_run_table(db)
    where, args = ("WHERE mode = ?", (mode,)) if mode else ("", ())
    return [dict(r) for r in db.execute(
        f"SELECT * FROM {RUN_TABLE} {where} ORDER BY started_utc DESC LIMIT ?",
        (*args, limit)).fetchall()]
