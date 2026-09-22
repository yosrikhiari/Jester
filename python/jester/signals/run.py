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

from . import FIELD_NAMES, Signal, TABLE, upsert
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

    for query in queries:
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

    counts = upsert(db, signals, seen_at=seen_at)
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
        "status": ("ok" if not failed_queries
                   else ("partial" if len(signals) > len(failed_queries) else "failed")),
        "note": "",
    }
    _save_run(db, row)
    # Not a column: the recovery pass needs to know WHICH queries failed on
    # this pass, and it cannot read that back off the table — the previous
    # failure's row is still there, marked error, which is exactly the point
    # of keeping it.
    row["failed_queries"] = failed_queries
    return row


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


def reclassify(db: sqlite3.Connection, rules=None, *, baseline=None, mode: str = "",
               dry_run: bool = True) -> dict:
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
    where, args = ("WHERE mode = ?", (mode,)) if mode else ("", ())
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
