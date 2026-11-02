"""D-2 nightly scheduler: register the run with the host's own scheduler.

`scripts/nightly.sh` has always been the *entry point*, but nothing ever
invoked it — the "nightly job" was a script waiting for a cron line somebody
had to write by hand, and on Windows there is no cron at all. This registers it
for real, and can say whether it is registered, which is the difference between
a scheduler and a script named `nightly`.

Windows uses Task Scheduler via `schtasks`; POSIX rewrites the user's crontab,
replacing any prior jester line so a re-install cannot leave two schedules
racing. Printing the line and leaving the operator to paste it was the earlier
behaviour, and it meant `install` reliably scheduled nothing.
"""

import os
import platform
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

TASK_NAME = "JesterNightly"
DEFAULT_TIME = "03:00"

#: schtasks caps `/sc MINUTE /mo` at one minute short of a day. Beyond that it
#: wants HOURLY, so anything that divides evenly into hours is expressed that
#: way — it reads better in the Task Scheduler UI and survives a reboot the
#: same.
MAX_MINUTE_INTERVAL = 1439


def interval_schedule(every_minutes: int):
    """(schtasks /sc value, /mo value) for an every-N-minutes cadence."""
    n = int(every_minutes)
    if n < 1:
        raise ValueError("interval must be at least 1 minute")
    if n > MAX_MINUTE_INTERVAL:
        raise ValueError(
            f"interval must be {MAX_MINUTE_INTERVAL} minutes or less "
            "(use a daily schedule beyond that)"
        )
    if n >= 60 and n % 60 == 0:
        return "HOURLY", n // 60
    return "MINUTE", n


def cycle_command(
    db: str = "data/jester.db", config: str = "config", python: str | None = None
) -> list:
    """The argv a schedule should invoke: the FULL loop.

    `run` alone drains a queue nothing fills — the nightly job this module
    registered for months fetched nothing and reported success every time.
    """
    return [
        python or sys.executable,
        "-m",
        "jester.cli",
        "cycle",
        "--db",
        db,
        "--config",
        config,
    ]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def is_windows() -> bool:
    return platform.system() == "Windows"


def _run(cmd, **kw):
    # utf-8/replace rather than the platform locale codec — schtasks and the
    # nightly run both emit bytes cp1252 cannot decode, and a dead reader
    # thread hands back stdout=None instead of an error.
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, capture_output=True, **kw)


def nightly_command(
    db: str = "data/jester.db", config: str = "config", python: str | None = None
) -> list:
    """The argv the scheduler should invoke.

    Deliberately calls the module rather than the bash script: `nightly.sh`
    needs a POSIX shell that a stock Windows box does not have, and the shell
    wrapper adds nothing the CLI cannot do itself.
    """
    return [
        python or sys.executable,
        "-m",
        "jester.cli",
        "run",
        "--db",
        db,
        "--config",
        config,
    ]


#: Where the generated launcher lives. Under data/ because it hardcodes this
#: machine's interpreter and repo path — it is local state, not source.
LAUNCHER_NAME = "scheduled-cycle.cmd"

#: What the launcher FILE carries, beyond the command it runs.
#:
#: 1 is anything written before this number existed, which is also every
#: launcher installed before Task 7. 2 adds the log rotation guard. Such a
#: launcher has exactly the argv this code would write, so comparing argv alone
#: reported it as current forever and `schedule apply`/`refresh` never
#: delivered the rotation to the one host that needed it. The marker is a fact
#: in the file; comparing it is what makes a file that must be rewritten
#: visible.
LAUNCHER_VERSION = 2

#: The line the version is written on, and read back from.
LAUNCHER_VERSION_RE = re.compile(r"^REM jester-launcher-version: (\d+)\s*$", re.M)


def launcher_path(command: str = "cycle") -> Path:
    """One launcher per scheduled COMMAND.

    Scraping and treatment run on different clocks — fetching is cheap and
    bounded by politeness, treatment is bounded by an LLM quota that resets on
    someone else's schedule — so they are separate tasks with separate
    launchers. A single shared file would have the second install silently
    overwrite the first.
    """
    if command in ("cycle", ""):
        return repo_root() / "data" / LAUNCHER_NAME
    return repo_root() / "data" / f"scheduled-{command}.cmd"


def log_path(command: str = "cycle") -> Path:
    """Where one scheduled command writes.

    ONE FILE PER COMMAND, because sharing one was actively breaking the
    schedule. cmd.exe opens a `>>` target without granting write sharing, so
    while a long run held data/schedule.log every other task's very first
    `echo` failed with "the process cannot access the file because it is being
    used by another process" — the line vanished, and because that echo is the
    batch's last statement, THE TASK REPORTED FAILURE. A 2h21m treat run
    therefore made both other tasks log nothing and return 1, which reads as
    two broken jobs and was three healthy ones fighting over a file.

    It also makes the logs readable: "what did treatment do" no longer means
    reading it interleaved with every filtered comment the scraper printed.
    """
    return repo_root() / "data" / f"schedule-{command or 'cycle'}.log"


#: Where a log stops growing, and how many rotations are kept.
#:
#: A scheduled command appends forever and nothing ever trims it: a 30-minute
#: job that prints a few hundred lines a tick is a few hundred megabytes a
#: year, on the same disk as the archive it is backing up. Five megabytes is
#: roughly a week of ticks for the noisiest command, and three rotations keep
#: the fortnight the backup retention already promises.
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_ROTATIONS = 3

#: The batch label the rotation lives under. Named once so the guard and the
#: body cannot drift apart.
ROTATE_LABEL = "jester_rotate_log"


def rotation_guard(log: Path | str, *, limit: int = MAX_LOG_BYTES) -> str:
    """The one line in a `.cmd` launcher that starts a rotation.

    `%%~zA` is the file's size in bytes. The `if exist` guard matters: with no
    log yet, `%%~zA` expands to nothing and `if` fails with a syntax error the
    operator would read as "the task did not run".

    The label it calls is written by `rotation_body`, at the END of the file.
    It cannot sit next to this line: batch runs top to bottom, so a label
    placed here would be fallen into after the call returned and would rotate
    the log a second time, then `exit /b 0` the whole task before it ran.
    """
    log = str(log)
    return (f'if exist "{log}" for %%A in ("{log}") do '
            f"if %%~zA GEQ {int(limit)} call :{ROTATE_LABEL}")


def rotation_body(log: Path | str, *, keep: int = LOG_ROTATIONS) -> str:
    """The rotation itself, as the labelled tail of a `.cmd` launcher."""
    log = str(log)
    lines = [f":{ROTATE_LABEL}", f'del /q "{log}.{keep}" 2>nul']
    for n in range(keep - 1, 0, -1):
        lines.append(f'if exist "{log}.{n}" move /y "{log}.{n}" "{log}.{n + 1}" >nul')
    lines.append(f'move /y "{log}" "{log}.1" >nul')
    lines.append("exit /b 0")
    return "\r\n".join(lines)


def posix_rotation(log: Path | str, *, keep: int = LOG_ROTATIONS,
                   limit: int = MAX_LOG_BYTES) -> str:
    """The same rotation as one POSIX shell fragment.

    `wc -c` rather than `stat`, because `stat` has two incompatible syntaxes
    and cron runs whatever `sh` the host has. Every step is guarded: a missing
    rotation is not an error, or the first tick of the month would exit 1.
    """
    log = shlex.quote(str(log))
    steps = [f"rm -f {log}.{keep}"]
    for n in range(keep - 1, 0, -1):
        steps.append(f"[ -f {log}.{n} ] && mv -f {log}.{n} {log}.{n + 1}")
    steps.append(f"mv -f {log} {log}.1")
    return (f'if [ -f {log} ] && [ "$(wc -c < {log})" -ge {int(limit)} ]; then '
            + "; ".join(steps) + "; fi")


