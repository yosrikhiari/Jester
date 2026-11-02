"""`jester health`: a dead source must not read like a quiet one.

`signals runs` lists what happened, one row at a time, and a zero-row run is a
valid run by design — so "collected 0" says nothing about whether the source
answered. Three zero-row runs are a barren source, not a happy week; no run at
all is a failure, not a green line. These drive the streak writer, the module
and the CLI exit code over fixture databases only.

Nothing here opens the real archive, and the read path is asserted to be
read-only: a diagnostic must not migrate what it diagnoses.
"""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from jester import cli
from jester.signals import health as sighealth
from jester.signals import run as sigrun
from jester.signals import open_signals, Signal
from jester.signals.sources import SourceError


def _iso(when):
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row(source, *, at, collected=0, errors=0, status="ok", run_id=None, new=0):
    stamp = _iso(at) if isinstance(at, datetime) else at
    return {
        "run_id": run_id or f"r-{source}-{stamp}-{collected}-{errors}-{status}",
        "source": source, "mode": "live", "kind": "scheduled",
        "started_utc": stamp, "finished_utc": stamp, "since": "7d",
        "queries": "[]", "collected": collected, "new": new, "seen_again": 0,
        "edited": 0, "relevant": 0, "buyer": 0, "practitioner": 0,
        "errors": errors, "status": status, "note": "",
    }


def _write_run(db, source, **kw):
    row = _row(source, **kw)
    sigrun.ensure_run_table(db)
    sigrun._save_run(db, row)
    return row


def _state(db, source):
    row = db.execute("SELECT * FROM source_state WHERE source = ?", (source,)).fetchone()
    return dict(row) if row is not None else None


class _Source:
    """A collector that returns canned rows, or raises the canned error."""

    platform = "hackernews"
    reads = {}
    requests = 0

    def __init__(self, name, signals=(), error=None):
        self.name = name
        self._signals = list(signals)
        self._error = error

    def search(self, query, *, since_utc="", limit=100):
        if self._error is not None:
            raise self._error
        return iter(self._signals)


def _signal(n):
    return Signal(source_id=f"health-{n}", platform="hackernews",
                  community="hn/comment", title="We re-key every order by hand",
                  text="Two of us spend six hours a week copying order details.")


# --- 3a: the streak the collector leaves behind ------------------------------

def test_a_zero_row_run_starts_a_quiet_streak():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    at = "2026-11-08T09:00:00+00:00"
    sighealth.record_source_state(db, "hn", _row("hn", at=at), at=at)

    state = _state(db, "hn")
    assert state["consecutive_zero"] == 1
    assert state["consecutive_error"] == 0
    assert state["last_ok_utc"] == ""
    assert state["last_rows"] == 0


def test_rows_reset_the_streak_and_record_the_last_ok():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    for i in range(2):
        at = f"2026-11-0{i + 1}T09:00:00+00:00"
        sighealth.record_source_state(db, "hn", _row("hn", at=at), at=at)
    at = "2026-11-03T09:00:00+00:00"
    sighealth.record_source_state(db, "hn",
                                  _row("hn", at=at, collected=4, new=2), at=at)

    state = _state(db, "hn")
    assert state["consecutive_zero"] == 0
    assert state["consecutive_error"] == 0
    assert state["last_ok_utc"] == at
    assert state["last_rows"] == 4


def test_a_raised_query_counts_as_an_error_and_clears_the_quiet_streak():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    sighealth.record_source_state(
        db, "hn", _row("hn", at="2026-11-01T09:00:00+00:00"),
        at="2026-11-01T09:00:00+00:00")
    at = "2026-11-02T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=at, errors=1, status="failed", run_id="r-fail"), at=at)

    state = _state(db, "hn")
    assert state["consecutive_error"] == 1
    assert state["consecutive_zero"] == 0
    assert state["last_error"]


