"""The jobs scraper on the Problem signals page: run it now, or on a schedule.

None of these touch the real Task Scheduler or the machine's launcher files:
`_run` is faked and `repo_root` points at a temp dir, the same guard
test_schedule.py uses, because `install()` writes the launcher the registered
task executes.
"""
import subprocess
from pathlib import Path

import pytest

from jester import schedule
from jester.console.api import ConsoleAPI

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

DISABLED_DAILY = """\
TaskName:                             \\JesterNightlySignalsHiring
Next Run Time:                        N/A
Status:                               Disabled
Last Run Time:                        9/25/2026 9:30:01 AM
Last Result:                          0
Scheduled Task State:                 Disabled
Schedule Type:                        Daily
Start Time:                           9:30:00 AM
Repeat: Every:                        Disabled
"""


class FakeRun:
    """Records schtasks invocations; answers /query with `query_out`."""

    def __init__(self, query_out="", query_rc=0):
        self.calls = []
        self.query_out, self.query_rc = query_out, query_rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "/query" in cmd:
            return subprocess.CompletedProcess(cmd, self.query_rc, self.query_out, "")
        return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")

    def made(self, verb):
        return [c for c in self.calls if verb in c]


@pytest.fixture()
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun(DISABLED_DAILY)
    monkeypatch.setattr(schedule, "_run", fake)
    return fake


@pytest.fixture()
def api(tmp_path):
    return ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(REPO_CONFIG))


def test_status_tells_a_paused_task_from_a_live_one(host):
    """Registered is not live: a disabled task still answers /query, and
    reading `installed` alone reported a paused job as a working schedule."""
    st = schedule.status("JesterNightlySignalsHiring")
    assert st["installed"] is True
    assert st["enabled"] is False
    assert st["running"] is False
    assert st["last_run"] == "9/25/2026 9:30:01 AM" and st["last_result"] == "0"


def test_set_enabled_pauses_without_unregistering(host):
    res = schedule.set_enabled("JesterNightlySignalsHiring", False)
    assert res["ok"] is True
    assert host.made("/change") == [
        ["schtasks", "/change", "/tn", "JesterNightlySignalsHiring", "/disable"]]
    assert not host.made("/delete"), "off must be a pause, not a delete"


def test_scraper_status_reports_the_hiring_task(host, api):
    d = api.jobs_scraper()
    assert d["task"] == "JesterNightlySignalsHiring"
    assert d["installed"] is True and d["enabled"] is False
    assert d["command"].startswith("jester signals run --source hackernews-hiring")
    assert d["job"] is None


def test_turning_it_off_disables_the_task(host, api):
    res = api.jobs_scraper_schedule(enabled=False)
    assert res["ok"] is True
    assert host.made("/disable") and not host.made("/create")


def test_daily_registers_the_same_command_the_button_runs(host, api):
    res = api.jobs_scraper_schedule(enabled=True, at="7:05")
    assert res["ok"] is True, res
    create = host.made("/create")[0]
    assert create[create.index("/tn") + 1] == "JesterNightlySignalsHiring"
    assert create[create.index("/st") + 1] == "07:05"
    launcher = schedule.launcher_path("signals-hiring").read_text()
    assert "signals run --source hackernews-hiring" in launcher
    assert "--query 2 --limit 1000" in launcher


def test_every_12h_repeats_hourly(host, api):
    res = api.jobs_scraper_schedule(enabled=True, every=720)
    assert res["ok"] is True, res
    create = host.made("/create")[0]
    assert create[create.index("/sc") + 1] == "HOURLY"
    assert create[create.index("/mo") + 1] == "12"


@pytest.mark.parametrize("every", [15, 60, "abc"])
def test_intervals_outside_the_presets_are_refused(host, api, every):
    """Tighter than six hours re-reads a monthly thread for nothing, at the
    cost of someone else's API."""
    res = api.jobs_scraper_schedule(enabled=True, every=every)
    assert res["ok"] is False
    assert not host.made("/create")


@pytest.mark.parametrize("at", ["25:00", "9h30", "12:75"])
def test_bad_times_are_refused(host, api, at):
    res = api.jobs_scraper_schedule(enabled=True, at=at)
    assert res["ok"] is False and "HH:MM" in res["error"]
    assert not host.made("/create")


def test_run_now_refuses_while_the_scheduled_run_is_going(host, api):
    """Two collectors writing one SQLite file at once is the shape that
    corrupted the main archive."""
    host.query_out = DISABLED_DAILY.replace(
        "Status:                               Disabled",
        "Status:                               Running")
    res = api.jobs_scraper_run()
    assert res["ok"] is False and "in progress" in res["error"]


def test_scheduling_is_windows_only_here(monkeypatch, api):
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    res = api.jobs_scraper_schedule(enabled=True, at="09:30")
    assert res["ok"] is False and "signals-hiring" in res["detail"]
