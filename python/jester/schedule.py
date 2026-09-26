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
from pathlib import Path

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
    return (
        "@echo off\r\n"
        "REM Generated by `jester schedule install`. Edits are overwritten on\r\n"
        "REM the next install; change the schedule with the CLI instead.\r\n"
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
        f'>>"{log}" echo [%DATE% %TIME%] jester {command} starting\r\n'
        # -u, because this stdout goes to a FILE. Python block-buffers a
        # redirected stream, so a `treat` run that spends an hour in synthesis
        # — which is the normal pace when nearly every Groq call comes back 429
        # and waits out the retry cap — writes nothing to the log until it
        # exits. From outside, a healthy long run and a hung one look identical,
        # and the console only says "running". One flag makes the difference
        # between the two visible while it is still happening.
        f'"{py}" -u -m jester.cli {" ".join(command_argv(command))} '
        f'--db "{db}"{cfg}{args} '
        f'>>"{log}" 2>&1\r\n'
        f'>>"{log}" echo [%DATE% %TIME%] jester {command} exited %ERRORLEVEL%\r\n'
    )


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
}

#: What each schedulable command expands to on the command line, and whether it
#: takes `--config`.
#:
#: `signals` needed both: it is a sub-command (`signals run`, not `signals`),
#: and it has no `--config` option, so the launcher's fixed
#: `--db X --config Y` shape would have made it exit 2 on every tick. A table
#: is cheaper than a branch in the launcher, and it is the place to look when
#: the next command does not fit the mould either.
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
                 "--rules", "config/hiring_rules.yaml",
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
        "argv": ("signals", "run",
                 "--source", "hackernews-hiring",
                 "--rules", "config/hiring_rules.yaml",
                 "--query", "2", "--limit", "1000"),
        "config": False,
    },
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
    # A daily trigger repeats once every 24h; that is still a cadence.
    if (fields.get("schedule type") or "").strip().lower().startswith("daily"):
        return 24 * 60
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
        return {
            "installed": True,
            "scheduler": "schtasks",
            "next_run": fields.get("next run time", ""),
            "cadence": _cadence_from(fields),
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


def _jester_cron_lines() -> tuple:
    """(match-predicate substring, ) used to find our own entries."""
    return ("jester.cli cycle", "jester.cli run")


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


def cron_line(
    at: str = DEFAULT_TIME,
    db: str = "data/jester.db",
    config: str = "config",
    python: str | None = None,
    every: int | None = None,
    extra=(),
    command: str = "cycle",
) -> str:
    """The crontab line to install. `every` gives an every-N-minutes cadence;
    otherwise it is daily at `at`. `extra` carries the scrape scope, which the
    POSIX path used to drop on the floor — a crontab line that quietly walks
    every source is not the schedule the operator configured."""
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
        when = f"{int(mm or 0)} {int(hh)} * * *"
    args = "".join(f" {a}" for a in extra)
    cfg = f" --config {config}" if command_takes_config(command) else ""
    return (
        f"{when}  "
        f"cd {root} && PYTHONPATH={root / 'python'} "
        f"{py} -m jester.cli {' '.join(command_argv(command))} "
        f"--db {db}{cfg}{args} "
        f">> {log_path(command)} 2>&1"
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
) -> dict:
    """Register the scrape+process job. Returns {ok, action, detail}.

    `every` schedules every N minutes; without it the job runs daily at `at`.
    Either way the registered command is `jester cycle` — fetch AND process.
    Registering `run` was the original bug: the job fired on time for months
    and archived nothing, because nothing had queued anything for it to read.
    """
    if every is not None:
        try:
            sc, mo = interval_schedule(every)
        except ValueError as exc:
            return {"ok": False, "action": "create", "detail": str(exc)}
    if not is_windows():
        try:
            line = cron_line(at, db, config, python, every=every, extra=extra,
                             command=command)
        except ValueError as exc:
            return {"ok": False, "action": "manual", "detail": str(exc)}
        try:
            current = _read_crontab()
            needle = _jester_cron_lines()
            # Drop any prior jester entry so re-installing does not duplicate.
            current = [ln for ln in current if not any(n in ln for n in needle)]
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
    if every is not None:
        # Task Scheduler's default multiple-instances policy is IgnoreNew, so
        # a tick that overruns its interval is skipped rather than doubled.
        # `cycle` guards this itself too, for the cron path that has no such
        # policy.
        cmd += ["/sc", sc, "/mo", str(mo)]
        cadence = f"every {mo} minute(s)" if sc == "MINUTE" else f"every {mo} hour(s)"
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
            current = [ln for ln in _read_crontab() if not any(n in ln for n in needle)]
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
