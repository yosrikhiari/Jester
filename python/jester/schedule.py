"""D-2 nightly scheduler: register the run with the host's own scheduler.

`scripts/nightly.sh` has always been the *entry point*, but nothing ever
invoked it — the "nightly job" was a script waiting for a cron line somebody
had to write by hand, and on Windows there is no cron at all. This registers it
for real, and can say whether it is registered, which is the difference between
a scheduler and a script named `nightly`.

Windows uses Task Scheduler via `schtasks`; POSIX prints the crontab line to
install (editing a user's crontab behind their back is worse than telling them
the line).
"""
import os
import platform
import subprocess
import sys
from pathlib import Path

TASK_NAME = "JesterNightly"
DEFAULT_TIME = "03:00"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def is_windows() -> bool:
    return platform.system() == "Windows"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def nightly_command(db: str = "data/jester.db", config: str = "config",
                    python: str | None = None) -> list:
    """The argv the scheduler should invoke.

    Deliberately calls the module rather than the bash script: `nightly.sh`
    needs a POSIX shell that a stock Windows box does not have, and the shell
    wrapper adds nothing the CLI cannot do itself.
    """
    return [
        python or sys.executable, "-m", "jester.cli", "run",
        "--db", db, "--config", config,
    ]


def _windows_action(db, config, python):
    root = repo_root()
    py = python or sys.executable
    # PYTHONPATH must be set for the task's own environment; a scheduled task
    # inherits none of this shell's exports.
    inner = (
        f'$env:PYTHONPATH="{root / "python"}"; '
        f'Set-Location "{root}"; '
        f'& "{py}" -m jester.cli run --db "{db}" --config "{config}"'
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", inner]


def status(task_name: str = TASK_NAME) -> dict:
    """Is a nightly job actually registered? Reports the truth, not a guess."""
    if is_windows():
        p = _run(["schtasks", "/query", "/tn", task_name, "/fo", "LIST"])
        if p.returncode != 0:
            return {"installed": False, "scheduler": "schtasks", "detail": ""}
        detail = p.stdout.strip()
        next_run = ""
        for line in detail.splitlines():
            if line.lower().startswith("next run time"):
                next_run = line.split(":", 1)[1].strip()
        return {"installed": True, "scheduler": "schtasks",
                "next_run": next_run, "detail": detail}
    p = _run(["crontab", "-l"])
    installed = p.returncode == 0 and "jester.cli run" in (p.stdout or "")
    return {"installed": installed, "scheduler": "cron", "detail": (p.stdout or "").strip()}


def cron_line(at: str = DEFAULT_TIME, db: str = "data/jester.db",
              config: str = "config", python: str | None = None) -> str:
    hh, _, mm = at.partition(":")
    root = repo_root()
    py = python or sys.executable
    return (
        f"{int(mm or 0)} {int(hh)} * * *  "
        f"cd {root} && PYTHONPATH={root / 'python'} "
        f"{py} -m jester.cli run --db {db} --config {config} "
        f">> {root / 'data' / 'nightly.log'} 2>&1"
    )


def install(at: str = DEFAULT_TIME, db: str = "data/jester.db",
            config: str = "config", task_name: str = TASK_NAME,
            python: str | None = None) -> dict:
    """Register the nightly job. Returns {ok, action, detail}."""
    if not is_windows():
        return {
            "ok": False,
            "action": "manual",
            "detail": (
                "POSIX: add this crontab line yourself (jester will not edit "
                "your crontab)\n\n    " + cron_line(at, db, config, python)
            ),
        }
    action = _windows_action(db, config, python)
    # schtasks takes the whole command as one string; quote the inner argv.
    tr = subprocess.list2cmdline(action)
    p = _run([
        "schtasks", "/create", "/tn", task_name, "/tr", tr,
        "/sc", "DAILY", "/st", at, "/f",
    ])
    if p.returncode != 0:
        return {"ok": False, "action": "create",
                "detail": (p.stderr or p.stdout).strip()}
    return {"ok": True, "action": "create",
            "detail": f"{task_name} runs daily at {at}"}


def remove(task_name: str = TASK_NAME) -> dict:
    if not is_windows():
        return {"ok": False, "action": "manual",
                "detail": "POSIX: remove the jester line from your crontab"}
    p = _run(["schtasks", "/delete", "/tn", task_name, "/f"])
    if p.returncode != 0:
        return {"ok": False, "action": "delete",
                "detail": (p.stderr or p.stdout).strip()}
    return {"ok": True, "action": "delete", "detail": f"{task_name} removed"}


def run_now(task_name: str = TASK_NAME) -> dict:
    """Trigger the registered task immediately — proves it actually fires."""
    if not is_windows():
        return {"ok": False, "action": "manual",
                "detail": "POSIX: run scripts/nightly.sh directly"}
    p = _run(["schtasks", "/run", "/tn", task_name])
    ok = p.returncode == 0
    return {"ok": ok, "action": "run",
            "detail": (p.stdout or p.stderr).strip()}