def launcher_script(db, config, python, command="cycle", extra=()) -> str:
    """The batch file the scheduled task actually runs.

    Not an inline command: `schtasks /tr` refuses anything over 261 characters,
    and this repo's own path appears four times in the obvious one-liner. The
    daily install this module has shipped for months silently exceeded that on
    any path longer than a few segments, so it could not be registered at all.

    A file also means the operator can read exactly what the schedule runs, and
    the pipeline's own stdout lands in a log rather than being swallowed —
    schtasks records an exit code and nothing else.
    """
    root = repo_root()
    py = python or sys.executable
    args = "".join(f' "{a}"' for a in extra)
    cfg = f' --config "{config}"' if command_takes_config(command) else ""
    log = log_path(command)
    task = TASK_FOR_COMMAND.get(command, command)
    return (
        "@echo off\r\n"
        "REM Generated by `jester schedule install`. Edits are overwritten on\r\n"
        "REM the next install; change the schedule with the CLI instead.\r\n"
        # What this file's FORMAT is, so a host that installed an older one can
        # be told to rewrite it even when the command it runs never changed.
        f"REM jester-launcher-version: {LAUNCHER_VERSION}\r\n"
        f'set "PYTHONPATH={root / "python"}"\r\n'
        # `origin` separates automated runs from operator-triggered ones in the
        # ledger, and _duration_fields keys its rolling duration baseline off
        # it — filing scheduled runs as "manual" blends two populations whose
        # durations have no reason to match.
        'set "JESTER_ORIGIN=scheduled"\r\n'
        f'cd /d "{root}"\r\n'
        # REDIRECT FIRST, and it is not a style choice. Written the natural way
        # — `echo ... %ERRORLEVEL%>>"log"` — cmd parses the digit immediately
        # before `>>` as a FILE HANDLE, so `1>>` redirects stdout instead of
        # printing the code. Every single-digit exit code this scheduler has
        # ever produced was swallowed that way; the only one that ever reached
        # the log was 9009, because four digits are not a handle number.
        # Verified: the natural form writes "exited " and this form writes
        # "exited 7".
        #
        # Rotate BEFORE the first append, or the run that pushes the log over
        # the bound is the one that keeps growing it: nothing ever trimmed a
        # scheduled log, so a noisy tick was a few hundred megabytes a year on
        # the same disk as the archive it backs up.
        f"{rotation_guard(log)}\r\n"
        f'>>"{log}" echo [%DATE% %TIME%] jester {command} starting\r\n'
        # -u, because this stdout goes to a FILE. Python block-buffers a
        # redirected stream, so a `treat` run that spends an hour in synthesis
        # — which is the normal pace when nearly every Groq call comes back 429
        # and waits out the retry cap — writes nothing to the log until it
        # exits. From outside, a healthy long run and a hung one look identical,
        # and the console only says "running". One flag makes the difference
        # between the two visible while it is still happening.
        f'"{py}" -u -m jester.cli {argv_text(command)} '
        f'--db "{db}"{cfg}{args} '
        f'>>"{log}" 2>&1\r\n'
        # Captured once, because the echo below is itself a command and the
        # next `%ERRORLEVEL%` is not the run's.
        'set "RC=%ERRORLEVEL%"\r\n'
        f'>>"{log}" echo [%DATE% %TIME%] jester {command} exited %RC%\r\n'
        # A scheduled failure used to die in this log: schtasks records a code
        # and nothing reads it. The hook is best-effort by contract (notify
        # always exits 0), and `exit /b %RC%` keeps the run's own code even
        # when the hook ran.
        f'if not "%RC%"=="0" "{py}" -m jester.cli notify --task "{task}" '
        f'--exit "%RC%" >>"{log}" 2>&1\r\n'
        'exit /b %RC%\r\n'
        # The rotation's labelled tail. It has to be the LAST thing in the
        # file: batch executes top to bottom, so a label written next to the
        # `call` above would be fallen into when the call returned — rotating
        # the log a second time and then `exit /b 0`, which would report every
        # scheduled run as a success. `call :label` reaches past `exit /b`.
        f"{rotation_body(log)}\r\n"
    )


def argv_text(command: str) -> str:
    """The command as the launcher writes it. An argument with a space
    ("machine learning") is quoted, or cmd hands argparse two words and the
    task exits 2 on every run."""
    return " ".join(f'"{a}"' if " " in a else a for a in command_argv(command))


_LAUNCH = " -u -m jester.cli "


def read_launcher(command: str) -> dict | None:
    """What an installed launcher actually runs, parsed back out of the file.

    None when there is no launcher. `argv` is the command as written; `db`,
    `config`, `python` and `extra` (the scrape flags) are the operator's own
    settings, kept so a refresh can rewrite the file without changing them.
    `version` is the format marker -- None for a launcher written before it
    existed, which is how an old file with a current command is recognised.
    """
    path = launcher_path(command)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = LAUNCHER_VERSION_RE.search(text)
    line = next((ln for ln in text.splitlines() if _LAUNCH in ln), "")
    body = line.split(">>", 1)[0].strip()
    head, _, rest = body.partition(_LAUNCH)
    argv, _, tail = rest.partition(' --db "')
    db, _, after = tail.partition('"')
    tokens = [t.strip('"') for t in shlex.split(after, posix=False)]
    config = ""
    if tokens[:1] == ["--config"] and len(tokens) > 1:
        config, tokens = tokens[1], tokens[2:]
    return {"command": command, "path": str(path), "python": head.strip().strip('"'),
            "argv": argv.strip(), "db": db, "config": config, "extra": tokens,
            "version": int(found.group(1)) if found else None}


def launcher_drift(command: str) -> dict | None:
    """Does the installed launcher still run what this code would write?

    The failure it exists for, on 2026-09-28: the hiring task's launcher had
    been written before the job-board fallback and the Reddit step were added,
    so the 09:30 run read one thread and stopped -- for days, exit code 0,
    while the console's button (which builds its command fresh) did the whole
    chain. A launcher is a snapshot of the code that wrote it, and nothing
    compared the two.

    Only the command and the file format are compared. The database, config and
    scrape flags are the operator's settings, not the code's; the version is
    the file's, and a launcher written before a format change runs an old shape
    even when its command is unchanged.
    """
    have = read_launcher(command)
    if have is None:
        return None
    want = argv_text(command)
    return {**have, "want": want, "want_version": LAUNCHER_VERSION,
            "stale": have["argv"] != want or have["version"] != LAUNCHER_VERSION}


def stale_launchers(installed_only: bool = True) -> list:
    """Every launcher whose command no longer matches the code.

    A launcher file with no task behind it runs nothing, so by default only
    installed tasks are reported -- a leftover file is not a missed run.
    """
    out = []
    for command in COMMAND_SPEC:
        drift = launcher_drift(command)
        if not drift or not drift["stale"]:
            continue
        if installed_only and not status(TASK_FOR_COMMAND.get(command, TASK_NAME)).get("installed"):
            continue
        out.append(drift)
    return out


def refresh_launchers(commands=None) -> list:
    """Rewrite stale launchers from the current code, keeping each one's
    database, config, python and scrape flags. The task registration is not
    touched: it points at the file, and the file is what changes."""
    refreshed = []
    for drift in stale_launchers(installed_only=False):
        if commands and drift["command"] not in commands:
            continue
        write_launcher(drift["db"], drift["config"] or "config", drift["python"] or None,
                       command=drift["command"], extra=drift["extra"])
        refreshed.append({"command": drift["command"], "was": drift["argv"], "now": drift["want"]})
    return refreshed


def write_launcher(db, config, python, command="cycle", extra=()) -> Path:
    """Write the launcher, atomically.

    cmd.exe reads a batch file by BYTE OFFSET as it executes, re-opening it at
    each line. Rewriting one in place while an instance is running therefore
    resumes it at an offset into different content — observed live as
    `jester treat exited 9009`, cmd's "command not found", after a launcher was
    regenerated 33 minutes into a 2h21m run.

    Writing to a sibling file and replacing keeps the running instance on the
    file it started with. On Windows the replace fails outright if the target
    is held, which is the right outcome too: a loud error beats corrupting a
    run that is hours in.
    """
    path = launcher_path(command)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".new")
    # newline="" so the \r\n written above survive verbatim; cmd.exe needs CRLF.
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(launcher_script(db, config, python, command, extra))
    try:
        os.replace(tmp, path)
    except OSError:
        # Windows refuses to replace a file another process holds. That is a
        # running instance of this very launcher, and clobbering it is the
        # failure this function exists to avoid — so the temp file is cleaned
        # up and the caller is told, rather than the write half-happening.
        tmp.unlink(missing_ok=True)
        raise
    return path


