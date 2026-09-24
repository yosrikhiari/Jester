"""D-2 scheduler: registering the nightly run with the host's own scheduler.

`scripts/nightly.sh` was always the entry point, but nothing invoked it — the
"nightly job" was a script waiting for a cron line somebody had to write, and
on Windows there is no cron at all. These cover the registration path without
touching the real scheduler.

"Without touching" now includes the filesystem: `install()` on the Windows path
writes the launcher batch file into `<repo>/data/`, so these tests were quietly
rewriting whatever schedule this machine has registered — pointing it at a bare
`python.exe` with a relative database path and no scrape scope. The task kept
firing; it just no longer did what the operator configured.
"""
from types import SimpleNamespace
import subprocess
from pathlib import Path

import pytest

from jester import schedule



@pytest.fixture(autouse=True)
def _never_touch_the_real_launcher(tmp_path, monkeypatch, request):
    """Redirect repo_root so no test here can write the machine's launcher.

    Exempt the one test whose subject IS repo_root — it asserts the real path
    resolves to this project, which a redirect would make vacuous.
    """
    if request.node.name.startswith("test_repo_root"):
        return
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)


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
    # The registered action is the generated launcher, not an inline command:
    # `schtasks /tr` refuses anything over 261 characters and the obvious
    # one-liner repeats the repo path four times.
    assert tr.strip('"').endswith(schedule.LAUNCHER_NAME)
    assert len(tr) <= 261
    # A scheduled task inherits none of the launching shell's exports, so the
    # launcher must set PYTHONPATH and cd into the repo itself.
    script = schedule.launcher_script("data/j.db", "config", "py.exe")
    assert "PYTHONPATH" in script
    # `cycle`, never `run`: `run` drains a queue that nothing fills, so the
    # job fired on time for months and archived nothing.
    assert "jester.cli cycle" in script
    # -u, because this stdout is redirected to a FILE and Python block-buffers
    # a redirected stream. Without it a `treat` run that spends an hour in
    # synthesis writes nothing to the log until it exits, and a healthy long
    # run is indistinguishable from a hung one while it is still happening.
    assert "-u -m jester.cli" in script


def test_install_surfaces_a_scheduler_failure(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_run", FakeRun(returncode=1, stderr="ERROR: Access is denied."))
    res = schedule.install()
    assert res["ok"] is False and "Access is denied" in res["detail"]


def _capture(sink):
    """A `_write_crontab` stand-in that records the lines and reports success."""

    def write(lines):
        sink["lines"] = list(lines)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return write


def test_posix_install_writes_the_line_into_the_crontab(monkeypatch):
    """POSIX installs for real. `jester schedule install` that only PRINTS a
    crontab line leaves nothing scheduled — the operator has to notice the
    line, copy it, and run `crontab -e`, and until they do `schedule status`
    correctly reports NOT installed. Windows registers the task outright, so
    the POSIX path does too."""
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    written = {}
    monkeypatch.setattr(schedule, "_read_crontab", lambda: ["0 5 * * * backup.sh"])
    monkeypatch.setattr(schedule, "_write_crontab", _capture(written))
    res = schedule.install(at="02:15", db="data/j.db")
    assert res["ok"] is True and res["action"] == "create"
    line = written["lines"][-1]
    assert "15 2 * * *" in line
    # The scheduled verb is `cycle` (fetch AND process). Registering `run`
    # gave a job that drained a queue nothing ever filled.
    assert "jester.cli cycle" in line
    # An unrelated crontab entry must survive the install.
    assert "0 5 * * * backup.sh" in written["lines"]


def test_posix_install_replaces_a_previous_jester_line(monkeypatch):
    """Re-installing must not leave two schedules racing each other."""
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    stale = "*/30 * * * * cd /r && python -m jester.cli cycle --db data/j.db"
    written = {}
    monkeypatch.setattr(schedule, "_read_crontab", lambda: [stale])
    monkeypatch.setattr(schedule, "_write_crontab", _capture(written))
    schedule.install(at="02:15", db="data/j.db")
    assert stale not in written["lines"]
    assert len(written["lines"]) == 1


def test_posix_install_says_so_when_the_host_has_no_crontab(monkeypatch):
    """A container without cron cannot schedule anything; say that plainly
    rather than reporting a job that will never fire."""
    monkeypatch.setattr(schedule, "is_windows", lambda: False)

    def no_crontab():
        raise FileNotFoundError("crontab")

    monkeypatch.setattr(schedule, "_read_crontab", no_crontab)
    res = schedule.install(at="02:15", db="data/j.db")
    assert res["ok"] is False and res["action"] == "manual"
    assert "crontab" in res["detail"]


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


# ---- a scheduled run has to actually happen on a laptop --------------------

def test_install_sets_the_three_settings_schtasks_cannot(monkeypatch):
    """Task Scheduler's defaults refuse to start on battery, kill a run when
    you unplug, and never retry a slot missed while asleep. Read off this
    machine's own JesterNightly task, all three were wrong — so a nightly job
    on an unplugged or sleeping laptop produced nothing, silently. `schtasks`
    has no switch for any of them, which is why they were never set."""
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)

    res = schedule.install(at="04:30", db="data/j.db", config="config")
    assert res["ok"] is True
    powershell = [c for c in fake.calls if c and c[0] == "powershell"]
    assert powershell, "nothing applied the power settings"
    script = powershell[0][-1]
    assert "-StartWhenAvailable" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    # Waking someone's laptop at 03:00 to scrape a forum is their decision,
    # and StartWhenAvailable already covers the missed slot by running late.
    assert "-WakeToRun" not in script


