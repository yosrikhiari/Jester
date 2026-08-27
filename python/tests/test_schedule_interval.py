"""Recurring scrape: interval cadences, the launcher, and the overlap guard.

Every test here corresponds to something that was broken or absent:
the scheduler could only say "daily"; it registered `jester run`, a command
that drains a queue nothing fills; the inline command exceeded what
`schtasks /tr` accepts on any real repo path; and the reaper that keeps one
nightly run honest would have had two ticks killing each other.
"""
from pathlib import Path

import pytest

from jester import schedule as S
from jester.store import active_run, finish_run, open_db, reap_running, start_run
from jester.worker import worker_argv



@pytest.fixture(autouse=True)
def _never_touch_the_real_launcher(tmp_path, monkeypatch, request):
    """No test in this module may write the machine's real launcher.

    `_windows_action` and `write_launcher` write to `<repo>/data/` by design,
    so a test that calls either reconfigures whatever schedule this box has
    registered — a unit test quietly editing live machine state. Tests that
    want to assert on a written file redirect repo_root themselves; this makes
    forgetting impossible rather than merely discouraged.
    """
    if "no_launcher_guard" in request.keywords:
        return
    monkeypatch.setattr(S, "repo_root", lambda: tmp_path)


# ---- cadence translation -------------------------------------------------

@pytest.mark.parametrize("minutes,sc,mo", [
    (1, "MINUTE", 1),
    (5, "MINUTE", 5),
    (30, "MINUTE", 30),
    (59, "MINUTE", 59),
    (60, "HOURLY", 1),
    (120, "HOURLY", 2),
    (720, "HOURLY", 12),
    (1439, "MINUTE", 1439),
])
def test_interval_maps_to_a_schtasks_cadence(minutes, sc, mo):
    assert S.interval_schedule(minutes) == (sc, mo)


def test_whole_hours_are_expressed_as_hours():
    """`/sc MINUTE /mo 120` works but reads as "every 120 minutes" in the Task
    Scheduler UI. Anything dividing evenly into hours says so."""
    assert S.interval_schedule(180)[0] == "HOURLY"
    assert S.interval_schedule(90)[0] == "MINUTE", "90 is not a whole hour"


@pytest.mark.parametrize("bad", [0, -1, 1440, 5000])
def test_out_of_range_intervals_are_refused(bad):
    with pytest.raises(ValueError):
        S.interval_schedule(bad)


# ---- cron -----------------------------------------------------------------

def test_cron_line_for_sub_hour_intervals():
    assert S.cron_line(every=15).startswith("*/15 * * * *")


def test_cron_line_for_whole_hours():
    assert S.cron_line(every=120).startswith("0 */2 * * *")


def test_cron_refuses_a_cadence_it_cannot_express():
    """cron has no "every 90 minutes". Rounding silently would give a schedule
    that does not match what was asked for."""
    with pytest.raises(ValueError, match="cannot express"):
        S.cron_line(every=90)


def test_cron_line_defaults_to_daily():
    assert S.cron_line(at="04:30").startswith("30 4 * * *")


def test_every_scheduled_command_is_the_full_cycle():
    """The original bug: the registered command was `jester run`, which
    processes the queue and never fills it, so the job fired on time forever
    and archived nothing."""
    assert " cycle " in S.cron_line(every=30) or S.cron_line(every=30).endswith("cycle")
    assert "jester.cli cycle" in S.cron_line(every=30)
    assert "jester.cli cycle" in S.cron_line(at="03:00")
    assert S.cycle_command()[-4:] == ["--db", "data/jester.db", "--config", "config"][-4:]
    assert "cycle" in S.cycle_command()


# ---- the launcher ---------------------------------------------------------

def test_registered_command_fits_schtasks_261_char_limit(tmp_path, monkeypatch):
    """`schtasks /tr` refuses anything longer. The obvious inline one-liner
    repeats the repo path four times and blew past it on this very repo, so
    the daily install could not be registered at all.

    repo_root is redirected because `_windows_action` WRITES the launcher —
    without this the test rewrites the real `data/scheduled-cycle.cmd`, which
    silently reconfigures whatever schedule the machine has registered.
    """
    import subprocess
    monkeypatch.setattr(S, "repo_root", lambda: tmp_path)
    argv = S._windows_action("data/jester.db", "config", "python.exe")
    assert len(subprocess.list2cmdline(argv)) <= 261


def test_launcher_sets_pythonpath_and_cwd():
    """A scheduled task inherits none of the installing shell's exports."""
    script = S.launcher_script("data/jester.db", "config", "py.exe")
    assert "set \"PYTHONPATH=" in script
    assert "cd /d " in script


def test_launcher_marks_the_run_as_scheduled():
    """`origin` drives the rolling duration baseline; automated runs filed as
    "manual" blend two populations."""
    assert 'JESTER_ORIGIN=scheduled' in S.launcher_script("db", "cfg", "py.exe")