#: The three Task Scheduler settings that decide whether a scheduled run on a
#: laptop actually happens. All three default the wrong way for this, and
#: `schtasks /create` has no switch for any of them — they need task XML or
#: PowerShell, which is why every task this module has ever registered carried
#: the defaults.
#:
#: Read off this machine's own JesterNightly task before the fix:
#:
#:     StartWhenAvailable         : False   <- a slot missed while asleep is
#:                                            never retried
#:     DisallowStartIfOnBatteries : True    <- the run is refused on battery
#:     StopIfGoingOnBatteries     : True    <- unplugging kills a run
#:
#: So a nightly job on an unplugged or sleeping laptop produces nothing, and
#: produces it silently: no error, no row, no notification. For a milestone
#: that asks for "three scheduled daily runs", that is the exact failure the
#: schedule exists to rule out.
#:
#: WakeToRun is deliberately NOT set. Waking someone's laptop at 03:00 to
#: scrape a forum is a decision for the person who owns the laptop, and
#: StartWhenAvailable already covers the missed slot by running late.
_POWER_SETTINGS_PS = (
    "$ErrorActionPreference='Stop';"
    "$s = New-ScheduledTaskSettingsSet"
    " -StartWhenAvailable"
    " -AllowStartIfOnBatteries"
    " -DontStopIfGoingOnBatteries"
    " -MultipleInstances IgnoreNew;"
    "Set-ScheduledTask -TaskName '{task}' -Settings $s | Out-Null"
)


def apply_power_settings(task_name: str) -> dict:
    """Make a registered task survive a laptop. Best-effort by design.

    A task that runs on mains power and a woken machine is still a working
    task, so a failure here downgrades the install rather than failing it —
    but it is reported, because an operator who thinks the missed-run setting
    is on when it is not is worse off than one who knows it is off.
    """
    if not is_windows():
        return {"ok": True, "applied": False, "detail": "not Windows"}
    p = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
              _POWER_SETTINGS_PS.format(task=task_name)])
    if p.returncode != 0:
        return {"ok": False, "applied": False,
                "detail": (p.stderr or p.stdout).strip()[:200]}
    return {"ok": True, "applied": True,
            "detail": "runs when available, and on battery"}


def _windows_action(db, config, python, command="cycle", extra=()):
    """The argv schtasks registers: the generated launcher, nothing more."""
    return [str(write_launcher(db, config, python, command, extra))]


#: The two halves of the loop, and what each is bounded by.
#:
#:   ingest   cheap, polite, bounded by how often we knock on someone's door
#:   treat    one LLM call per comment, bounded by a quota on someone else's
#:            clock; exits in milliseconds when that quota is known to be gone
#:
#: `cycle` remains for anyone who wants them welded together.
TASK_FOR_COMMAND = {
    "cycle": TASK_NAME,
    "ingest": TASK_NAME + "Scrape",
    "treat": TASK_NAME + "Treat",
    "signals": TASK_NAME + "Signals",
    "signals-hiring": TASK_NAME + "SignalsHiring",
    "leads-reddit": TASK_NAME + "LeadsReddit",
    "signals-brief": TASK_NAME + "SignalsBrief",
    "signals-sweep": TASK_NAME + "SignalsSweep",
    # The answer to the morning question "is the collector alive?": a barren,
    # stale or erroring source (or a week without a run) exits 1, and the
    # launcher turns that into a notify call.
    "health": TASK_NAME + "Health",
    # Operational jobs are schedulable for the same reason as the scrapers:
    # a policy nobody runs is a policy that is not in force. Both are declared
    # in config/schedule.yaml; retention is declared PAUSED until the
    # evaluation sample exists.
    "backup": TASK_NAME + "Backup",
    # The monthly restore drill: open the newest backup in a temp directory,
    # run integrity_check and compare counts with its manifest. Declared
    # PAUSED until the owner picks the machine that runs it.
    "backup-drill": TASK_NAME + "BackupDrill",
    "retention": TASK_NAME + "Retention",
    # The signals archive's own window. A different sweep from `retention`
    # above, on a different database: that one minimises text in the idea
    # pipeline, this one deletes signal records past their last sighting.
    # Declared PAUSED in config/schedule.yaml until the owner picks a TTL.
    # The name the task card specifies, verbatim — it does not carry the
    # `JesterNightly` prefix the other entries share.
    "signals-retention": "JesterSignalsRetention",
    # The ClickHouse mirror of the signals archive. SQLite is the source of
    # truth, so this is a convenience rather than a pipeline step: the load is
    # idempotent (a ReplacingMergeTree keyed on the record id) and safe to skip
    # entirely. Declared PAUSED because it needs a running ClickHouse, which
    # the shipped stack only starts under the `signals` profile. The task name
    # is the one the documentation uses, verbatim -- like `signals-retention`
    # above, it does not carry the `JesterNightly` prefix.
    "signals-load": "JesterClickHouseLoad",
}

#: What each schedulable command expands to on the command line, and whether it
#: takes `--config`.
#:
#: `signals` needed both: it is a sub-command (`signals run`, not `signals`),
#: and it has no `--config` option, so the launcher's fixed
#: `--db X --config Y` shape would have made it exit 2 on every tick. A table
#: is cheaper than a branch in the launcher, and it is the place to look when
#: the next command does not fit the mould either.
#: The hiring rule set, which three of the commands below score with.
HIRING_RULES = "config/hiring_rules.yaml"

#: How often the Himalayas sweep runs. The command reads every term to the end
#: of its results (~300 requests) against a board that refreshes once a day, so
#: daily is seven times the work for the same answer. The declaration in
#: `config/schedule.yaml` is what the machine actually runs, and a contract
#: test compares it to this: one fact, not two that drift apart.
SWEEP_CADENCE = {"weekly": "SUN", "at": "08:00"}

