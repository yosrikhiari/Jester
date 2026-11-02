"""Is each source alive, or only quiet?

`jester signals runs` answers "what happened", one row at a time. It cannot
answer the question an operator asks in the morning: is this source quiet this
week, or dead? A source returning zero rows and a source that stopped running
both print "collected 0", and the run's own status is `ok` in both cases --
zero results is a valid run by design, which is exactly what makes the two look
alike.

So every run leaves a per-source streak in `signals_source_state`, and this module
turns the streaks and the run ledger into one verdict with an exit code:

  HEALTHY  the last read yielded rows
  QUIET    1-2 consecutive zero-row runs
  BARREN   3 or more consecutive zero-row runs
  STALE    no read at all inside the source's own expected interval
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
* **STALE is cadence-aware.** The brief scopes it to "a daily source", and the
  ledger is the only honest witness of a source's cadence: `signals run` walks
  a fallback chain and stops at the first board that answered, so `himalayas`,
  `remotive` and `jobicy` only gain a row on days the primary found nothing,
  and the sweep runs weekly. Judging every source against 36 h called a
  5-day-old himalayas row a dead source and fired the morning notification on
  a day with no outage at all.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SOURCE_STATE_TABLE = "signals_source_state"
#: What this table was called before the signals archive got its own file. The
#: Go worker owns a `source_state` of its own in the nuggets archive (keyed by
#: `name`, with visit counters); the two shapes shared a name and no columns,
#: so one archive could not serve both. Read-only reference: the legacy table is
#: never dropped or altered.
LEGACY_SOURCE_STATE_TABLE = "source_state"
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

#: A source that has not been read in 36 hours has missed its day. The FLOOR
#: of the stale threshold: longer than 24 so a late tick is not a finding;
#: shorter than 48 so a whole missed day is. A source whose ledger shows a
#: slower rhythm than daily gets its own, longer threshold -- see
#: `_stale_after_hours`.
STALE_AFTER_HOURS = 36

#: Grace on top of a source's own observed interval: one period plus half a
#: period. Applied to a daily source it reproduces `STALE_AFTER_HOURS` exactly
#: (24 h x 1.5 = 36 h), so the brief's constant is still the daily answer.
STALE_CADENCE_FACTOR = 1.5

#: How many of a source's most recent intervals count as its current rhythm.
#: Enough for a weekly sweep to have a handful of samples; small enough that an
#: outage a year ago does not set the bar for good.
CADENCE_SAMPLES = 12

#: The bound applied when the ledger cannot show a rhythm (fewer than two
#: reads): a week plus the same half-period grace as an observed weekly
#: interval. Without it a source that was read once and never again stayed
#: HEALTHY forever, and "no read in 12 days" produced no morning verdict.
UNOBSERVED_CADENCE_HOURS = 7 * 24

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


#: The streak table's columns, in DDL order -- also the order the legacy rows
#: are copied in, so the copy names them rather than trusting `SELECT *`.
STATE_COLUMNS = ("source", "consecutive_zero", "consecutive_error",
                 "last_ok_utc", "last_rows", "last_error", "updated_utc")


def _columns_of(db: sqlite3.Connection, table: str) -> set:
    """The table's column names, or an empty set when it does not exist."""
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _adopt_legacy_streaks(db: sqlite3.Connection) -> int:
    """Move rows out of a legacy Python-shaped `source_state`, once.

    Additive on purpose: `INSERT OR IGNORE` keyed on `source` means a second
    call copies nothing, and the old table keeps every row it had. Dropping it
    to rename it would be a destructive act on live state performed by a
    migration nobody asked for.

    A `source_state` without `consecutive_zero` is the GO table -- same name, a
    different question (visit counters for the nuggets archive). It is left
    entirely alone: copying its integers into a streak column would invent a
    verdict out of a number that means something else.
    """
    columns = _columns_of(db, LEGACY_SOURCE_STATE_TABLE)
    if "consecutive_zero" not in columns:
        return 0
    names = ", ".join(STATE_COLUMNS)
    moved = db.execute(
        f"INSERT OR IGNORE INTO {SOURCE_STATE_TABLE} ({names}) "
        f"SELECT {names} FROM {LEGACY_SOURCE_STATE_TABLE}")
    return moved.rowcount


