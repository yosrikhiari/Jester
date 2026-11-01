"""Is each source alive, or only quiet?

`jester signals runs` answers "what happened", one row at a time. It cannot
answer the question an operator asks in the morning: is this source quiet this
week, or dead? A source returning zero rows and a source that stopped running
both print "collected 0", and the run's own status is `ok` in both cases --
zero results is a valid run by design, which is exactly what makes the two look
alike.

So every run leaves a per-source streak in `source_state`, and this module
turns the streaks and the run ledger into one verdict with an exit code:

  HEALTHY  the last read yielded rows
  QUIET    1-2 consecutive zero-row runs
  BARREN   3 or more consecutive zero-row runs
  STALE    no read at all in the last 36 hours
  ERROR    the last read raised

Two rules shape the module:

* **The read never writes.** `signals` opens its database by running DDL, so
  pointing a diagnostic at that helper would migrate the archive it is
  reporting on. This opens the file read-only and never creates one. A table
  that is missing is reported, not created, and the streak falls back to the
  run ledger so an un-migrated archive still gets an honest answer.
* **One threshold, one place.** The constants below are the only definition of
  what quiet, barren and stale mean. `signals/limits.py` has no
  `PARK_AFTER_BARREN_VISITS` (the console has its own, unrelated visit-parking
  rule), so `BARREN_AFTER_ZERO_VISITS` is defined here and nowhere else.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SOURCE_STATE_TABLE = "source_state"
RUN_TABLE = "signal_run"
SIGNAL_TABLE = "problem_signal"

HEALTHY = "HEALTHY"
QUIET = "QUIET"
BARREN = "BARREN"
STALE = "STALE"
ERROR = "ERROR"

#: The statuses that make `jester health` exit non-zero.
FAILING = (BARREN, STALE, ERROR)

#: Consecutive zero-row runs that mean the source is not quiet, it is barren.
BARREN_AFTER_ZERO_VISITS = 3

#: A daily source that has not been read in 36 hours has missed a day (and the
#: grace). Longer than 24 so a late tick is not a finding; shorter than 48 so a
#: whole missed day is.
STALE_AFTER_HOURS = 36

SOURCE_STATE_DDL = f"""
CREATE TABLE IF NOT EXISTS {SOURCE_STATE_TABLE} (
  source TEXT PRIMARY KEY,
  consecutive_zero INTEGER NOT NULL DEFAULT 0,
  consecutive_error INTEGER NOT NULL DEFAULT 0,
  last_ok_utc TEXT NOT NULL DEFAULT '',
  last_rows INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated_utc TEXT NOT NULL DEFAULT ''
);
"""


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def ensure_source_state_table(db: sqlite3.Connection) -> None:
    """The additive migration. Called by the WRITE path only (`run.collect`),
    never by `report`, so a diagnostic cannot reshape what it diagnoses."""
    db.executescript(SOURCE_STATE_DDL)
    db.commit()


def _state(db: sqlite3.Connection, source: str) -> dict:
    ensure_source_state_table(db)
    row = db.execute(
        f"SELECT consecutive_zero, consecutive_error, last_ok_utc, last_rows, "
        f"last_error, updated_utc FROM {SOURCE_STATE_TABLE} WHERE source = ?",
        (source,)).fetchone()
    if row is None:
        return {"consecutive_zero": 0, "consecutive_error": 0, "last_ok_utc": "",
                "last_rows": 0, "last_error": "", "updated_utc": ""}
    return dict(zip(("consecutive_zero", "consecutive_error", "last_ok_utc",
                     "last_rows", "last_error", "updated_utc"), tuple(row)))


def record_source_state(db: sqlite3.Connection, source: str, row: dict, *,
                        at: Optional[str] = None) -> dict:
    """Fold one finished run into the source's streak, and return the state.

    The three outcomes are distinct, and the streaks say which happened:

    * rows came back            -- both streaks reset, `last_ok_utc` moves;
    * zero rows, no error       -- `consecutive_zero` grows (the quiet streak);
    * a query raised            -- `consecutive_error` grows, quiet resets.

    A `limited` run moves neither streak: the source asked us to stop, which is
    not a verdict on whether it has anything to say. It is still recorded as
    the last thing that happened to the source.
    """
    state = _state(db, source)
    status = str(row.get("status") or "")
    errors = int(row.get("errors") or 0)
    collected = int(row.get("collected") or 0)
    stamp = at or row.get("finished_utc") or _iso(datetime.now(timezone.utc))

    if status == "limited":
        pass
    elif errors:
        state["consecutive_error"] += 1
        state["consecutive_zero"] = 0
        state["last_error"] = (row.get("note")
                               or f"{errors} quer{'y' if errors == 1 else 'ies'} failed")[:300]
    elif collected == 0:
        state["consecutive_zero"] += 1
        state["consecutive_error"] = 0
        state["last_error"] = ""
    else:
        state["consecutive_zero"] = 0
        state["consecutive_error"] = 0
        state["last_error"] = ""
        state["last_ok_utc"] = stamp

    state["last_rows"] = collected
    state["updated_utc"] = stamp
    db.execute(
        f"INSERT OR REPLACE INTO {SOURCE_STATE_TABLE} "
        "(source, consecutive_zero, consecutive_error, last_ok_utc, last_rows, "
        "last_error, updated_utc) VALUES (?,?,?,?,?,?,?)",
        (source, state["consecutive_zero"], state["consecutive_error"],
         state["last_ok_utc"], state["last_rows"], state["last_error"],
         state["updated_utc"]))
    db.commit()
    return state


# ---------------------------------------------------------------------------
# The read path
# ---------------------------------------------------------------------------

def _connect_read_only(db_path) -> tuple:
    """(connection, "") or (None, why). Never creates a file or a table.

    Deliberately NOT a SQLite `mode=ro` connection: `store.open_db` documents
    what was measured here — reading a file whose other writers are active
    still needs write access to its directory for lock handling, so plain
    SELECTs intermittently died with "attempt to write a readonly database" on
    the Windows bind mount. The connection is ordinary and the restraint is in
    this module: it selects, never writes, and refuses to connect at all when
    the file is absent (SQLite would create it).
    """
    path = Path(db_path)
    if not path.exists():
        return None, f"no database at {db_path}"
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1").fetchone()
        return conn, ""
    except sqlite3.Error as exc:
        return None, f"cannot read {db_path}: {exc}"


def _tables(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _parse(text: str) -> Optional[datetime]:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _streak_from_runs(reads: list) -> tuple:
    """(zero, error) read straight off the ledger, newest first.

    Used when `source_state` does not exist yet (an un-migrated archive) or the
    source has never been written to since the migration. It cannot recover
    `last_ok_utc`, but it can still tell a barren source from a healthy one --
    which is the question the command exists to answer.
    """
    zero = 0
    for run in reads:
        if int(run.get("collected") or 0) > 0 or int(run.get("errors") or 0):
            break
        if (run.get("status") or "") == "limited":
            continue
        zero += 1
    newest = reads[0] if reads else {}
    error = 1 if (int(newest.get("errors") or 0)
                  and (newest.get("status") or "") != "limited") else 0
    return zero, error


def _status(last_read_utc: str, zero: int, error: int, now: datetime) -> str:
    when = _parse(last_read_utc)
    if when is None or (now - when) > timedelta(hours=STALE_AFTER_HOURS):
        return STALE
    if error > 0:
        return ERROR
    if zero >= BARREN_AFTER_ZERO_VISITS:
        return BARREN
    if zero > 0:
        return QUIET
    return HEALTHY


def _stored_counts(conn, tables: set, window_runs: list) -> dict:
    """How many records each run wrote, keyed by source.

    `collected` is the collector's own count; this is what actually landed in
    `problem_signal`. Reading both is how a report cannot quote a figure the
    archive does not hold.
    """
    if SIGNAL_TABLE not in tables:
        return {}
    ids = [r["run_id"] for r in window_runs if r.get("run_id")]
    counts = {}
    for i in range(0, len(ids), 500):   # stay under SQLite's variable limit
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for run_id, n in conn.execute(
                f"SELECT run_id, COUNT(*) FROM {SIGNAL_TABLE} "
                f"WHERE run_id IN ({marks}) GROUP BY run_id", chunk).fetchall():
            counts[run_id] = n
    by_source = {}
    for run in window_runs:
        if run.get("run_id") in counts:
            by_source[run["source"]] = by_source.get(run["source"], 0) + counts[run["run_id"]]
    return by_source


def _gather(conn: sqlite3.Connection, out: dict, since: datetime,
            now: datetime) -> None:
    tables = _tables(conn)
    have_state = SOURCE_STATE_TABLE in tables
    if not have_state:
        out["warnings"].append(
            f"{SOURCE_STATE_TABLE} is missing (the signals migration has not run "
            "here); streaks are read from the run ledger instead")
    if RUN_TABLE not in tables:
        out["warnings"].append(
            f"{RUN_TABLE} is missing; no collector run has ever been recorded")
        return

    rows = [dict(r) for r in conn.execute(
        f"SELECT run_id, source, started_utc, finished_utc, collected, new, errors, "
        f"status FROM {RUN_TABLE} ORDER BY started_utc DESC").fetchall()]
    since_text = _iso(since)
    in_window = [r for r in rows if (r["started_utc"] or "") >= since_text]
    out["runs"] = len(in_window)

    states = {}
    if have_state:
        states = {r["source"]: dict(r) for r in conn.execute(
            f"SELECT * FROM {SOURCE_STATE_TABLE}").fetchall()}
    stored = _stored_counts(conn, tables, in_window)

    for name in sorted({r["source"] for r in rows if r["source"]} | set(states)):
        mine = [r for r in rows if r["source"] == name]
        reads = [r for r in mine if (r["status"] or "") != "skipped"]
        window = [r for r in in_window
                  if r["source"] == name and (r["status"] or "") != "skipped"]
        state = states.get(name)
        if state is not None:
            zero = int(state.get("consecutive_zero") or 0)
            error = int(state.get("consecutive_error") or 0)
            last_ok = state.get("last_ok_utc") or ""
            last_error = state.get("last_error") or ""
        else:
            zero, error = _streak_from_runs(reads)
            last_ok, last_error = "", ""
        last_read = reads[0]["started_utc"] if reads else ""
        out["sources"].append({
            "source": name,
            "last_run_utc": mine[0]["started_utc"] if mine else "",
            "last_read_utc": last_read,
            "rows": sum(int(r.get("collected") or 0) for r in window),
            "new": sum(int(r.get("new") or 0) for r in window),
            "stored": stored.get(name, 0),
            "consecutive_zero": zero,
            "consecutive_error": error,
            "last_ok_utc": last_ok,
            "last_error": last_error,
            "last_run_status": (mine[0].get("status") or "") if mine else "",
            "status": _status(last_read, zero, error, now),
        })


def _decide(out: dict) -> None:
    statuses = [s["status"] for s in out["sources"]]
    failing = [s["source"] for s in out["sources"] if s["status"] in FAILING]
    if not out["runs"]:
        # Silence for a whole window is the failure this command exists for.
        out["verdict"] = "barren"
        out["warnings"].append(f"no runs in the last {out['days']} day(s)")
    elif BARREN in statuses:
        out["verdict"] = "barren"
    elif STALE in statuses or ERROR in statuses:
        out["verdict"] = "degraded"
    elif QUIET in statuses:
        out["verdict"] = "quiet"
    else:
        out["verdict"] = "healthy"
    out["exit_code"] = 1 if (failing or not out["runs"]) else 0


def report(db_path, *, days: int = 7, now: Optional[datetime] = None) -> dict:
    """The whole answer: per-source status, an overall verdict, an exit code.

    `now` is injectable so the window and the stale threshold are testable
    without waiting 36 hours.
    """
    now = now or datetime.now(timezone.utc)
    days = max(1, int(days))
    out = {
        "db": str(db_path),
        "generated_at": _iso(now),
        "days": days,
        "window": {"from": _iso(now - timedelta(days=days)), "to": _iso(now)},
        "runs": 0,
        "sources": [],
        "warnings": [],
        "verdict": "barren",
        "exit_code": 1,
    }
    conn, problem = _connect_read_only(db_path)
    if conn is None:
        out["warnings"].append(problem)
        out["warnings"].append(f"no runs in the last {days} day(s)")
        return out
    try:
        _gather(conn, out, now - timedelta(days=days), now)
    except sqlite3.Error as exc:
        # A locked or truncated archive is a finding, not a traceback: the
        # command's whole job is to say what is and is not readable.
        out["warnings"].append(f"cannot read {db_path}: {exc}")
    finally:
        conn.close()
    _decide(out)
    return out