COMMAND_SPEC = {
    "cycle": {"argv": ("cycle",), "config": True},
    "ingest": {"argv": ("ingest",), "config": True},
    "treat": {"argv": ("treat",), "config": True},
    "signals": {"argv": ("signals", "run"), "config": False},
    # Turn the Reddit hiring adverts the worker has ALREADY collected into
    # leads. Two pipelines existed and never met: the worker writes those rooms
    # into `nuggets`, which has no audience column, so adding the subreddits
    # moved no buyer count and it looked like they had produced nothing.
    #
    # This task collects nothing -- it scores rows already on disk. That is
    # deliberate and must stay true: the scope document gates Reddit
    # COLLECTION on written commercial access, and reading a stored row is not
    # collection. Scheduling it is safe for the same reason.
    "leads-reddit": {
        "argv": ("signals", "from-archive",
                 "--rules", HIRING_RULES,
                 "--archive", "data/jester.db"),
        "config": False,
    },
    # The forum collector and the hiring collector are two different questions
    # and two different rule sets, so they are two schedulable commands.
    #
    # `signals` alone has been the only one running, and it is the one MEASURED
    # not to find buyers: 569 rows collected over days produced 3 buyers, 0.5%.
    # The hiring collector was written precisely because companies state a
    # budget in "Who is hiring?" threads, and it had never been scheduled at
    # all — 400 adverts produced 26 buyers, 24 of them correct on inspection.
    # Same archive, same schema; `community` tells them apart.
    "signals-hiring": {
        # --query 2 --limit 1000, because the defaults were the whole problem.
        # `--limit` defaults to 100 and `--query` to 12 months, so the daily run
        # read the first 100 adverts of a year's worth of threads and stopped.
        # One month's thread alone carries 400-600. Collecting 3,000 in one
        # pass took the archive from 6 buyers to 154 — the rate was always
        # ~5%, we were just sampling a twentieth of the material.
        #
        # Two months daily: new adverts land in the current thread, and the
        # previous one is still being added to. Anything older is already in
        # the archive and dedupes on sight.
        #
        # --fallback, because two months of threads are exhausted a day after
        # they are read: between the 1st of one month and the next, every run
        # re-read the same ~490 adverts and stored none. The job boards post
        # daily and can be asked for contract work specifically; they are
        # walked in order until one adds something, each run noting why in the
        # ledger. Himalayas first: by far the largest contract pool measured.
        #
        # --also-reddit-archive scores the Reddit hiring rooms the Go worker
        # has already stored -- the leads-reddit step, folded in so one run
        # covers every hiring source. It makes no requests to Reddit.
        "argv": ("signals", "run",
                 "--source", "hackernews-hiring",
                 "--rules", HIRING_RULES,
                 "--query", "2", "--limit", "1000",
                 "--fallback", "himalayas,remotive,jobicy",
                 "--also-reddit-archive", "--archive", "data/jester.db"),
        "config": False,
    },
    # The buyer brief page, rebuilt from the archive after the morning runs,
    # so the list a person reads is never older than the leads behind it.
    # Reads the archive only: no requests to anyone.
    "signals-brief": {"argv": ("signals", "brief"), "config": False},
    # `health` takes no --config and no scrape scope: it reads the archive and
    # reports. Its non-zero exit is the point, so it is schedulable.
    "health": {"argv": ("health",), "config": False},
    # Himalayas read to the END of its results for the terms that found 95%
    # of the stored listings (developer and engineer alone are 74%). Only a
    # complete read can tell a closed listing from one further down, and the
    # daily chain reads 100 per term, so this is what marks filled roles
    # closed -- and it finds new listings on the way. ~300 requests: well
    # inside the 600/day budget, but not something to do daily to a board
    # whose data refreshes once a day.
    "signals-sweep": {
        "argv": ("signals", "run", "--source", "himalayas",
                 "--rules", HIRING_RULES,
                 "--query", "developer", "--query", "engineer", "--query", "data",
                 "--query", "qa", "--query", "python", "--query", "integration",
                 "--query", "machine learning", "--query", "ai",
                 "--query", "automation", "--query", "devops",
                 "--limit", "3000"),
        "config": False,
    },
    # `backup` takes no --config and verifies the snapshot it writes, so one
    # tick is both the copy and the proof that the copy opens.
    "backup": {"argv": ("backup",), "config": False},
    # The drill restores into a temp directory and reports; `backup drill`
    # takes no --config and needs no path, so it can run unattended.
    "backup-drill": {"argv": ("backup", "drill"), "config": False},
    # `retention` takes no --config; its TTL is the documented 30 days.
    "retention": {"argv": ("retention",), "config": False},
    # `signals retention` is a sub-command and takes no --config, and its
    # window is fixed here rather than in `options`: `options` are the scrape
    # ceilings (`SCRAPE_OPTION_FLAGS`) and nothing else. 365 days, not the
    # nugget pipeline's 30: a record is a 600-character excerpt, but the row
    # is still the only evidence that a post said what it said.
    "signals-retention": {"argv": ("signals", "retention", "--days", "365"),
                          "config": False},
    # `signals load` is a sub-command, so it takes no `--config` for the same
    # reason as the two above -- the launcher's fixed `--config` tail would
    # make it exit 2 on every tick. `--db` is still appended by the launcher,
    # and it is the archive the load reads.
    "signals-load": {"argv": ("signals", "load"), "config": False},
}


def command_argv(command: str):
    """The argv tail for a command, without the `--db` / `--config` pair."""
    return COMMAND_SPEC.get(command, {"argv": (command,)})["argv"]


def command_takes_config(command: str) -> bool:
    return COMMAND_SPEC.get(command, {"config": True}).get("config", True)


def humanise_repeat(raw: str) -> str:
    """schtasks says "0 Hour(s), 30 Minute(s)"; a person says "every 30 minutes".

    Falls back to the raw string rather than guessing, so an unrecognised
    format still tells the operator something true.
    """
    m = re.search(r"(\d+)\s*Hour", raw or "", re.I)
    hours = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s*Minute", raw or "", re.I)
    minutes = int(m.group(1)) if m else 0
    total = hours * 60 + minutes
    if total <= 0:
        return (raw or "").strip()
    if total % 60 == 0:
        n = total // 60
        return f"every {n} hour" + ("s" if n != 1 else "")
    return f"every {total} minute" + ("s" if total != 1 else "")


def interval_minutes(fields: dict):
    """The registered cadence in minutes, or None for a daily/unknown trigger.

    Lets the console say what a window SHOULD have produced. "4 runs in the
    last 2 hours" only means something next to "every 30 minutes"; on its own
    it cannot distinguish a healthy schedule from one firing once a night.
    """
    raw = fields.get("repeat: every", "")
    m = re.search(r"(\d+)\s*Hour", raw or "", re.I)
    hours = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s*Minute", raw or "", re.I)
    mins = int(m.group(1)) if m else 0
    total = hours * 60 + mins
    if total > 0:
        return total
    # A daily trigger repeats once every 24h; that is still a cadence. So is
    # a weekly one (one day named), and a monthly one (a 30-day window).
    kind = (fields.get("schedule type") or "").strip().lower()
    if kind.startswith("daily"):
        return 24 * 60
    if kind.startswith("monthly"):
        return 30 * 24 * 60
    if kind.startswith("weekly"):
        days = [d for d in re.split(r"[,\s]+", fields.get("days") or "") if d]
        return 7 * 24 * 60 // max(1, len(days))
    return None


#: Labels whose NAME contains a colon. `"Repeat: Every:  0 Hour(s), 30
#: Minute(s)"` splits at the first colon into ("Repeat", "Every: 0 Hour…"),
#: so a naive parse loses the one field that carries an interval cadence.
_COLON_LABELS = ("repeat: every", "repeat: until: time", "repeat: until: duration")


def parse_query_fields(detail: str) -> dict:
    """schtasks' verbose LIST output as a lowercase-keyed dict."""
    fields = {}
    for line in (detail or "").splitlines():
        low = line.lower().strip()
        for label in _COLON_LABELS:
            if low.startswith(label):
                fields.setdefault(
                    label, line.strip()[len(label) :].lstrip(": ").strip()
                )
                break
        else:
            key, sep, value = line.partition(":")
            if sep:
                fields.setdefault(key.strip().lower(), value.strip())
    return fields


def _cadence_from(fields: dict) -> str:
    """Describe the trigger from schtasks' verbose fields.

    The two shapes need different fields, and each puts junk in the other's:
    a repeating task reports `Schedule Type: One Time Only, Minute` and keeps
    the real cadence in `Repeat: Every`; a daily task reports
    `Repeat: Every: Disabled` and keeps it in `Schedule Type` + `Start Time`.
    Reading one field for both gives "Disabled" for a healthy daily job, and
    "one time only, minute" for a job that runs every half hour.
    """
    repeat = humanise_repeat(fields.get("repeat: every", ""))
    if repeat and repeat.startswith("every "):
        return repeat
    kind = (fields.get("schedule type") or "").strip()
    start = (fields.get("start time") or "").strip()
    days = (fields.get("days") or "").strip()
    if kind.lower().startswith("weekly") and days and start:
        return f"weekly on {days} at {start}"
    if kind.lower().startswith("monthly") and days and start:
        return f"monthly on day {days} at {start}"
    if kind and start:
        return f"{kind.lower()} at {start}"
    return kind or repeat or ""


