"""Maintenance passes (design §5 S7, AD7): measure, surface, never delete.

Three deterministic checks, no model and no network, all APPENDING to the
same event ledger rather than mutating live rows:

- scraper drift: per source, the share of ingest batches that came back
  EMPTY over a trailing window, against that source's whole-history share.
  A jump beyond `maintenance_null_rate_spike` flags `drift_suspect` - the
  manual analogue of a self-healing crawler: what to look at, never what
  to fix (a model re-deriving selectors is a much-later step and would
  face the same G6 human gate the spec journey has).
- stale nuggets: clusterless rows whose ingest age passes
  `maintenance_stale_days`. The event LISTS them for a human; nothing is
  deleted, re-pointed or rewritten here.
- queues: the re-embed (`nuggets.needs_reembed`) and re-score
  (`ideas.needs_score`) queues aggregated and surfaced - they already
  have their own mechanisms; this is a read-only head-count.

Every event row keeps reason + inputs + outcome + actor, so the who/what/
why discipline holds for machinery too. Append-only by construction: no
UPDATE or DELETE exists against maintenance_events anywhere.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: A trailing window shorter than this cannot tell drift from occasion.
MIN_WINDOW_ROWS = 3

#: The flagged list in an event's outcome is capped; the count stays exact.
MAX_FLAGGED = 250

KINDS = ("drift_suspect", "stale_nuggets", "queues")

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_events (
    event_id    TEXT PRIMARY KEY,
    created_utc TEXT NOT NULL,
    kind        TEXT NOT NULL,
    actor_kind  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maint_kind ON maintenance_events(kind);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_maintenance_schema(db: sqlite3.Connection) -> None:
    db.executescript(EVENT_SCHEMA)
    db.commit()


def _mint_seq(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()
    return int(row[0])


def _append_event(db: sqlite3.Connection, *, kind: str, actor_kind: str,
                  actor: str, reason: str, inputs: Dict[str, Any],
                  outcome: Dict[str, Any]) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown maintenance kind {kind!r}")
    ensure_maintenance_schema(db)
    n = _mint_seq(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    event_id = f"ME-{stamp}-{n:04d}"
    db.execute(
        "INSERT INTO maintenance_events(event_id, created_utc, kind, "
        "actor_kind, actor, reason, inputs_json, outcome_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (event_id, _utcnow(), kind, actor_kind, actor, reason,
         json.dumps(inputs, ensure_ascii=False),
         json.dumps(outcome, ensure_ascii=False)),
    )
    db.commit()
    return {"event_id": event_id, "kind": kind, "reason": reason,
            "inputs": inputs, "outcome": outcome}


# ---- drift --------------------------------------------------------------------

def _batch_comments(text: Optional[str]) -> int:
    """Count of comments a batch's JSON holds; unreadable counts as 0.

    Go marshals an empty comment slice as JSON `null` and a truncated write
    can leave invalid JSON (R55); both read as "nothing came out", which is
    exactly what a null-rate must measure.
    """
    if not text:
        return 0
    try:
        payload = json.loads(text or "null")
    except (TypeError, ValueError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def check_drift(cfg, db: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Window vs upstream-history null rates per source; spike fires a flag.

    The baseline is the history BEFORE the window - a window that swallowed
    the whole ledger cannot show change, and a thin baseline (fewer than
    MIN_WINDOW_ROWS batches) cannot either.
    """
    window = int(getattr(cfg, "scheduler_window_batches", 30))
    spike = float(getattr(cfg, "maintenance_null_rate_spike", 0.25))
    events: List[Dict[str, Any]] = []
    rows = db.execute(
        "SELECT source, comments, status FROM ingest_batch "
        "WHERE source IS NOT NULL AND source != '' ORDER BY id").fetchall()
    by_source: Dict[str, List[int]] = {}
    for r in rows:
        by_source.setdefault(r[0], []).append(_batch_comments(r[1]))
    for name, counts in sorted(by_source.items()):
        tail = counts[-window:]
        base = counts[:-window] if window < len(counts) else []
        if len(tail) < MIN_WINDOW_ROWS or len(base) < MIN_WINDOW_ROWS:
            continue  # not enough history on one side to call change
        baseline_rate = sum(1 for c in base if c == 0) / len(base)
        window_rate = sum(1 for c in tail if c == 0) / len(tail)
        if window_rate - baseline_rate >= spike:
            events.append(_append_event(
                db, kind="drift_suspect",
                actor_kind="scheduler", actor="maintenance",
                reason=(f"empty-batch rate jumped {baseline_rate:.0%} -> "
                        f"{window_rate:.0%} over the last {len(tail)} "
                        "batches (spike threshold "
                        f"{spike:.0%})"),
                inputs={"source": name, "batches": len(counts),
                        "window": len(tail),
                        "baseline_null_rate": round(baseline_rate, 4),
                        "window_null_rate": round(window_rate, 4)},
                outcome={"looking_glass": "inspect the source's recent "
                                            "batches before its next turns; "
                                            "the scheduler will probe it "
                                            "cheaper meanwhile"},
            ))
    return events


# ---- stale nuggets ------------------------------------------------------------