def test_a_task_that_cannot_be_hardened_says_so(monkeypatch):
    """The task still works on mains power, so this downgrades the install
    rather than failing it — but an operator who believes the missed-run
    setting is on when it is not will read an empty day as an empty internet."""
    monkeypatch.setattr(schedule, "is_windows", lambda: True)

    class Refuses(FakeRun):
        def __call__(self, cmd, **kw):
            out = super().__call__(cmd, **kw)
            if cmd and cmd[0] == "powershell":
                out.returncode = 1
                out.stderr = "Access is denied."
            return out

    monkeypatch.setattr(schedule, "_run", Refuses())
    res = schedule.install(at="04:30", db="data/j.db", config="config")
    assert res["ok"] is True, "a task on mains power is still a working task"
    assert "WARNING" in res["detail"] and "will not catch up" in res["detail"]


def test_signals_is_schedulable_and_gets_its_own_task():
    assert "signals" in schedule.TASK_FOR_COMMAND
    assert schedule.TASK_FOR_COMMAND["signals"] != schedule.TASK_NAME


def test_the_signals_launcher_runs_the_subcommand_and_drops_config():
    """`signals` is a sub-command (`signals run`), and it has no `--config`
    option — the launcher's fixed `--db X --config Y` shape would have made it
    exit 2 on every single tick."""
    script = schedule.launcher_script("data/signals.db", "config", "py.exe", "signals")
    assert "jester.cli signals run" in script
    assert "--config" not in script
    assert '--db "data/signals.db"' in script
    # The commands that do take it must be untouched.
    assert '--config "config"' in schedule.launcher_script(
        "data/j.db", "config", "py.exe", "cycle")


def test_the_signals_cron_line_matches_the_same_shape():
    line = schedule.cron_line("03:00", "data/signals.db", "config", "py.exe",
                              command="signals")
    assert "jester.cli signals run" in line
    assert "--config" not in line


# --- the hiring collector is schedulable ------------------------------------

def test_every_schedulable_command_knows_how_to_run():
    """A command with a task name but no argv would install a task that runs
    nothing. The two tables are separate and must agree."""
    from jester.schedule import COMMAND_SPEC, TASK_FOR_COMMAND

    assert set(TASK_FOR_COMMAND) == set(COMMAND_SPEC), (
        f"registries disagree: {set(TASK_FOR_COMMAND) ^ set(COMMAND_SPEC)}"
    )


def test_the_cli_offers_exactly_what_is_schedulable(capsys):
    """`signals-hiring` could exist, be registered, be approved and still never
    run, because the CLI's --command choices were a hand-written tuple that did
    not include it. Deriving them from the registry is what stops that.

    Asked of the real parser: an invalid choice makes argparse print the list
    it WOULD accept, which is the thing under test.
    """
    import os
    import pytest as _pytest
    from jester import cli
    from jester.schedule import TASK_FOR_COMMAND

    # cli.main() calls load_env_once(), which reads the repo's .env into
    # os.environ and leaves it there. That flipped four later tests out of
    # mock mode the first time this file grew a main() call — they passed
    # alone and failed in the suite, which is the worst way to find out.
    before = os.environ.copy()
    try:
        with _pytest.raises(SystemExit):
            cli.main(["schedule", "--command", "no-such-command"])
        offered = capsys.readouterr().err
    finally:
        os.environ.clear()
        os.environ.update(before)

    for command in TASK_FOR_COMMAND:
        assert command in offered, (
            f"{command!r} is schedulable but the CLI will not accept it"
        )


def test_each_command_gets_its_own_task_name():
    """Two commands sharing a task name would have the second overwrite the
    first's schedule, silently."""
    from jester.schedule import TASK_FOR_COMMAND

    names = list(TASK_FOR_COMMAND.values())
    assert len(names) == len(set(names)), f"duplicate task names: {names}"


def test_the_hiring_collector_carries_its_own_rules():
    """It asks a different question from the forum collector. Running it
    against signal_rules.yaml would score adverts with rules written for
    people describing problems."""
    from jester.schedule import command_argv

    argv = command_argv("signals-hiring")
    assert "--source" in argv and "hackernews-hiring" in argv
    assert "--rules" in argv and "config/hiring_rules.yaml" in argv