def status(task_name: str = TASK_NAME) -> dict:
    """Is a nightly job actually registered? Reports the truth, not a guess."""
    if is_windows():
        # /v: the plain LIST view carries TaskName, Next Run Time and
        # Status only. The cadence — the one thing someone asking about a
        # schedule wants — is verbose-only.
        try:
            p = _run(["schtasks", "/query", "/tn", task_name, "/fo", "LIST", "/v"])
        except FileNotFoundError:
            # A POSIX image running under Wine, or a stripped Windows host
            # without schtasks — report uninstalled rather than crashing.
            return {
                "installed": False,
                "scheduler": "schtasks",
                "detail": "schtasks not available on this host",
            }
        if p.returncode != 0:
            return {"installed": False, "scheduler": "schtasks", "detail": ""}
        detail = p.stdout.strip()
        fields = parse_query_fields(detail)
        # Registered is not the same as live. A task switched off in Task
        # Scheduler (or by `set_enabled`) still answers /query, so reading
        # "installed" alone reported a paused job as a working schedule.
        state = (fields.get("scheduled task state") or "").strip().lower()
        return {
            "installed": True,
            "enabled": state != "disabled",
            "running": (fields.get("status") or "").strip().lower() == "running",
            "scheduler": "schtasks",
            "next_run": fields.get("next run time", ""),
            "last_run": fields.get("last run time", ""),
            "last_result": fields.get("last result", ""),
            "cadence": _cadence_from(fields),
            "start_time": fields.get("start time", ""),
            "interval_minutes": interval_minutes(fields),
            "detail": detail,
        }
    try:
        p = _run(["crontab", "-l"])
    except FileNotFoundError:
        # Minimal/container images (e.g. python:slim) ship no crontab. The
        # schedule simply cannot be registered here — say so instead of 500ing.
        return {
            "installed": False,
            "scheduler": "cron",
            "detail": "crontab not available on this host",
        }
    # Match either verb: an older install registered `run`, and reporting that
    # as "not installed" would send someone to add a second, duplicate line.
    out = p.stdout or ""
    installed = p.returncode == 0 and (
        "jester.cli cycle" in out or "jester.cli run" in out
    )
    return {"installed": installed, "scheduler": "cron", "detail": out.strip()}


#: The knobs that decide WHAT a scheduled tick fetches and HOW DEEP it goes,
#: in the order the cycle applies them. Every one is already a `jester cycle`
#: flag — the scheduler simply had no way to pass them, so an unattended run
#: always meant "every enabled source, no ceiling", which is the one shape you
#: least want firing every half hour against a rate-limited API.
SCRAPE_OPTION_FLAGS = (
    ("only", "--only"),  # comma-joined source names
    ("max_posts", "--max-posts"),  # threads/videos/topics per tick
    ("max_comments", "--max-comments"),  # comments per tick
    ("max_ideas", "--max-ideas"),  # new ideas synthesised per tick
)


def scrape_flags(options) -> list:
    """Turn a scope dict into `jester cycle` flags.

    Absent, empty and zero all mean "no limit" and produce no flag at all —
    passing `--max-posts 0` would be a ceiling of zero, which is a run that
    fetches nothing.
    """
    options = options or {}
    out = []
    for key, flag in SCRAPE_OPTION_FLAGS:
        value = options.get(key)
        if key == "only":
            names = value
            if isinstance(names, str):
                names = names.split(",")
            names = [str(n).strip() for n in (names or []) if str(n).strip()]
            if names:
                out += [flag, ",".join(names)]
            continue
        if value in (None, "", 0, "0"):
            continue
        out += [flag, str(int(value))]
    return out


def parse_scrape_flags(argv) -> dict:
    """Recover a scope dict from a flag list — the inverse of `scrape_flags`.

    Lets the console show what the REGISTERED task will actually do rather
    than what someone last typed into the form. Those differ the moment a
    schedule is installed from the CLI, or edited by hand.
    """
    opts = {}
    tokens = list(argv or [])
    by_flag = {flag: key for key, flag in SCRAPE_OPTION_FLAGS}
    i = 0
    while i < len(tokens):
        tok = str(tokens[i]).strip().strip('"')
        key = by_flag.get(tok)
        if key and i + 1 < len(tokens):
            raw = str(tokens[i + 1]).strip().strip('"')
            if key == "only":
                opts[key] = [n for n in (x.strip() for x in raw.split(",")) if n]
            else:
                try:
                    opts[key] = int(raw)
                except ValueError:
                    pass
            i += 2
            continue
        i += 1
    return opts


def _read_crontab() -> list:
    """The user's crontab as a list of lines ([] when none is installed)."""
    try:
        p = _run(["crontab", "-l"])
    except FileNotFoundError:
        return []
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]


def _write_crontab(lines: list) -> "subprocess.CompletedProcess":
    """Replace the user's crontab. An empty list clears it (via `crontab -r`,
    since `crontab -` with empty stdin is unreliable on Debian)."""
    import subprocess

    if not lines:
        # Non-zero when there is no crontab to remove; that is still "cleared".
        return subprocess.run(
            ["crontab", "-r"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        ["crontab", "-"],
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _jester_cron_lines(command: str | None = None) -> tuple:
    """Substrings that identify the crontab lines this generator owns.

    Install replaces only the same command's own line. Broadening the needle
    to `("jester.cli ",)` made `install --command health` delete a previously
    installed `signals run` line, which is one scheduled job removing another.
    The tail is the command's argv as `cron_line` writes it, followed by the
    fixed `--db` every generated line carries: `signals` and `signals-hiring`
    both start `jester.cli signals run`, so the argv alone would let installing
    one replace the other.

    `None` (removal) keeps the breadth: "remove the jester schedule" clears
    every line naming this module, ours or hand-written. That is the one place
    the breadth is the point, and it is a delete the operator asked for.
    """
    if command is None:
        return ("jester.cli",)
    # The tail is written by `shlex.join` in `cron_line`, so a command whose
    # scope carries a two-word argument ("machine learning") is quoted here
    # exactly as it is there. `" ".join` stopped matching that line the moment
    # quoting was fixed, and an unmatched needle makes install APPEND a second
    # schedule instead of replacing the first.
    return (f"jester.cli {shlex.join(command_argv(command))} --db ",)


def _line_is_ours(line: str, needles: tuple) -> bool:
    """True when a needle appears in `line` ending at a word boundary, so
    `jester.cli signals run` does not claim the `... --source hackernews-hiring`
    line that `signals-hiring` owns. A needle that already ends in a space is
    its own boundary."""
    for needle in needles:
        start = line.find(needle)
        while start != -1:
            end = start + len(needle)
            if (needle[-1] in " \t" or end == len(line)
                    or line[end] in " \t"):
                return True
            start = line.find(needle, start + 1)
    return False


def installed_options(task_name: str = TASK_NAME, command: str = "cycle") -> dict:
    """What the registered task is actually configured to scrape.

    Read out of the generated launcher, which is the file the scheduler runs —
    the single honest answer. Returns {} when nothing is installed.
    """
    path = launcher_path(command)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        # `signals` registers as `signals run`, so match the argv tail rather
        # than the bare key — otherwise a signals line would never be found
        # and `status` would report it uninstalled while it ran every night.
        if f"jester.cli {' '.join(command_argv(command))}" not in line:
            continue
        # The launcher quotes every argument it interpolates, and the trailing
        # redirect is not an argument — cut it before splitting.
        body = line.split(">>", 1)[0]
        return parse_scrape_flags(shlex.split(body, posix=False))
    return {}


#: Days as schtasks names them (`/d SUN`), with cron's day-of-week number.
#: Weekly exists for work that is worth doing but not every day -- the full
#: Himalayas sweep reads ~300 pages of a board whose data refreshes daily.
WEEKDAYS = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6, "SUN": 0}


def weekday(value: str) -> str:
    """"sun", "Sunday" or "SUN" -> "SUN"; anything else is refused by name."""
    key = (value or "").strip().upper()[:3]
    if key not in WEEKDAYS:
        raise ValueError(f"not a day of the week: {value!r}; use MON, TUE ... SUN")
    return key


def month_day(value) -> int:
    """A day of the month, 1-28.

    29-31 is refused rather than accepted and silently skipped in February:
    "monthly on the 31st" is a schedule that does not run six months a year,
    and Task Scheduler and cron both express it without warning.
    """
    try:
        day = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"not a day of the month: {value!r}; use 1-28") from None
    if not 1 <= day <= 28:
        raise ValueError(
            f"monthly day must be 1-28, got {day}: later days do not exist in "
            "every month")
    return day


