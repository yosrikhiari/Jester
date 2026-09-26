"""A tick that is still working is not a corpse.

`reap_running` flips 'running' rows to 'aborted' and stamps them "the process
died without closing this run". Called with no age it treats EVERY running row
that way, including one belonging to a process that is alive.

That is not hypothetical. Three schedules overlap on the live machine --
JesterNightly (cycle), JesterNightlyScrape and JesterNightlyTreat -- and all
three have been seen Running at the same moment. `cycle` has always passed an
age; `cmd_run` passed none, so a treat tick firing mid-cycle killed the live
cycle. 26 of 264 runs in the live archive carry that epitaph, and each one
then blocked the following tick, because `active_run` makes a tick stand down
when a row still says 'running'.
"""
from jester.store import active_run, open_db, reap_running, start_run


def _running(db, run_id, minutes_ago=0):
    start_run(db, run_id, models_used={})
    if minutes_ago:
        db.execute("UPDATE runs SET started_at = datetime('now', ?) "
                   "WHERE run_id = ?", (f"-{minutes_ago} minutes", run_id))
        db.commit()


def test_a_run_that_just_started_is_left_alone(tmp_path):
    """The regression. A cycle two minutes into its work must survive a treat
    tick starting up beside it."""
    db = open_db(str(tmp_path / "j.db"))
    _running(db, "cycle-alive", minutes_ago=2)

    reaped = reap_running(db, older_than_minutes=60)

    assert reaped == 0, "reaped a run that started two minutes ago"
    assert active_run(db) == "cycle-alive"


def test_a_genuinely_stuck_run_is_still_reaped(tmp_path):
    """The guard must not turn the reaper off. A run older than the window is
    what the reaper exists for."""
    db = open_db(str(tmp_path / "j.db"))
    _running(db, "cycle-dead", minutes_ago=180)

    assert reap_running(db, older_than_minutes=60) == 1
    assert active_run(db) is None


def test_the_epitaph_says_what_happened(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    _running(db, "cycle-dead", minutes_ago=180)
    reap_running(db, older_than_minutes=60)

    err = db.execute("SELECT error FROM runs WHERE run_id='cycle-dead'").fetchone()[0]
    assert "no exit recorded" in err


def test_ageless_reaping_is_what_went_wrong(tmp_path):
    """Pinned so nobody reintroduces it thinking the age is optional noise."""
    db = open_db(str(tmp_path / "j.db"))
    _running(db, "cycle-alive", minutes_ago=2)

    assert reap_running(db) == 1, (
        "without an age, reap_running kills a live run — this is the bug, "
        "documented here so the age at the call sites is understood as load-"
        "bearing rather than decoration"
    )
