"""Weekly schedules (2026-09-29).

The scheduler knew two cadences, daily and every N minutes. The Himalayas
sweep needs a third: it reads ~300 pages of a board whose data refreshes once
a day, so it is worth doing weekly and impolite to do daily. Nothing here
touches the real scheduler or this machine's launchers.
"""
import subprocess

import pytest

from jester import schedule


@pytest.fixture(autouse=True)
def _no_real_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)


class FakeRun:
    def __init__(self, stdout=""):
        self.calls, self.stdout = [], stdout

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


@pytest.mark.parametrize("given, day", [("sun", "SUN"), ("Sunday", "SUN"), ("MON", "MON"),
                                        (" wednesday ", "WED")])
def test_a_day_is_read_however_it_is_written(given, day):
    assert schedule.weekday(given) == day


def test_a_day_that_is_not_one_is_refused_by_name():
    with pytest.raises(ValueError, match="not a day of the week"):
        schedule.weekday("someday")


def test_windows_registers_a_weekly_task_on_that_day(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    res = schedule.install(at="04:00", db="data/signals-live.db", command="signals-sweep",
                           task_name="JesterNightlySignalsSweep", weekly="sunday")
    assert res["ok"] is True
    cmd = fake.calls[0]
    assert cmd[cmd.index("/sc") + 1] == "WEEKLY"
    assert cmd[cmd.index("/d") + 1] == "SUN"
    assert cmd[cmd.index("/st") + 1] == "04:00"
    assert "weekly on SUN at 04:00" in res["detail"]


def test_cron_gets_a_day_of_week_field():
    line = schedule.cron_line("04:00", "data/signals-live.db", "config", "python",
                              command="signals-sweep", weekly="SUN")
    assert line.startswith("0 4 * * 0  ")
    assert schedule.cron_line("04:00", command="signals-brief").startswith("0 4 * * *  ")


def test_every_and_weekly_together_is_refused(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_run", FakeRun())
    res = schedule.install(every=30, weekly="SUN")
    assert res["ok"] is False
    assert "choose one cadence" in res["detail"]


def test_a_bad_day_is_refused_before_anything_is_registered(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    res = schedule.install(weekly="funday")
    assert res["ok"] is False
    assert fake.calls == []


def test_status_reads_a_weekly_task_back():
    """schtasks /v puts a weekly cadence in Schedule Type + Days + Start Time,
    and "Repeat: Every: Disabled" -- the same trap the daily case had."""
    fields = schedule.parse_query_fields(
        "Schedule Type:                        Weekly\n"
        "Start Time:                           4:00:00 AM\n"
        "Days:                                 SUN\n"
        "Repeat: Every:                        Disabled\n")
    assert schedule._cadence_from(fields) == "weekly on SUN at 4:00:00 AM"
    assert schedule.interval_minutes(fields) == 7 * 24 * 60


def test_the_cli_passes_the_day_through(monkeypatch):
    from jester import cli
    seen = {}

    def install(**kw):
        seen.update(kw)
        return {"ok": True, "action": "create", "detail": "ok"}

    monkeypatch.setattr(schedule, "install", install)
    cli.main(["schedule", "install", "--command", "signals-sweep", "--weekly", "SUN",
              "--at", "04:00", "--db", "data/signals-live.db"])
    assert seen["weekly"] == "SUN"
    assert seen["command"] == "signals-sweep"
    assert seen["task_name"].endswith("SignalsSweep")