def command_line_argv(
    db: str = "data/jester.db",
    config: str = "config",
    python: str | None = None,
    extra=(),
    command: str = "cycle",
) -> list:
    """The exact argv a POSIX schedule runs, one token per argument.

    A list rather than a string, because `cron_line` builds the line with
    `shlex.join`. The plain `" ".join` it replaced flattened
    `--query "machine learning"` into two words: argparse then read
    `learning` as a stray argument and the sweep tick exited 2 every week —
    the exact failure a quoting test is for.
    """
    argv = [python or sys.executable, "-u", "-m", "jester.cli",
            *command_argv(command), "--db", str(db)]
    if command_takes_config(command):
        argv += ["--config", str(config)]
    return argv + [str(a) for a in extra]


def cron_line(
    at: str = DEFAULT_TIME,
    db: str = "data/jester.db",
    config: str = "config",
    python: str | None = None,
    every: int | None = None,
    extra=(),
    command: str = "cycle",
    weekly: str | None = None,
    monthly: int | None = None,
) -> str:
    """The crontab line to install. `every` gives an every-N-minutes cadence;
    otherwise it is daily at `at`, weekly on `weekly`, or monthly on day
    `monthly`. `extra` carries the scrape scope, which the POSIX path used to
    drop on the floor — a crontab line that quietly walks every source is not
    the schedule the operator configured."""
    root = repo_root()
    py = python or sys.executable
    if every:
        n = int(every)
        if n < 1:
            raise ValueError("interval must be at least 1 minute")
        if n < 60:
            when = f"*/{n} * * * *"
        elif n % 60 == 0 and n // 60 < 24:
            when = f"0 */{n // 60} * * *"
        else:
            # cron cannot express "every 90 minutes" in one field without
            # lying about it, so say so rather than silently rounding.
            raise ValueError(
                f"cron cannot express every {n} minutes; use a divisor of 60, "
                "or a whole number of hours under 24"
            )
    else:
        hh, _, mm = at.partition(":")
        if monthly is not None:
            when = f"{int(mm or 0)} {int(hh)} {month_day(monthly)} * *"
        else:
            dow = str(WEEKDAYS[weekday(weekly)]) if weekly else "*"
            when = f"{int(mm or 0)} {int(hh)} * * {dow}"
    # Every interpolated token goes through shlex, so the line round-trips
    # through `shlex.split` to the argv above. A repo path with a space
    # ("D:\\project in this pc\\...") broke the old line twice: cron's sh saw
    # `cd D:\project` and the assignment saw `PYTHONPATH=D:\project`.
    log_at = log_path(command)
    log = shlex.quote(str(log_at))
    env = f"PYTHONPATH={shlex.quote(str(root / 'python'))}"
    run = shlex.join(command_line_argv(db, config, py, extra, command))
    task = shlex.quote(TASK_FOR_COMMAND.get(command, command))
    # One crontab line has no room for an `if` block that reads well, but it
    # does have room for the same contract as the .cmd launcher: keep the
    # code, and turn a non-zero one into a notify call. `exit "$rc"` is what
    # tells cron (and anyone reading the log) what really happened; without it
    # the last command in the line decides.
    #
    # The rotation runs FIRST and is the POSIX twin of the launcher's guard,
    # so both shapes of the same schedule bound their log the same way. Each
    # step inside it is guarded: the first tick of the month has no log yet,
    # and an unguarded `mv` would exit 1 through the notify branch.
    return (
        f"{when}  {posix_rotation(log_at)}; "
        f"cd {shlex.quote(str(root))} && {env} {run} "
        f">> {log} 2>&1; rc=$?; "
        f'if [ "$rc" -ne 0 ]; then {env} {shlex.quote(str(py))} -m jester.cli notify '
        f"--task {task} --exit \"$rc\" >> {log} 2>&1; fi; exit \"$rc\""
    )


def install(
    at: str = DEFAULT_TIME,
    db: str = "data/jester.db",
    config: str = "config",
    task_name: str = TASK_NAME,
    python: str | None = None,
    every: int | None = None,
    extra=(),
    command: str = "cycle",
    weekly: str | None = None,
    monthly: int | None = None,
    run_as: str = "",
) -> dict:
    """Register the scrape+process job. Returns {ok, action, detail}.

    `every` schedules every N minutes; `weekly` ("SUN") once a week at `at`;
    `monthly` (1-28) once a month at `at`; with none of them the job runs
    daily at `at`. Exactly one cadence.
    Either way the registered command is `jester cycle` — fetch AND process.
    Registering `run` was the original bug: the job fired on time for months
    and archived nothing, because nothing had queued anything for it to read.

    `run_as` is an account name for `/ru`, and is EMPTY by default. It is not
    defaulted to SYSTEM: a nightly scrape that reads HTTP and writes one SQLite
    file has no business holding a SYSTEM token, and `schtasks` would accept
    that silently. When an account is named, `schtasks` prompts for its
    password — which is why this is an explicit, interactive act rather than a
    default.
    """
    chosen = [name for name, present in (
        ("every", every is not None), ("weekly", bool(weekly)),
        ("monthly", monthly is not None)) if present]
    if len(chosen) > 1:
        return {"ok": False, "action": "create",
                "detail": "choose one cadence: --every N minutes, --weekly DAY, "
                          "or --monthly DAY"}
    if every is not None:
        try:
            sc, mo = interval_schedule(every)
        except ValueError as exc:
            return {"ok": False, "action": "create", "detail": str(exc)}
    if weekly:
        try:
            weekly = weekday(weekly)
        except ValueError as exc:
            return {"ok": False, "action": "create", "detail": str(exc)}
    if monthly is not None:
        try:
            month_day(monthly)
        except ValueError as exc:
            return {"ok": False, "action": "create", "detail": str(exc)}
    if not is_windows():
        try:
            line = cron_line(at, db, config, python, every=every, extra=extra,
                             command=command, weekly=weekly, monthly=monthly)
        except ValueError as exc:
            return {"ok": False, "action": "manual", "detail": str(exc)}
        try:
            current = _read_crontab()
            needle = _jester_cron_lines(command)
            # Drop this command's prior entry so re-installing does not
            # duplicate -- and only this command's, or installing one job
            # would unschedule the others.
            current = [ln for ln in current if not _line_is_ours(ln, needle)]
            current.append(line)
            p = _write_crontab(current)
        except FileNotFoundError:
            return {
                "ok": False,
                "action": "manual",
                "detail": "crontab not available on this host",
            }
        if p.returncode != 0:
            return {
                "ok": False,
                "action": "create",
                "detail": (p.stderr or p.stdout).strip(),
            }
        return {"ok": True, "action": "create", "detail": f"scheduled: {line}"}
    action = _windows_action(db, config, python, command=command, extra=extra)
    # schtasks takes the whole command as one string; quote the inner argv.
    tr = subprocess.list2cmdline(action)
    cmd = ["schtasks", "/create", "/tn", task_name, "/tr", tr]
    # /rl LIMITED on EVERY task: without it schtasks registers at HIGHEST, so a
    # job that fetches a forum and appends to a log ran with the same token as
    # an elevated shell. Nothing this module schedules needs more.
    cmd += ["/rl", "LIMITED"]
    if run_as:
        cmd += ["/ru", run_as]
    if every is not None:
        # Task Scheduler's default multiple-instances policy is IgnoreNew, so
        # a tick that overruns its interval is skipped rather than doubled.
        # `cycle` guards this itself too, for the cron path that has no such
        # policy.
        cmd += ["/sc", sc, "/mo", str(mo)]
        cadence = f"every {mo} minute(s)" if sc == "MINUTE" else f"every {mo} hour(s)"
    elif weekly:
        cmd += ["/sc", "WEEKLY", "/d", weekly, "/st", at]
        cadence = f"weekly on {weekly} at {at}"
    elif monthly is not None:
        # 1-28 only: schtasks accepts 29-31 and then skips the months that
        # lack the day, which is a schedule that silently does not run.
        day = month_day(monthly)
        cmd += ["/sc", "MONTHLY", "/d", str(day), "/st", at]
        cadence = f"monthly on day {day} at {at}"
    else:
        cmd += ["/sc", "DAILY", "/st", at]
        cadence = f"daily at {at}"
    p = _run(cmd + ["/f"])
    if p.returncode != 0:
        return {
            "ok": False,
            "action": "create",
            "detail": (p.stderr or p.stdout).strip(),
        }
    # schtasks cannot set these, so they are applied to the task it just made.
    power = apply_power_settings(task_name)
    what = {
        "cycle": "jester cycle: ingest + pipeline",
        "ingest": "jester ingest: scrape only, no LLM",
        "treat": "jester treat: drain the queue while the quota lasts",
    }.get(command, f"jester {command}")
    detail = f"{task_name} runs {cadence} ({what})"
    if power.get("applied"):
        detail += f"; {power['detail']}"
    elif not power.get("ok"):
        # Named, not swallowed: the task works on mains power either way, but
        # an operator who believes the missed-run setting is on when it is not
        # will read an empty day as an empty internet.
        detail += (f"; WARNING could not set power/missed-run options "
                   f"({power.get('detail', 'unknown')}) — a run missed while "
                   "asleep or on battery will not catch up")
    return {
        "ok": True,
        "action": "create",
        "detail": detail,
        "power": power,
    }