def _clustered_keys(db: sqlite3.Connection) -> set:
    keys: set = set()
    for (blob,) in db.execute(
            "SELECT nugget_keys FROM clusters WHERE nugget_keys IS NOT NULL"):
        try:
            got = json.loads(blob or "null")
        except (TypeError, ValueError):
            continue
        if isinstance(got, list):
            keys.update(got)
    return keys


def _age_days(created_at: str, now: datetime) -> int:
    try:
        made = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return -1  # unparseable: never silently stale, it shows in checks
    return (now - made).days


def check_stale_nuggets(cfg, db: sqlite3.Connection,
                        now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Clusterless + old = flagged for review. Nothing is deleted."""
    stale_days = int(getattr(cfg, "maintenance_stale_days", 90))
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    clustered = _clustered_keys(db)
    rows = db.execute(
        "SELECT unique_key, created_at, platform, category, extracted_insight "
        "FROM nuggets ORDER BY id").fetchall()
    stale: List[Dict[str, Any]] = []
    for unique_key, created_at, platform, category, insight in rows:
        if unique_key in clustered:
            continue
        age = _age_days(created_at, now)
        if age >= stale_days:
            stale.append({"unique_key": unique_key, "age_days": age,
                          "platform": platform, "category": category})
    if not stale:
        return []
    return [_append_event(
        db, kind="stale_nuggets",
        actor_kind="scheduler", actor="maintenance",
        reason=(f"{len(stale)} clusterless nugget(s) at least "
                f"{stale_days} days old - flagged for review, nothing "
                "deleted"),
        inputs={"clusterless": len(rows) and
                sum(1 for unique_key, *_ in rows
                    if unique_key not in clustered),
                "stale_days": stale_days},
        outcome={"flagged": stale[:MAX_FLAGGED], "flagged_count": len(stale)},
    )]


# ---- queues: surface, do not re-implement --------------------------------------

def queue_counts(db: sqlite3.Connection) -> Dict[str, int]:
    re_ = db.execute("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1"
                     ).fetchone()[0]
    rs = db.execute("SELECT COUNT(*) FROM ideas WHERE needs_score=1"
                    ).fetchone()[0]
    return {"needs_reembed": int(re_), "needs_score": int(rs)}


def check_queues(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    counts = queue_counts(db)
    if not any(counts.values()):
        return []
    what = []
    if counts["needs_reembed"]:
        what.append(f"{counts['needs_reembed']} nugget(s) need re-embedding")
    if counts["needs_score"]:
        what.append(f"{counts['needs_score']} idea(s) need re-scoring")
    return [_append_event(
        db, kind="queues", actor_kind="scheduler", actor="maintenance",
        reason="; ".join(what) + " - the queues own the fixing, this only "
                                    "counts heads",
        inputs=dict(counts),
        outcome={"note": "run the re-embed / re-score mechanisms that "
                         "already exist; maintenance does not repair"},
    )]


# ---- the whole pass ------------------------------------------------------------

def maintenance_pass(cfg, db: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Run every check, append their events, return them in kind order.

    Deterministic given the archive (the wall clock lands ONLY in event
    ids and created_utc, never in the decisions).
    """
    events: List[Dict[str, Any]] = []
    events.extend(check_drift(cfg, db))
    events.extend(check_stale_nuggets(cfg, db))
    events.extend(check_queues(db))
    return events


def event_rows(db: sqlite3.Connection, *, limit: int = 50) -> List[Dict[str, Any]]:
    # cursor-scoped Row factory: never mutate the caller's connection
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        "SELECT * FROM maintenance_events ORDER BY created_utc DESC "
        "LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["inputs"] = json.loads(d.get("inputs_json") or "null")
        d["outcome"] = json.loads(d.get("outcome_json") or "null")
        out.append(d)
    return out


# ---- the CLI -------------------------------------------------------------------

def cmd_maintenance(args) -> int:
    """`jester maintenance`: run the pass, print it, leave the ledger row."""
    from jester.config import load_thresholds
    from jester.store import open_db

    base = Path(args.config) if args.config \
        else Path(__file__).resolve().parents[2] / "config"
    cfg = load_thresholds(base / "thresholds.yaml")
    db = open_db(args.db)
    try:
        events = maintenance_pass(cfg, db)
        print(f"maintenance: {len(events)} event(s) appended")
        for e in events:
            print(f"  {e['event_id']}  {e['kind']}")
            print(f"    {e['reason']}")
        counts = queue_counts(db)
        print(f"queues now: needs_reembed={counts['needs_reembed']} "
              f"needs_score={counts['needs_score']}")
        return 0
    finally:
        db.close()


def maintenance_cli(sub) -> None:
    """Register `jester maintenance`; one delegation line from cli.main."""
    m = sub.add_parser(
        "maintenance",
        help="deterministic upkeep pass: drift suspects, stale clusterless "
             "nuggets, queue head-counts - all as append-only events",
    )
    m.add_argument("--db", default="data/jester.db")
    m.add_argument("--config", default=None,
                   help="config dir (defaults beside this install)")
    m.set_defaults(func=cmd_maintenance)
