"""A scheduled failure has to reach the operator (V3-18).

The generated launcher used to echo `%ERRORLEVEL%` into a log and exit -- the
only push hook was `JESTER_NOTIFY_CMD`, read by `scripts/nightly.sh`, which the
host schedule never calls. So a barren collector ran, failed, and died in a
file nobody opens. These pin the missing wire: on a non-zero exit the launcher
runs `jester notify` before leaving with the same code, and `notify` turns
`JESTER_NOTIFY_CMD` into a call that carries the task name and exit code.

The launcher is only inspected, never executed: these run on Windows and Linux
alike, and the batch file is the bit a test can assert on both.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from jester import cli, schedule

PY = r"C:\Python312\python.exe"


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    """No test here may write this machine's real launcher."""
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)


def _hook_argv(*parts):
    """A command line the way the host would write it, so the parser is tested
    with the quoting it will actually meet."""
    if os.name == "nt":
        return subprocess.list2cmdline([str(p) for p in parts])
    return shlex.join([str(p) for p in parts])


def _helper(tmp_path):
    helper = tmp_path / "hook.py"
    helper.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "task = os.environ.get('JESTER_TASK', '')\n"
        "code = os.environ.get('JESTER_EXIT_CODE', '')\n"
        "Path(sys.argv[1]).write_text(task + '|' + code)\n",
        encoding="utf-8")
    return helper


# --- the generated launcher -------------------------------------------------

def test_the_windows_launcher_notifies_before_it_exits():
    script = schedule.launcher_script("data/signals-live.db", "config", PY,
                                      command="signals-hiring")

    assert "jester.cli notify" in script
    assert '--task "JesterNightlySignalsHiring"' in script, "quote like every other value"
    notify_line = next(ln for ln in script.splitlines() if "jester.cli notify" in ln)
    # Runs only on a failure, and after the exit code was captured.
    assert notify_line.startswith('if not "%RC%"=="0"')
    # The batch still leaves with the run's code, not the hook's.
    assert "exit /b %RC%" in script


def test_the_posix_cron_line_notifies_before_it_exits():
    line = schedule.cron_line("10:15", "data/signals-live.db", "config", PY,
                              command="health")

    assert "jester.cli health" in line
    assert 'jester.cli notify --task "JesterNightlyHealth" --exit' in line
    assert 'exit "$rc"' in line
    assert line.split()[:5] == ["15", "10", "*", "*", "*"]


# --- what install may replace on the host's crontab -------------------------

def _fake_crontab(monkeypatch):
    """A crontab in memory, so install/remove can be driven on either platform."""
    state = {"lines": []}

    def write(lines):
        state["lines"] = list(lines)
        return subprocess.CompletedProcess(args=["crontab", "-"], returncode=0,
                                           stdout="", stderr="")

    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    monkeypatch.setattr(schedule, "_read_crontab", lambda: list(state["lines"]))
    monkeypatch.setattr(schedule, "_write_crontab", write)
    return state


def test_installing_health_leaves_an_installed_signals_line_alone(monkeypatch):
    """`jester.cli ` as the needle made installing one command delete another
    command's entry, so the morning chain lost its collector."""
    state = _fake_crontab(monkeypatch)

    schedule.install(db="data/signals-live.db", command="signals-hiring")
    schedule.install(db="data/signals-live.db", command="health")

    lines = state["lines"]
    assert len(lines) == 2, lines
    assert any("jester.cli health" in ln for ln in lines)
    assert any("hackernews-hiring" in ln for ln in lines)


def test_reinstalling_a_command_replaces_only_its_own_line(monkeypatch):
    state = _fake_crontab(monkeypatch)

    for _ in range(2):
        schedule.install(db="data/signals-live.db", command="health")

    assert len(state["lines"]) == 1, state["lines"]


def test_installing_plain_signals_leaves_the_hiring_collector_alone(monkeypatch):
    """Both register as `jester.cli signals run ...`; the match has to stop at a
    word boundary or one install overwrites the other."""
    state = _fake_crontab(monkeypatch)

    schedule.install(db="data/signals-live.db", command="signals-hiring")
    schedule.install(db="data/signals-live.db", command="signals")

    assert len(state["lines"]) == 2, state["lines"]


def test_remove_clears_every_line_that_names_the_module(monkeypatch):
    """Removal keeps the breadth on purpose: "remove the jester schedule" drops
    every entry naming this module, including one an operator wrote by hand.
    Installing must not -- see the tests above."""
    state = _fake_crontab(monkeypatch)
    state["lines"] = [
        "0 1 * * * cd /srv/jester && python -m jester.cli cycle --db data/x.db",
        "0 2 * * * cd /srv/jester && python -m jester.cli signals run --db data/x.db",
        "0 3 * * * /usr/local/bin/backup.sh",
    ]

    result = schedule.remove()

    assert result["ok"] is True
    assert state["lines"] == ["0 3 * * * /usr/local/bin/backup.sh"]


# --- `jester notify` --------------------------------------------------------

def test_a_task_that_exits_1_reaches_the_configured_hook(tmp_path, monkeypatch):
    helper = _helper(tmp_path)
    target = tmp_path / "notified.txt"
    monkeypatch.setenv("JESTER_NOTIFY_CMD",
                       _hook_argv(sys.executable, helper, target))

    cli.main(["notify", "--task", "JesterNightlyHealth", "--exit", "1"])

    assert target.read_text() == "JesterNightlyHealth|1"


def test_a_task_that_exits_0_writes_nothing(tmp_path, monkeypatch, capsys):
    helper = _helper(tmp_path)
    target = tmp_path / "notified.txt"
    monkeypatch.setenv("JESTER_NOTIFY_CMD",
                       _hook_argv(sys.executable, helper, target))

    cli.main(["notify", "--task", "JesterNightlyHealth", "--exit", "0"])

    assert not target.exists(), "success is not news"
    assert capsys.readouterr().out.strip() == ""


def test_no_hook_configured_is_a_silent_no_op(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JESTER_NOTIFY_CMD", raising=False)

    cli.main(["notify", "--task", "JesterNightlyHealth", "--exit", "1"])

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_the_exit_code_keeps_its_old_spelling(tmp_path, monkeypatch):
    """`scripts/nightly.sh` passes --exit-code; the launcher passes --exit.
    Both name the same fact, so both are accepted."""
    helper = _helper(tmp_path)
    target = tmp_path / "notified.txt"
    monkeypatch.setenv("JESTER_NOTIFY_CMD",
                       _hook_argv(sys.executable, helper, target))

    cli.main(["notify", "--exit-code", "2"])

    assert target.read_text() == "|2"


# --- the declaration --------------------------------------------------------

def test_the_declaration_schedules_the_health_check():
    specs = {s["command"]: s for s in schedule.load_desired()}

    assert schedule.TASK_FOR_COMMAND["health"] == "JesterNightlyHealth"
    health = specs["health"]
    assert health["enabled"] is True
    assert schedule.describe_cadence(health) == "daily at 10:15"