def remove(task_name: str = TASK_NAME) -> dict:
    if not is_windows():
        try:
            needle = _jester_cron_lines()
            current = [ln for ln in _read_crontab() if not _line_is_ours(ln, needle)]
            # No jester lines left: clearing an already-absent crontab is fine.
            if not current:
                _write_crontab([])
                return {
                    "ok": True,
                    "action": "delete",
                    "detail": f"{task_name} removed from crontab",
                }
            p = _write_crontab(current)
        except FileNotFoundError:
            return {
                "ok": False,
                "action": "manual",
                "detail": "crontab not available on this host",
            }
        if p.returncode != 0:
            return {
                "ok": False,
                "action": "delete",
                "detail": (p.stderr or p.stdout).strip(),
            }
        return {
            "ok": True,
            "action": "delete",
            "detail": f"{task_name} removed from crontab",
        }
    p = _run(["schtasks", "/delete", "/tn", task_name, "/f"])
    if p.returncode != 0:
        return {
            "ok": False,
            "action": "delete",
            "detail": (p.stderr or p.stdout).strip(),
        }
    return {"ok": True, "action": "delete", "detail": f"{task_name} removed"}


def set_enabled(task_name: str, enabled: bool) -> dict:
    """Switch a registered task on or off without unregistering it.

    Off is a pause, not a delete: the cadence and the launcher stay exactly as
    they were, so switching it back on restores the schedule someone chose
    rather than a default. Windows only -- cron has no disabled state, and
    commenting lines in and out of a user's crontab is how schedules get lost.
    """
    if not is_windows():
        return {"ok": False, "action": "manual",
                "detail": "pausing a task needs Windows Task Scheduler; "
                          "on this host use `jester schedule remove`"}
    p = _run(["schtasks", "/change", "/tn", task_name,
              "/enable" if enabled else "/disable"])
    if p.returncode != 0:
        return {"ok": False, "action": "change",
                "detail": (p.stderr or p.stdout).strip()}
    return {"ok": True, "action": "change",
            "detail": f"{task_name} {'enabled' if enabled else 'disabled'}"}


def run_now(task_name: str = TASK_NAME) -> dict:
    """Trigger the registered job immediately — proves it actually fires."""
    if not is_windows():
        # In a container without schtasks, just run the cycle directly.
        cmd = cycle_command()
        p = _run(cmd)
        ok = p.returncode == 0
        return {
            "ok": ok,
            "action": "run",
            "detail": (p.stdout or p.stderr).strip()[-2000:],
        }
    p = _run(["schtasks", "/run", "/tn", task_name])
    ok = p.returncode == 0
    return {"ok": ok, "action": "run", "detail": (p.stdout or p.stderr).strip()}


# ---- S-20: the schedule is a checked-in declaration ---------------------------
#
# `install` can register one task, but nothing recorded WHICH tasks should
# exist, at what cadence, enabled or not. A disabled task was therefore
# indistinguishable from a deleted one, and "the pipeline is paused" lived only
# in this machine's Task Scheduler. `config/schedule.yaml` is that record;
# `apply` makes the machine match it; Doctor reports when it does not.

SCHEDULE_FILE = repo_root() / "config" / "schedule.yaml"


class ScheduleError(ValueError):
    """A declaration that cannot be applied."""


def _same_time(registered: str, at: str) -> bool:
    """Is a schtasks Start Time the HH:MM the declaration asked for?"""
    text = (registered or "").strip()
    for fmt in ("%I:%M:%S %p", "%H:%M:%S", "%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M") == at
        except ValueError:
            continue
    return False


