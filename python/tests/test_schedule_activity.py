"""The "how often did it actually run" indicator.

A registered schedule that fires and achieves nothing looks exactly like a
healthy one until you put the count next to the cadence — which is the failure
this console shipped for months. These pin the counting, the expectation, and
the honesty of both.
"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jester.cli import _TimestampedOut
from jester.console.api import ConsoleAPI
from jester.store import finish_run, open_db, run_activity, start_run

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def api(tmp_path):
    return ConsoleAPI(db_path=str(tmp_path / "a.db"), config_dir=str(REPO_CONFIG))


def _tick(db, run_id, *, origin="scheduled", minutes_ago=0, nuggets=0, ideas=0,
          status="completed"):
    """Insert a finished run at a chosen age, bypassing datetime('now')."""
    db.execute(
        "INSERT INTO runs(run_id, status, started_at, finished_at, origin, "
        "                 n_comments, n_nuggets_kept, n_ideas) "
        "VALUES(?,?,datetime('now', ?),datetime('now', ?),?,?,?,?)",
        (run_id, status, f"-{minutes_ago} minutes", f"-{minutes_ago} minutes",
         origin, nuggets, nuggets, ideas),
    )
    db.commit()


# ---- counting -------------------------------------------------------------

def test_counts_only_the_requested_origin(tmp_path):
    """A manual run is not evidence that the schedule fired."""
    db = open_db(str(tmp_path / "j.db"))
    _tick(db, "sched-1", origin="scheduled")
    _tick(db, "by-hand", origin="manual")
    act = run_activity(db, origin="scheduled")
    assert act["total"] == 1
    assert act["windows"]["1"]["runs"] == 1


def test_windows_respect_age(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    _tick(db, "now", minutes_ago=5)
    _tick(db, "older", minutes_ago=90)      # outside 1h, inside 24h
    _tick(db, "ancient", minutes_ago=60 * 30)  # outside both
    act = run_activity(db, windows=(1, 24))
    assert act["windows"]["1"]["runs"] == 1
    assert act["windows"]["24"]["runs"] == 2
    assert act["total"] == 3


def test_window_sums_what_was_archived(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    _tick(db, "a", minutes_ago=5, nuggets=10, ideas=2)
    _tick(db, "b", minutes_ago=10, nuggets=7, ideas=1)
    act = run_activity(db, windows=(1,))
    assert act["windows"]["1"]["nuggets"] == 17
    assert act["windows"]["1"]["ideas"] == 3


def test_completed_is_counted_apart_from_started(tmp_path):
    """A tick that aborted still fired; it just did not finish."""
    db = open_db(str(tmp_path / "j.db"))
    _tick(db, "ok", minutes_ago=5)
    _tick(db, "died", minutes_ago=6, status="aborted")
    act = run_activity(db, windows=(1,))
    assert act["windows"]["1"]["runs"] == 2
    assert act["windows"]["1"]["completed"] == 1


def test_last_and_recent_are_newest_first(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    _tick(db, "old", minutes_ago=60)
    _tick(db, "new", minutes_ago=1)
    act = run_activity(db)
    assert act["last"]["run_id"] == "new"
    assert [r["run_id"] for r in act["recent"]] == ["new", "old"]


def test_no_runs_is_empty_not_an_error(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    act = run_activity(db)
    assert act["total"] == 0 and act["last"] is None and act["recent"] == []


# ---- the expectation ------------------------------------------------------

def test_expected_counts_the_tick_at_the_start_of_the_span(api):
    """A 46-minute-old 30-minute schedule should have fired at t=0 and t=30 —
    two ticks. Plain division says one, and reports a healthy job as behind."""
    _tick(api.db, "a", minutes_ago=46)
    _tick(api.db, "b", minutes_ago=16)
    act = api._schedule_activity(30)
    assert act["windows"]["1"]["expected"] == 2
    assert act["windows"]["1"]["runs"] == 2


def test_expectation_is_capped_by_how_long_the_schedule_has_existed(api):
    """A job installed ten minutes ago has not missed 47 of its 48 daily ticks.
    An always-red tile is one people learn to ignore."""
    _tick(api.db, "a", minutes_ago=10)
    act = api._schedule_activity(30)
    assert act["windows"]["24"]["expected"] == 1
    assert act["windows"]["24"]["partial"] is True


def test_a_full_window_is_not_partial(api):
    _tick(api.db, "old", minutes_ago=60 * 30)
    _tick(api.db, "new", minutes_ago=1)
    act = api._schedule_activity(60)
    assert act["windows"]["1"]["partial"] is False
    assert act["windows"]["24"]["partial"] is False


def test_no_cadence_means_no_expectation(api):
    """A hand-made trigger the parser cannot read must produce no verdict,
    rather than a fabricated one."""
    _tick(api.db, "a", minutes_ago=5)
    act = api._schedule_activity(None)
    assert act["windows"]["1"]["expected"] is None


def test_no_runs_yet_means_no_expectation(api):
    """Before the first tick there is no anchor to measure from."""
    act = api._schedule_activity(30)
    assert act["age_minutes"] is None
    assert act["windows"]["1"]["expected"] is None


def test_a_silent_schedule_shows_the_shortfall(api):
    """The whole point: registered, fired once two hours ago, and nothing
    since. The count alone looks fine; against the cadence it does not."""
    _tick(api.db, "only-one", minutes_ago=120)
    act = api._schedule_activity(30)
    assert act["windows"]["1"]["runs"] == 0
    assert act["windows"]["24"]["runs"] == 1
    assert act["windows"]["24"]["expected"] == 5, "2h at 30m is five ticks"


# ---- timestamped output ---------------------------------------------------

def _stamped():
    buf = io.StringIO()
    clock = lambda: datetime(2026, 8, 25, 16, 4, 5, tzinfo=timezone.utc)  # noqa: E731
    return buf, _TimestampedOut(buf, clock)


def test_each_line_gets_a_stamp():
    buf, out = _stamped()
    out.write("alpha\nbeta\n")
    assert buf.getvalue() == (
        "[2026-08-25 16:04:05Z] alpha\n[2026-08-25 16:04:05Z] beta\n")


def test_a_partial_write_does_not_grow_a_stamp_mid_line():
    """Output arrives in whatever chunks print() produces; stamping every
    write would put a timestamp in the middle of a word."""
    buf, out = _stamped()
    out.write("half ")
    out.write("a line\n")
    assert buf.getvalue() == "[2026-08-25 16:04:05Z] half a line\n"


def test_blank_lines_are_left_alone():
    buf, out = _stamped()
    out.write("\n\n")
    assert buf.getvalue() == "\n\n"


def test_it_passes_through_stream_attributes():
    """Something downstream will ask for .encoding or .isatty()."""
    buf, out = _stamped()
    out.flush()
    assert out.getvalue() == ""  # proxied to the wrapped StringIO


def test_writing_nothing_is_a_no_op():
    buf, out = _stamped()
    out.write("")
    assert buf.getvalue() == ""