def test_launcher_captures_output():
    """schtasks records an exit code and nothing else, so without this a
    scheduled run leaves no account of itself."""
    script = S.launcher_script("db", "cfg", "py.exe")
    assert "schedule-cycle.log" in script
    assert "2>&1" in script


def test_each_command_logs_to_its_own_file():
    """They used to share data/schedule.log, and cmd.exe grants no write
    sharing on a `>>` target — so while a long run held it, every other task's
    very FIRST echo failed with a sharing violation, the line vanished, and
    because that echo is the batch's last statement the task reported failure.
    A 2h21m treat run made both other tasks look broken."""
    logs = {c: str(S.log_path(c)) for c in ("cycle", "ingest", "treat")}
    assert len(set(logs.values())) == 3, logs
    for command, log in logs.items():
        assert log in S.launcher_script("db", "cfg", "py.exe", command=command)


def test_the_exit_code_survives_the_redirect():
    """Written the natural way — `echo ... %ERRORLEVEL%>>"log"` — cmd parses
    the digit immediately before `>>` as a FILE HANDLE, so `1>>` redirects
    stdout instead of printing the code. Every single-digit exit code this
    scheduler produced was swallowed that way; 9009 was the only one that ever
    reached the log, because four digits are not a handle number."""
    script = S.launcher_script("db", "cfg", "py.exe", command="treat")
    assert "%ERRORLEVEL%" in script
    # The redirect must come BEFORE the echo, never after the variable.
    assert '%ERRORLEVEL%>>' not in script
    assert '%ERRORLEVEL% >>' not in script
    exit_line = next(l for l in script.splitlines() if "exited" in l)
    assert exit_line.startswith(">>"), exit_line


def test_launcher_uses_crlf():
    """cmd.exe does not reliably parse an LF-only batch file."""
    assert "\r\n" in S.launcher_script("db", "cfg", "py.exe")


def test_launcher_runs_cycle_not_run():
    assert "jester.cli cycle" in S.launcher_script("db", "cfg", "py.exe")


# ---- the overlap guard ----------------------------------------------------

def test_young_run_survives_an_age_gated_reap(tmp_path):
    """The whole hazard of an N-minute schedule: tick B must not mark tick A's
    live run as a crashed orphan."""
    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "tick-A")
    assert reap_running(db, older_than_minutes=60) == 0
    assert active_run(db) == "tick-A"


def test_old_run_is_reaped(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "crashed")
    assert reap_running(db, older_than_minutes=0) == 1
    assert active_run(db) is None


def test_ungated_reap_keeps_its_original_meaning(tmp_path):
    """R25's nightly behaviour must not change: with no age, every 'running'
    row is an orphan."""
    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "whenever")
    assert reap_running(db) == 1
    assert active_run(db) is None


def test_active_run_is_none_once_finished(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "done-one")
    finish_run(db, "done-one", status="completed")
    assert active_run(db) is None


# ---- the worker argv the schedule ends up running ------------------------

def test_worker_db_path_is_absolute():
    """The worker runs with cwd=go/, so a relative --db lands in go/data/.
    A scheduled run scraped 554 comments into a database nothing reads before
    this was pinned."""
    import os
    cmd = worker_argv("go", "config", "data/jester.db", "r")
    assert os.path.isabs(cmd[cmd.index("-db") + 1])


def test_worker_memory_db_is_left_alone():
    cmd = worker_argv("go", "config", ":memory:", "r")
    assert cmd[cmd.index("-db") + 1] == ":memory:"


# ---- status ---------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("0 Hour(s), 30 Minute(s)", "every 30 minutes"),
    ("1 Hour(s), 0 Minute(s)", "every 1 hour"),
    ("2 Hour(s), 0 Minute(s)", "every 2 hours"),
    ("0 Hour(s), 1 Minute(s)", "every 1 minute"),
])
def test_repeat_line_reads_like_english(raw, want):
    assert S.humanise_repeat(raw) == want


def test_unparseable_repeat_line_is_passed_through():
    """Better to show schtasks' own words than to invent a cadence."""
    assert S.humanise_repeat("something new") == "something new"


# ---- cadence, parsed from REAL schtasks output ---------------------------
# Hand-built dicts are how the first version of this passed while the parser
# was broken: the test invented the key `repeat: every`, but the label itself
# contains a colon, so a first-colon split actually yields `repeat`. These
# read captured output from both trigger shapes instead.

FIXTURES = Path(__file__).parent / "fixtures"


def _fields(name):
    return S.parse_query_fields(
        (FIXTURES / f"schtasks_{name}.txt").read_text(encoding="utf-8"))


def test_colon_bearing_label_is_parsed():
    """`Repeat: Every:  0 Hour(s), 30 Minute(s)` — the label has a colon in it,
    and it is the only field carrying an interval cadence."""
    assert _fields("minute")["repeat: every"] == "0 Hour(s), 30 Minute(s)"


def test_ordinary_labels_still_parse():
    assert _fields("daily")["schedule type"] == "Daily"
    assert _fields("minute")["start time"] == "3:31:00 PM"