def test_a_partial_run_is_an_error_even_though_rows_landed():
    """`status="partial"` means a query raised and rows still came back. The
    source is not dead, but a query of it is, and `ERROR` is defined as "the
    last run raised" -- so the streak says error, not healthy, and one clean
    run is all it takes to clear it."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    at = "2026-11-01T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=at, collected=6, errors=1, status="partial"), at=at)

    state = _state(db, "hn")
    assert state["consecutive_error"] == 1
    assert state["consecutive_zero"] == 0
    assert state["last_rows"] == 6
    assert state["last_ok_utc"] == "", "a run with a failed query is not the last ok"

    clean = "2026-11-02T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=clean, collected=6), at=clean)
    state = _state(db, "hn")
    assert state["consecutive_error"] == 0
    assert state["last_ok_utc"] == clean


def test_a_rate_limited_run_is_not_a_quiet_run():
    """Asked to stop with nothing to show is not a verdict on the source: the
    streaks do not move."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    at = "2026-11-01T09:00:00+00:00"
    sighealth.record_source_state(db, "hn", _row("hn", at=at), at=at)
    later = "2026-11-02T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=later, errors=3, status="limited"), at=later)

    state = _state(db, "hn")
    assert state["consecutive_zero"] == 1, "the limit did not make it quieter"
    assert state["consecutive_error"] == 0


def test_a_rate_limited_run_that_yielded_rows_is_a_read():
    """The other half: a source that is permanently rate-limited still returns
    rows before it is cut off. Those rows are a read, so both streaks reset and
    `last_ok_utc` moves -- otherwise such a source stays invisible forever."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    at = "2026-11-01T09:00:00+00:00"
    sighealth.record_source_state(db, "hn", _row("hn", at=at), at=at)
    at2 = "2026-11-02T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=at2, errors=1, status="failed", run_id="r-fail"), at=at2)
    assert _state(db, "hn")["consecutive_error"] == 1
    assert _state(db, "hn")["consecutive_zero"] == 0

    at3 = "2026-11-03T09:00:00+00:00"
    sighealth.record_source_state(
        db, "hn", _row("hn", at=at3, collected=2, status="limited"), at=at3)

    state = _state(db, "hn")
    assert state["consecutive_zero"] == 0
    assert state["consecutive_error"] == 0
    assert state["last_ok_utc"] == at3
    assert state["last_rows"] == 2


def test_the_migration_runs_once_not_on_every_source_run(monkeypatch):
    """`executescript` is an implicit COMMIT. Running it at the top of every
    source run put DDL and a commit in the middle of a run's write phase; the
    table is looked up in `sqlite_master` and created only when absent."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    created = []
    real = sighealth.ensure_source_state_table

    def counting(conn):
        created.append(conn)
        return real(conn)

    monkeypatch.setattr(sighealth, "ensure_source_state_table", counting)
    for i in range(3):
        at = f"2026-11-0{i + 1}T09:00:00+00:00"
        sighealth.record_source_state(db, f"s{i}", _row(f"s{i}", at=at), at=at)

    assert len(created) == 1, f"the migration ran {len(created)} times"