def ensure_source_state_table(db: sqlite3.Connection) -> int:
    """The additive migration. Called by the WRITE path only (`run.collect`),
    never by `report`, so a diagnostic cannot reshape what it diagnoses.

    Returns how many legacy rows were adopted; 0 when there was nothing to take.
    """
    db.executescript(SOURCE_STATE_DDL)
    moved = _adopt_legacy_streaks(db)
    db.commit()
    return moved


def _have_state_table(db: sqlite3.Connection) -> bool:
    """Is the streak table here? One indexed SELECT against the schema.

    The DDL used to run at the top of every source run: `executescript` is an
    implicit COMMIT, so a run put DDL and a commit in the middle of its write
    phase for a table that almost always already existed. The lookup is
    cheaper than the state read that follows it, and the DDL still lives on
    the write path -- `report` never calls any of this. (A per-connection memo
    is not available: `sqlite3.Connection` cannot hold attributes and cannot
    be weakly referenced. One schema lookup per source run is the honest
    price.)
    """
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (SOURCE_STATE_TABLE,)).fetchone() is not None


def _state(db: sqlite3.Connection, source: str) -> dict:
    if not _have_state_table(db):
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

    A `limited` run is read by what it got: with zero rows it moves neither
    streak (the source asked us to stop, which is not a verdict on whether it
    has anything to say), and with rows it is a read like any other -- a
    source that is permanently rate-limited but still returns rows before it
    is cut off must not stay invisible. It is always recorded as the last
    thing that happened to the source.
    """
    state = _state(db, source)
    status = str(row.get("status") or "")
    errors = int(row.get("errors") or 0)
    collected = int(row.get("collected") or 0)
    stamp = at or row.get("finished_utc") or _iso(datetime.now(timezone.utc))

    if status == "limited":
        if collected > 0:
            state["consecutive_zero"] = 0
            state["consecutive_error"] = 0
            state["last_error"] = ""
            state["last_ok_utc"] = stamp
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

    Used when the streak table does not exist yet (an un-migrated archive) or the
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


def _observed_interval_hours(reads: list) -> Optional[float]:
    """The longest silence this source has recently been through, in hours.

    STALE asks "has this source stopped doing what it normally does?", and the
    ledger is the only honest witness of what a source's rhythm is: `signals
    run` stops the fallback chain at the first board that answered, so a
    fallback (himalayas, remotive, jobicy) gains a row only on the days the
    primary found nothing, and the sweep runs weekly. Judging every source
    against 36 h called a 5-day-old himalayas row a dead source and fired the
    morning notification on a day with no outage at all.

    The LONGEST of the recent gaps, not the mean or the median. A fallback's
    cadence is bimodal -- asked daily in some weeks, weekly in others -- and
    any average sits between the two and so alarms on the source's own normal
    silence. The longest gap is the length of silence the source has already
    been through without being dead, which is exactly the bar a silence has to
    clear before it is news.

    `None` when the ledger holds fewer than two reads: one run is not a
    rhythm, and the only interval available then is the distance from that run
    to now, which says nothing about the source's cadence.
    """
    stamps = sorted(s for s in (_parse(r.get("started_utc")) for r in reads)
                    if s is not None)
    if len(stamps) < 2:
        return None
    gaps = [(b - a).total_seconds() / 3600.0
            for a, b in zip(stamps, stamps[1:])]
    gaps = [g for g in gaps if g > 0][-CADENCE_SAMPLES:]
    return max(gaps) if gaps else None


def _stale_after_hours(interval: Optional[float]) -> Optional[float]:
    """How long this source may stay silent.

    One observed period plus half a period of grace, floored at the daily
    answer so a source that runs several times a day does not get a sub-hour
    threshold: daily -> max(36 h, 24 x 1.5) = 36 h, weekly -> 7 d x 1.5 = 10.5 d.

    An unmeasurable interval (fewer than two reads) is judged against the
    slowest cadence the system knows, the weekly sweep:
    `UNOBSERVED_CADENCE_HOURS` x 1.5 = 252 h.
    """
    if interval is None:
        return UNOBSERVED_CADENCE_HOURS * STALE_CADENCE_FACTOR
    return max(STALE_AFTER_HOURS, interval * STALE_CADENCE_FACTOR)


def _status(last_read_utc: str, zero: int, error: int, now: datetime,
            stale_after: Optional[float] = STALE_AFTER_HOURS) -> str:
    when = _parse(last_read_utc)
    if when is None:
        # Never read at all: nothing has to be inferred to say so.
        return STALE
    if stale_after is not None and (now - when) > timedelta(hours=stale_after):
        return STALE
    if error > 0:
        return ERROR
    if zero >= BARREN_AFTER_ZERO_VISITS:
        return BARREN
    if zero > 0:
        return QUIET
    return HEALTHY


def _force_failure(out: dict, message: str) -> None:
    """Record a read that could not be finished, and make it the verdict.

    Whatever `_gather` assembled before the raise is a fragment: a `runs`
    count with a default, empty `sources` list reads exactly like a healthy
    archive. A verdict is a certification, so a read that did not finish may
    not issue one -- it exits 1 whatever the fragment said.
    """
    out["error"] = message
    out["warnings"].append(message)


def _stored_counts(conn, tables: set, window_runs: list, out: dict) -> dict:
    """How many records each run wrote, keyed by source.

    `collected` is the collector's own count; this is what actually landed in
    `problem_signal`. Reading both is how a report cannot quote a figure the
    archive does not hold.

    A `problem_signal` that predates the `run_id` column cannot answer, and
    the answer is "unknown", not a crash and not a zero: the per-source
    streaks above are still worth printing, and the verdict says the count
    could not be read.
    """
    if SIGNAL_TABLE not in tables:
        return {}
    ids = [r["run_id"] for r in window_runs if r.get("run_id")]
    counts = {}
    for i in range(0, len(ids), 500):   # stay under SQLite's variable limit
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"SELECT run_id, COUNT(*) FROM {SIGNAL_TABLE} "
                f"WHERE run_id IN ({marks}) GROUP BY run_id", chunk).fetchall()
        except sqlite3.Error as exc:
            _force_failure(
                out, f"cannot count records in {SIGNAL_TABLE} by run: {exc}")
            return {}
        for run_id, n in rows:
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
    stored = _stored_counts(conn, tables, in_window, out)

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
        # Judged against this source's own rhythm, not the primary's: a
        # fallback that gains a row twice a week is not a dead daily source.
        stale_after = _stale_after_hours(_observed_interval_hours(reads))
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
            "stale_after_hours": stale_after,
            "status": _status(last_read, zero, error, now,
                              stale_after=stale_after),
        })


def _decide(out: dict) -> None:
    if out.get("error"):
        # A read that did not finish cannot certify a healthy archive, however
        # much of the fragment happened to look fine.
        out["verdict"] = "degraded"
        out["exit_code"] = 1
        return
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
        # Non-empty when the read itself failed: it forces the verdict to a
        # failure, so a half-read archive can never print `healthy`.
        "error": "",
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
        # A locked or truncated archive is a finding, not a traceback -- and
        # not a green line either: whatever was gathered before the raise is a
        # fragment (a run count beside a default, empty source list reads as
        # `healthy`), so the failure is recorded and the verdict is forced.
        _force_failure(out, f"cannot read {db_path}: {exc}")
    finally:
        conn.close()
    _decide(out)
    return out
