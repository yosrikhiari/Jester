"""D-2 scheduler: registering the nightly run with the host's own scheduler.

`scripts/nightly.sh` was always the entry point, but nothing invoked it — the
"nightly job" was a script waiting for a cron line somebody had to write, and
on Windows there is no cron at all. These cover the registration path without
touching the real scheduler.
"""
import subprocess
from pathlib import Path

import pytest

from jester import schedule


class FakeRun:
    """Records schtasks/crontab invocations instead of running them."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def test_nightly_command_calls_the_module_not_the_bash_script():
    """nightly.sh needs a POSIX shell a stock Windows box does not have, and
    the wrapper adds nothing the CLI cannot do itself."""
    cmd = schedule.nightly_command(db="x.db", config="cfg", python="py.exe")
    assert cmd[:4] == ["py.exe", "-m", "jester.cli", "run"]
    assert "--db" in cmd and "x.db" in cmd
    assert not any("nightly.sh" in str(part) for part in cmd)


def test_status_reports_not_installed_honestly(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_run", FakeRun(returncode=1, stderr="ERROR: cannot find"))
    st = schedule.status()
    assert st["installed"] is False and st["scheduler"] == "schtasks"


def test_status_reports_installed_with_next_run(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_run", FakeRun(
        stdout="TaskName: \\JesterNightly\nNext Run Time: 26/08/2026 03:00:00\nStatus: Ready"))
    st = schedule.status()
    assert st["installed"] is True
    assert st["next_run"] == "26/08/2026 03:00:00"


def test_install_registers_a_daily_task_with_the_environment_it_needs(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)

    res = schedule.install(at="04:30", db="data/j.db", config="config")
    assert res["ok"] is True
    cmd = fake.calls[0]
    assert cmd[:2] == ["schtasks", "/create"]
    assert "/sc" in cmd and "DAILY" in cmd
    assert "04:30" in cmd
    tr = cmd[cmd.index("/tr") + 1]
    # A scheduled task inherits none of the launching shell's exports, so the
    # action must set PYTHONPATH and cd into the repo itself.
    assert "PYTHONPATH" in tr
    assert "jester.cli" in tr and "run" in tr


def test_install_surfaces_a_scheduler_failure(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_run", FakeRun(returncode=1, stderr="ERROR: Access is denied."))
    res = schedule.install()
    assert res["ok"] is False and "Access is denied" in res["detail"]


def test_posix_prints_the_crontab_line_instead_of_editing_it(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    res = schedule.install(at="02:15", db="data/j.db")
    # Editing someone's crontab behind their back is worse than telling them
    # the line to add.
    assert res["ok"] is False and res["action"] == "manual"
    assert "15 2 * * *" in res["detail"]
    assert "jester.cli run" in res["detail"]


def test_cron_line_is_a_valid_five_field_schedule():
    line = schedule.cron_line(at="07:05")
    fields = line.split()
    assert fields[:5] == ["5", "7", "*", "*", "*"]
    assert "jester.cli" in line


def test_remove_deletes_the_task(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    assert schedule.remove()["ok"] is True
    assert fake.calls[0][:2] == ["schtasks", "/delete"]


def test_cli_status_says_how_to_install_when_absent(monkeypatch, capsys):
    from jester.cli import cmd_schedule

    monkeypatch.setattr(schedule, "status",
                        lambda *a, **k: {"installed": False, "scheduler": "schtasks", "detail": ""})
    args = type("A", (), {})()
    args.action = "status"
    cmd_schedule(args)
    out = capsys.readouterr().out
    assert "NOT installed" in out
    assert "jester schedule install" in out


def test_repo_root_points_at_the_project(tmp_path):
    root = schedule.repo_root()
    assert (root / "python" / "jester").is_dir()
    assert (root / "config").is_dir()