def test_collect_records_the_streak_at_the_end_of_a_run(tmp_path):
    """The wiring, not just the writer: every source run leaves a state row."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        src = _Source("tests-quiet")
        for i in range(3):
            sigrun.collect(db, source_name=src.name, queries=["q"], source=src,
                           run_id=f"quiet-{i}")
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    quiet = next(s for s in res["sources"] if s["source"] == "tests-quiet")
    assert quiet["consecutive_zero"] == 3
    assert quiet["status"] == sighealth.BARREN


def test_collect_records_an_error_and_a_healthy_run(tmp_path):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        broken = _Source("tests-broken",
                         error=SourceError("boom", query="q", status="500"))
        sigrun.collect(db, source_name=broken.name, queries=["q"], source=broken,
                       run_id="broken-1")
        alive = _Source("tests-alive", signals=[_signal(1)])
        sigrun.collect(db, source_name=alive.name, queries=["q"], source=alive,
                       run_id="alive-1")
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    by = {s["source"]: s for s in res["sources"]}
    assert by["tests-broken"]["status"] == sighealth.ERROR
    assert by["tests-broken"]["consecutive_error"] == 1
    assert by["tests-alive"]["status"] == sighealth.HEALTHY
    assert by["tests-alive"]["rows"] == 1


# --- 3b: the verdict and the exit code ---------------------------------------

def test_three_zero_yield_runs_are_barren_and_exit_1(tmp_path, capsys):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        src = _Source("tests-quiet")
        for i in range(3):
            sigrun.collect(db, source_name=src.name, queries=["q"], source=src,
                           run_id=f"quiet-{i}")
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    assert res["verdict"] == "barren"
    assert res["exit_code"] == 1

    with pytest.raises(SystemExit) as exc:
        cli.main(["health", "--db", path, "--days", "7"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "BARREN" in out and "verdict: barren" in out


def test_a_healthy_week_exits_0(tmp_path, capsys):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    try:
        for day in range(5):
            at = now - timedelta(days=day)
            row = _write_run(db, "tests-alive", at=at, collected=4, new=1,
                             run_id=f"alive-{day}")
            sighealth.record_source_state(db, "tests-alive", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    assert res["runs"] == 5
    assert [s["status"] for s in res["sources"]] == [sighealth.HEALTHY]
    assert res["verdict"] == "healthy"
    assert res["exit_code"] == 0

    cli.main(["health", "--db", path, "--days", "7"])
    assert "verdict: healthy" in capsys.readouterr().out


def test_an_empty_database_is_a_failure_not_a_green_line(tmp_path, capsys):
    path = tmp_path / "empty.db"
    sqlite3.connect(str(path)).close()
    assert path.stat().st_size == 0

    res = sighealth.report(str(path), days=7)
    assert res["runs"] == 0
    assert res["verdict"] == "barren"
    assert res["exit_code"] == 1
    assert any("no runs in the last 7 day(s)" in w for w in res["warnings"])

    with pytest.raises(SystemExit) as exc:
        cli.main(["health", "--db", str(path), "--days", "7"])
    assert exc.value.code == 1
    assert "no runs in the last 7 day(s)" in capsys.readouterr().out


def test_rows_exist_but_none_in_the_window_still_prints_a_row(tmp_path, capsys):
    """The per-source table was gated on `runs`, so an archive whose only run
    predates the window printed the verdict and no source at all -- the one
    case where the operator most needs to see which source went quiet."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    at = datetime.now(timezone.utc) - timedelta(days=10)
    try:
        row = _write_run(db, "tests-old", at=at, collected=2, run_id="old-1")
        sighealth.record_source_state(db, "tests-old", row, at=_iso(at))
    finally:
        db.close()

    with pytest.raises(SystemExit) as exc:
        cli.main(["health", "--db", path, "--days", "7"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "tests-old" in out, out
    assert "no runs in the last 7 day(s)" in out


def test_a_health_read_neither_creates_nor_migrates(tmp_path):
    """`signals` opens by running its DDL. A diagnostic must not: the archive it
    reports on has to be the archive it leaves behind."""
    path = tmp_path / "empty.db"
    sqlite3.connect(str(path)).close()

    sighealth.report(str(path), days=7)

    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in ro.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
    finally:
        ro.close()
    assert tables == [], f"a read created {tables}"
    assert path.stat().st_size == 0, "a read of an empty archive wrote to it"


def test_a_missing_database_is_reported_and_not_created(tmp_path):
    path = tmp_path / "absent.db"

    res = sighealth.report(str(path), days=7)

    assert res["exit_code"] == 1
    assert any("no database" in w for w in res["warnings"])
    assert not path.exists(), "a diagnostic brought an archive into existence"


def test_a_read_error_never_prints_green(tmp_path, capsys):
    """Measured on an archive whose `problem_signal` predates the `run_id`
    column: the ledger count had already landed, the exception left a default
    `sources` list, and `_decide` saw `runs > 0` -- so a read that failed
    halfway printed `healthy` and exited 0 with a warning. A verdict is a
    certification; a read that did not finish cannot issue one."""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    _write_run(db, "tests-alive", at=now, collected=3, run_id="alive-1")
    # The pre-`run_id` shape of the record table: counting by run cannot work.
    db.execute("CREATE TABLE problem_signal (id TEXT PRIMARY KEY, source TEXT)")
    db.commit()
    db.close()

    res = sighealth.report(str(path), days=7)

    assert res["verdict"] != "healthy", res
    assert res["exit_code"] == 1
    assert res["error"], "the report must carry the failure it could not survive"
    assert any("run_id" in w for w in res["warnings"]), res["warnings"]

    with pytest.raises(SystemExit) as exc:
        cli.main(["health", "--db", str(path), "--days", "7"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "verdict: healthy" not in out
    assert "cannot read" in out or "cannot count" in out


def test_a_locked_read_is_a_failure_not_a_traceback(tmp_path, monkeypatch):
    """A lock taken mid-read raises after the ledger count. Same rule: whatever
    was gathered before the raise is a fragment, not an answer."""
    path = tmp_path / "signals.db"
    sqlite3.connect(str(path)).close()

    def locked(conn, out, since, now):
        out["runs"] = 3          # the count landed; the per-source rows did not
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sighealth, "_gather", locked)
    res = sighealth.report(str(path), days=7)

    assert res["verdict"] != "healthy"
    assert res["exit_code"] == 1
    assert any("database is locked" in w for w in res["warnings"])


def test_a_source_that_has_not_run_in_36_hours_is_stale(tmp_path):
    """A daily source with a day of history that then missed a day. The ledger
    is what says "daily": three reads a day apart, the newest 48 h old."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    try:
        for day in range(2, 5):
            at = now - timedelta(hours=24 * day)
            row = _write_run(db, "tests-stale", at=at, collected=2,
                             run_id=f"stale-{day}")
            sighealth.record_source_state(db, "tests-stale", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    stale = next(s for s in res["sources"] if s["source"] == "tests-stale")
    assert stale["status"] == sighealth.STALE
    assert res["verdict"] == "degraded"
    assert res["exit_code"] == 1


def test_a_weekly_fallback_source_is_not_stale_beside_a_daily_one(tmp_path):
    """The measured false alarm: `signals run` stops at the first board that
    answered, so `himalayas` only gains a row on the days the primary found
    nothing, and the whole sweep runs weekly. Judging every source against a
    daily 36 h called a 5-day-old himalayas row STALE, degraded the morning
    verdict, and notified the operator every day there was no outage."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    try:
        for day in range(6):
            at = now - timedelta(days=day)
            row = _write_run(db, "hackernews", at=at, collected=5, new=1,
                             run_id=f"hn-{day}")
            sighealth.record_source_state(db, "hackernews", row, at=_iso(at))
        for week in range(4):
            at = now - timedelta(days=5 + 7 * week)
            row = _write_run(db, "himalayas", at=at, collected=2,
                             run_id=f"him-{week}")
            sighealth.record_source_state(db, "himalayas", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    weekly = next(s for s in res["sources"] if s["source"] == "himalayas")
    assert weekly["status"] == sighealth.HEALTHY, weekly
    assert res["verdict"] == "healthy"
    assert res["exit_code"] == 0
    # No SystemExit: the scheduled task would exit 1 and notify otherwise.
    cli.main(["health", "--db", path, "--days", "7"])


def test_a_fallback_with_a_bimodal_cadence_is_judged_by_its_longest_gap(tmp_path):
    """A fallback board is asked on the days the primary found nothing: daily
    for a stretch, then weekly for another. Any average of those gaps lands
    between the two rhythms and so alarms on the source's own normal silence;
    the longest recent silence is the bar a silence has to clear to be news."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    try:
        for day in range(3):
            at = now - timedelta(days=day)
            row = _write_run(db, "hackernews", at=at, collected=5, run_id=f"hn-{day}")
            sighealth.record_source_state(db, "hackernews", row, at=_iso(at))
        # daily for a week, then weekly: the last read five days ago is inside
        # the seven-day rhythm it has already been through.
        for day in (5, 6, 7, 8, 9, 10, 11, 18, 25):
            at = now - timedelta(days=day)
            row = _write_run(db, "himalayas", at=at, collected=2, run_id=f"him-{day}")
            sighealth.record_source_state(db, "himalayas", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    weekly = next(s for s in res["sources"] if s["source"] == "himalayas")
    assert weekly["stale_after_hours"] == 7 * 24 * sighealth.STALE_CADENCE_FACTOR
    assert weekly["status"] == sighealth.HEALTHY
    assert res["exit_code"] == 0


def test_a_source_with_one_read_is_judged_against_the_weekly_bound(tmp_path):
    """One run cannot show a rhythm, so the fallback is the slowest rhythm the
    system knows -- the weekly sweep -- not no rhythm at all. A lone read 60
    hours old is inside that bound, so the source is still HEALTHY."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    at = datetime.now(timezone.utc) - timedelta(hours=60)
    try:
        row = _write_run(db, "tests-new", at=at, collected=2, run_id="new-1")
        sighealth.record_source_state(db, "tests-new", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-new")
    assert row["status"] == sighealth.HEALTHY
    assert row["stale_after_hours"] == (
        sighealth.UNOBSERVED_CADENCE_HOURS * sighealth.STALE_CADENCE_FACTOR)
    assert res["exit_code"] == 0


def test_a_source_read_once_and_silent_for_twelve_days_is_stale(tmp_path):
    """One read is not a rhythm, but it is not immunity from the clock either.
    A source read once and never again must go STALE once it passes the weekly
    bound, or "no read in 12 days" produces no morning verdict. The read is
    outside the report window on purpose: the per-source read list is not
    windowed, so the verdict must still see it.

    A second source is read today, so the report has a run inside its window
    and the non-zero exit cannot come from "no collector run at all" -- it is
    the forgotten source's STALE that makes the morning verdict fail."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    at = datetime.now(timezone.utc) - timedelta(days=12)
    fresh = datetime.now(timezone.utc)
    try:
        row = _write_run(db, "tests-forgotten", at=at, collected=2, run_id="forgotten-1")
        sighealth.record_source_state(db, "tests-forgotten", row, at=_iso(at))
        row = _write_run(db, "tests-live", at=fresh, collected=3, run_id="live-1")
        sighealth.record_source_state(db, "tests-live", row, at=_iso(fresh))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-forgotten")
    assert res["runs"] == 1
    assert row["status"] == sighealth.STALE
    live = next(s for s in res["sources"] if s["source"] == "tests-live")
    assert live["status"] == sighealth.HEALTHY
    assert res["exit_code"] == 1


def test_a_weekly_source_that_missed_its_own_week_is_stale(tmp_path):
    """Cadence-awareness must not turn STALE off for good. A weekly source
    whose last read is nearly three weeks old has missed two cycles, whether
    or not any of its runs lands inside the report window."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    try:
        for day in range(1, 4):
            at = now - timedelta(days=day)
            row = _write_run(db, "hackernews", at=at, collected=5, run_id=f"hn-{day}")
            sighealth.record_source_state(db, "hackernews", row, at=_iso(at))
        for week in range(4):
            at = now - timedelta(days=20 + 7 * week)
            row = _write_run(db, "himalayas", at=at, collected=2,
                             run_id=f"him-{week}")
            sighealth.record_source_state(db, "himalayas", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    weekly = next(s for s in res["sources"] if s["source"] == "himalayas")
    assert weekly["rows"] == 0, "no himalayas run is inside the window"
    assert weekly["status"] == sighealth.STALE
    assert res["verdict"] == "degraded"
    assert res["exit_code"] == 1


def test_a_skipped_run_is_not_a_read(tmp_path):
    """The scheduler fired and the source sat out its cool-down. Nothing was
    asked, so nothing about the source was learned."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        _write_run(db, "tests-sitting-out", at=datetime.now(timezone.utc),
                   status="skipped")
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-sitting-out")
    assert row["status"] == sighealth.STALE
    assert res["exit_code"] == 1


def test_the_last_run_raised_is_an_error(tmp_path):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        row = _write_run(db, "tests-broken", at=datetime.now(timezone.utc),
                         errors=1, status="failed", run_id="broken-1")
        sighealth.record_source_state(db, "tests-broken", row, at=row["finished_utc"])
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-broken")
    assert row["status"] == sighealth.ERROR
    assert res["verdict"] == "degraded"
    assert res["exit_code"] == 1


def test_a_run_that_wrote_records_reports_rows_and_stored(tmp_path):
    """`stored` is counted out of `problem_signal`, so it can disagree with the
    collector's own `collected`. A run that wrote records must show both as
    non-zero -- a key that is always 0 is not a measurement."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        src = _Source("tests-alive", signals=[_signal(1), _signal(2)])
        sigrun.collect(db, source_name=src.name, queries=["q"], source=src,
                       run_id="alive-1")
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-alive")
    assert row["rows"] == 2
    assert row["stored"] == 2


def test_json_output_is_the_same_data_machine_readable(tmp_path, capsys):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    try:
        at = datetime.now(timezone.utc)
        row = _write_run(db, "tests-alive", at=at, collected=3)
        sighealth.record_source_state(db, "tests-alive", row, at=_iso(at))
    finally:
        db.close()

    cli.main(["health", "--db", path, "--json"])
    data = json.loads(capsys.readouterr().out)

    assert set(data) >= {"db", "generated_at", "days", "window", "runs",
                         "sources", "verdict", "exit_code", "warnings"}
    assert data["days"] == 7
    source = data["sources"][0]
    assert set(source) >= {"source", "last_run_utc", "last_read_utc", "rows", "new",
                           "stored", "consecutive_zero", "consecutive_error",
                           "last_ok_utc", "last_error", "last_run_status", "status"}
    assert source["source"] == "tests-alive"
    assert source["status"] == "HEALTHY"
    assert data["exit_code"] == 0


def test_the_streak_survives_before_the_migration(tmp_path):
    """An archive whose `source_state` table does not exist yet still reports an
    honest streak -- read from the run ledger -- instead of crashing or
    pretending every source is healthy."""
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    for day in range(3):
        _write_run(db, "tests-quiet", at=now - timedelta(days=day))
    db.close()

    res = sighealth.report(path, days=7)
    row = next(s for s in res["sources"] if s["source"] == "tests-quiet")
    assert row["consecutive_zero"] == 3
    assert row["status"] == sighealth.BARREN
    assert any("source_state" in w for w in res["warnings"])


def test_a_failing_streak_write_does_not_lose_the_run(tmp_path, monkeypatch, capsys):
    """`_save_run` commits the run before the streak is folded in. A bookkeeping
    failure after that commit must not turn a successful collection into a
    raised run: the run happened, and the ledger says so."""
    path = str(tmp_path / "signals.db")
    db = open_signals(path)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("source_state is locked")

    monkeypatch.setattr(sigrun, "record_source_state", broken)
    try:
        src = _Source("tests-alive", signals=[_signal(1)])
        row = sigrun.collect(db, source_name=src.name, queries=["q"], source=src,
                             run_id="alive-1")
        saved = db.execute(
            "SELECT COUNT(*) FROM signal_run WHERE run_id = 'alive-1'").fetchone()[0]
    finally:
        db.close()

    assert row["collected"] == 1
    assert saved == 1, "a bookkeeping failure must not cost the run its row"
    assert "source_state is locked" in capsys.readouterr().err