def test_cadence_of_a_repeating_task():
    assert _cadence(_fields("minute")) == "every 30 minutes"


def test_cadence_of_a_daily_task_is_not_disabled():
    """A daily task reports `Repeat: Every: Disabled`; reading that field for
    both shapes reported a healthy daily schedule as "Disabled"."""
    assert _cadence(_fields("daily")) == "daily at 4:15:00 AM"


def test_repeating_task_is_not_described_as_one_time_only():
    """Its `Schedule Type` genuinely says "One Time Only, Minute" — true of the
    trigger, useless to a reader, and wrong as a cadence."""
    assert "one time only" not in _cadence(_fields("minute")).lower()


def test_cadence_falls_back_to_whatever_is_present():
    assert _cadence({"schedule type": "Weekly"}) == "Weekly"
    assert _cadence({}) == ""


def _cadence(fields):
    return S._cadence_from(fields)


# ---- scrape scope: WHAT a tick fetches and HOW DEEP ----------------------
# The scheduler could say when and nothing else, so every unattended tick meant
# "every enabled source, no ceiling" — the one shape you least want firing on a
# timer against a rate-limited API.

def test_scope_flags_are_cycle_flags():
    assert S.scrape_flags({"only": ["hn-ask", "devops"], "max_posts": 3}) == [
        "--only", "hn-ask,devops", "--max-posts", "3"]


def test_scope_accepts_a_comma_string_or_a_list():
    assert S.scrape_flags({"only": "hn-ask, devops ,,"}) == ["--only", "hn-ask,devops"]


@pytest.mark.parametrize("scope", [
    {}, {"only": []}, {"only": ["  ", ""]},
    {"max_posts": 0}, {"max_comments": None}, {"max_ideas": ""},
])
def test_absent_empty_and_zero_all_mean_no_ceiling(scope):
    """`--max-posts 0` would be a ceiling of zero — a tick that fetches
    nothing. Absent must produce no flag rather than a zero."""
    assert S.scrape_flags(scope) == []


def test_scope_round_trips():
    scope = {"only": ["a", "b"], "max_posts": 3, "max_comments": 200, "max_ideas": 5}
    assert S.parse_scrape_flags(S.scrape_flags(scope)) == scope


def test_parse_ignores_flags_it_does_not_own():
    got = S.parse_scrape_flags(
        ["--db", "x.db", "--only", "a", "--config", "cfg", "--max-posts", "2"])
    assert got == {"only": ["a"], "max_posts": 2}


def test_parse_survives_a_non_numeric_ceiling():
    """A hand-edited launcher must not crash the console's schedule page."""
    assert S.parse_scrape_flags(["--max-posts", "lots"]) == {}


def test_parse_handles_the_launchers_quoting():
    """The launcher quotes every interpolated argument."""
    assert S.parse_scrape_flags(['"--only"', '"a,b"']) == {"only": ["a", "b"]}


def test_scope_reaches_the_windows_launcher():
    script = S.launcher_script(
        "db", "cfg", "py.exe", extra=S.scrape_flags({"only": ["hn-ask"], "max_posts": 3}))
    assert "--only" in script and "hn-ask" in script and "--max-posts" in script


def test_scope_reaches_the_cron_line():
    """The POSIX path dropped `extra` entirely, so a crontab schedule quietly
    walked every source no matter what was configured."""
    line = S.cron_line(every=30, extra=S.scrape_flags({"only": ["hn-ask"], "max_ideas": 2}))
    assert "--only hn-ask" in line and "--max-ideas 2" in line


def test_installed_options_reads_the_file_the_scheduler_runs(tmp_path, monkeypatch):
    """Not the form, not a stored copy — the launcher itself, which is what
    actually executes. They diverge the moment someone installs from the CLI."""
    monkeypatch.setattr(S, "repo_root", lambda: tmp_path)
    S.write_launcher("db", "cfg", "py.exe",
                     extra=S.scrape_flags({"only": ["hn-ask"], "max_comments": 150}))
    assert S.installed_options() == {"only": ["hn-ask"], "max_comments": 150}


def test_installed_options_is_empty_when_nothing_is_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "repo_root", lambda: tmp_path)
    assert S.installed_options() == {}


def test_an_ingest_run_is_given_room_to_finish():
    """900s was set when the list was sixteen sources of forum threads. It is
    now 95 across nine platforms, three of them individually slow — podcast
    transcripts are a megabyte each, the Reddit listing pages with ?after=
    (a full stealth-browser navigation per page), and the comment tree is
    expanded before it is read. A full walk went past 900s and was killed
    mid-run, losing whatever it had not yet enqueued."""
    from jester import worker

    assert worker.DEFAULT_INGEST_TIMEOUT >= 3600
    # A caller with no opinion must get the default, not subprocess.run's
    # "no timeout at all".
    import inspect

    src = inspect.getsource(worker.ingest)
    assert "if timeout is None" in src
    assert "timeout = DEFAULT_INGEST_TIMEOUT" in src