def load_desired(path=None) -> list:
    """Read and validate `config/schedule.yaml`."""
    path = Path(path or SCHEDULE_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ScheduleError(f"no schedule file at {path}") from None
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ScheduleError(f"{path}: `tasks` must be a non-empty list")

    out, seen = [], set()
    for raw in tasks:
        if not isinstance(raw, dict):
            raise ScheduleError(f"{path}: each task must be a mapping")
        task = str(raw.get("task") or "").strip()
        command = str(raw.get("command") or "").strip()
        if not task:
            raise ScheduleError(f"{path}: a task entry has no `task` name")
        if task in seen:
            raise ScheduleError(f"{path}: task {task!r} is declared twice")
        seen.add(task)
        if command not in TASK_FOR_COMMAND:
            raise ScheduleError(
                f"{path}: task {task!r} has unknown command {command!r}; "
                f"valid: {', '.join(TASK_FOR_COMMAND)}")
        cadence = raw.get("cadence")
        if not isinstance(cadence, dict):
            raise ScheduleError(f"{path}: task {task!r} needs a `cadence` mapping")
        unknown = set(cadence) - {"every", "at", "weekly", "monthly"}
        if unknown:
            raise ScheduleError(
                f"{path}: task {task!r}: unknown cadence key(s): "
                f"{', '.join(sorted(unknown))}")
        primary = [k for k in ("every", "weekly", "monthly") if k in cadence]
        if len(primary) > 1:
            raise ScheduleError(
                f"{path}: task {task!r} needs exactly one of every/at/weekly/monthly")
        if not primary and "at" not in cadence:
            raise ScheduleError(
                f"{path}: task {task!r} needs exactly one of every/at/weekly/monthly")
        if "every" in cadence and "at" in cadence:
            # An interval already carries its phase; a stray `at` here was
            # silently ignored, which read as "run at 09:00 every 30 min".
            raise ScheduleError(
                f"{path}: task {task!r}: `every` takes no `at`; interval "
                "cadences start from installation time")
        if "weekly" in cadence:
            try:
                cadence = {"weekly": weekday(cadence["weekly"]),
                           "at": str(cadence.get("at") or DEFAULT_TIME)}
                datetime.strptime(cadence["at"], "%H:%M")
            except (ValueError, TypeError) as exc:
                raise ScheduleError(f"{path}: task {task!r}: {exc}") from None
        elif "monthly" in cadence:
            try:
                cadence = {"monthly": month_day(cadence["monthly"]),
                           "at": str(cadence.get("at") or DEFAULT_TIME)}
                datetime.strptime(cadence["at"], "%H:%M")
            except (ValueError, TypeError) as exc:
                raise ScheduleError(f"{path}: task {task!r}: {exc}") from None
        elif "every" in cadence:
            try:
                every = int(cadence["every"])
                interval_schedule(every)
            except (TypeError, ValueError) as exc:
                raise ScheduleError(f"{path}: task {task!r}: {exc}") from None
            cadence = {"every": every}
        else:
            at = str(cadence["at"])
            try:
                datetime.strptime(at, "%H:%M")
            except ValueError:
                raise ScheduleError(
                    f"{path}: task {task!r}: at must be HH:MM, got {at!r}") from None
            cadence = {"at": at}
        options = raw.get("options") or {}
        if not isinstance(options, dict):
            raise ScheduleError(f"{path}: task {task!r}: options must be a mapping")
        unknown = set(options) - {k for k, _ in SCRAPE_OPTION_FLAGS}
        if unknown:
            raise ScheduleError(
                f"{path}: task {task!r}: unknown option(s): {', '.join(sorted(unknown))}")
        out.append({
            "task": task,
            "command": command,
            "cadence": cadence,
            "enabled": bool(raw.get("enabled", True)),
            "options": options,
        })
    return out


def describe_cadence(spec: dict) -> str:
    cadence = spec["cadence"]
    if "every" in cadence:
        n = int(cadence["every"])
        if n % 60 == 0:
            hours = n // 60
            return f"every {hours} hour" + ("s" if hours != 1 else "")
        return f"every {n} minute" + ("s" if n != 1 else "")
    if "weekly" in cadence:
        return f"weekly on {cadence['weekly']} at {cadence['at']}"
    if "monthly" in cadence:
        return f"monthly on day {int(cadence['monthly'])} at {cadence['at']}"
    return f"daily at {cadence['at']}"


def cadence_matches(st: dict, spec: dict) -> bool:
    """Does the registered trigger equal the declaration?"""
    cadence = spec["cadence"]
    if "every" in cadence:
        return st.get("interval_minutes") == int(cadence["every"])
    registered = (st.get("cadence") or "").lower()
    if "weekly" in cadence:
        return (registered.startswith("weekly")
                and cadence["weekly"].lower() in registered
                and _same_time(st.get("start_time") or "", cadence["at"]))
    if "monthly" in cadence:
        day = str(int(cadence["monthly"]))
        return (registered.startswith("monthly")
                and re.search(rf"\bday {day}\b", registered) is not None
                and _same_time(st.get("start_time") or "", cadence["at"]))
    return registered.startswith("daily") and _same_time(
        st.get("start_time") or "", cadence["at"])


def _launcher_problems(spec: dict) -> list:
    """What the generated launcher says versus what the declaration asks.

    The declaration carries a scope (`options`), and `apply` used to compare
    only cadence and enabled state: changing `only`/`max_posts` and re-running
    apply printed "unchanged" while the task kept scraping the old scope. The
    launcher is the file the scheduler runs, so it is the honest thing to
    compare. `treat` takes no scrape scope, so its options are not a fact.
    """
    launcher = read_launcher(spec["command"])
    if launcher is None:
        return [f"launcher missing; apply would rewrite "
                f"{launcher_path(spec['command']).name}"]
    version = launcher.get("version")
    name = launcher_path(spec["command"]).name
    if version is None or version < LAUNCHER_VERSION:
        # Checked before the `treat` shortcut: that command's options are not a
        # fact, but its FILE is out of date, and a launcher missing the log
        # rotation is exactly what this test exists to catch.
        return [f"launcher format v{version if version is not None else 'none'} "
                f"is older than v{LAUNCHER_VERSION}; apply would rewrite {name}"]
    if version > LAUNCHER_VERSION:
        return [f"launcher format v{version} is newer than v{LAUNCHER_VERSION}; "
                f"apply would rewrite {name}"]
    if spec["command"] == "treat":
        return []
    # Where the scope sits depends on the command: with a --config it is in
    # `argv` (before --db), without one it follows --db into `extra`. Reading
    # either field alone compared nothing for one of the two shapes.
    tokens = (shlex.split(launcher.get("argv") or "", posix=False)
              + list(launcher.get("extra") or []))
    have = parse_scrape_flags(tokens)
    if scrape_flags(have) != scrape_flags(spec["options"]):
        return [f"scrape options are {have or 'none'}; "
                f"should be {spec['options'] or 'none'}"]
    return []


def _drift_of(st: dict, spec: dict) -> list:
    problems = []
    if not st.get("installed"):
        # A task declared off and absent is still off. Only a task that should
        # RUN is missed; reporting the paused ones as drift would make a fresh
        # machine look broken for agreeing with the declaration.
        if spec["enabled"]:
            problems.append(f"not installed; should run {describe_cadence(spec)}")
        return problems
    if not cadence_matches(st, spec):
        problems.append(
            f"cadence is {st.get('cadence') or 'unknown'}; "
            f"should be {describe_cadence(spec)}")
    enabled = st.get("enabled", True)
    if bool(enabled) != spec["enabled"]:
        problems.append("should be enabled" if spec["enabled"] else "should be disabled")
    problems.extend(_launcher_problems(spec))
    return problems


def desired_drift(path=None) -> list:
    """Declared tasks whose live registration disagrees with the file.

    Windows-only: cron has one line per user and no per-task enabled state, so
    there is no drift to compare there.
    """
    if not is_windows():
        return []
    out = []
    for spec in load_desired(path):
        st = status(spec["task"])
        problems = _drift_of(st, spec)
        if problems:
            out.append({"task": spec["task"], "problems": problems})
    return out


def apply_desired(path=None, *, db="data/jester.db", config="config",
                  python=None, dry_run=False, force=False) -> dict:
    """Make the host match `config/schedule.yaml`. Applying twice changes nothing.

    One task at a time, in file order. A declared `enabled: false` is a pause:
    the task stays registered with its cadence and launcher, so re-enabling it
    later restores what was chosen rather than a default.

    A cadence change to a task that is currently ENABLED is refused unless
    `force`: that is a change to live request volume, and it must be asked for
    explicitly rather than arrived at by editing a file. Paused tasks move
    freely — they run nothing — and so do scope changes, which the next tick
    picks up without new requests.
    """
    if not is_windows():
        return {"ok": False, "results": [],
                "detail": "schedule apply needs Windows Task Scheduler; cron "
                          "has no per-task enabled state"}
    results = []
    for spec in load_desired(path):
        task = spec["task"]
        st = status(task)
        cadence_changed = bool(st.get("installed")) and not cadence_matches(st, spec)
        needs_create = (
            not st.get("installed")
            or cadence_changed
            or bool(_launcher_problems(spec))
        )
        needs_state = st.get("installed") and bool(st.get("enabled", True)) != spec["enabled"]
        if cadence_changed and bool(st.get("enabled", True)) and not force:
            results.append({
                "task": task,
                "action": "refused",
                "detail": (f"cadence {st.get('cadence') or 'unknown'} -> "
                           f"{describe_cadence(spec)} on an enabled task; "
                           "re-run with --force"),
            })
            continue
        if not needs_create and not needs_state:
            results.append({"task": task, "action": "unchanged"})
            continue
        if dry_run:
            what = []
            if needs_create:
                verb = "install" if not st.get("installed") else "reinstall"
                what.append(f"{verb} ({describe_cadence(spec)})")
            if needs_state:
                what.append("enable" if spec["enabled"] else "disable")
            results.append({"task": task, "action": "would " + ", ".join(what)})
            continue

        if needs_create:
            cadence = spec["cadence"]
            res = install(
                at=cadence.get("at", DEFAULT_TIME),
                db=db, config=config, task_name=task, python=python,
                every=cadence.get("every"),
                extra=scrape_flags({} if spec["command"] == "treat" else spec["options"]),
                command=spec["command"], weekly=cadence.get("weekly"),
                monthly=cadence.get("monthly"),
            )
            if not res.get("ok"):
                results.append({"task": task, "action": "failed",
                                "detail": res.get("detail", "")})
                continue
        current = status(task)
        if bool(current.get("enabled", True)) != spec["enabled"]:
            res = set_enabled(task, spec["enabled"])
            if not res.get("ok"):
                results.append({"task": task, "action": "failed",
                                "detail": res.get("detail", "")})
                continue
        results.append({"task": task,
                        "action": "installed" if needs_create else
                                  ("enabled" if spec["enabled"] else "disabled")})
    return {"ok": all(r["action"] not in ("failed", "refused") for r in results),
            "results": results}
