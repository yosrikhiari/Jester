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
    """Asked to stop is not a verdict on the source: the streaks do not move."""
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


def test_a_source_that_has_not_run_in_36_hours_is_stale(tmp_path):
    path = str(tmp_path / "signals.db")
    db = open_signals(path)
    now = datetime.now(timezone.utc)
    at = now - timedelta(hours=48)
    try:
        row = _write_run(db, "tests-stale", at=at, collected=2)
        sighealth.record_source_state(db, "tests-stale", row, at=_iso(at))
    finally:
        db.close()

    res = sighealth.report(path, days=7)
    stale = next(s for s in res["sources"] if s["source"] == "tests-stale")
    assert stale["status"] == sighealth.STALE
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
