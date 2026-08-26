"""Invoking the Go ingestion worker.

This lived inside `ConsoleAPI` and so was reachable only from the console. The
scheduled job runs through the CLI, which had no way to fetch anything at all —
`jester schedule install` registered `jester.cli run`, a command that processes
the queue and never fills it. A nightly job that scrapes nothing is the same
bug as a console button that scrapes nothing, and it had the same cause: one
copy of this knowledge, in the wrong place.

Everything here is a pure function of its arguments plus one subprocess call,
so both callers get identical behaviour and the argv is testable without Go.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The worker's own tally, taken from its log rather than inferred from a DB
#: delta — a concurrent run would make the delta a lie.
_QUEUED_RE = re.compile(r"^\[live\] done: (\d+) comment", re.M)
_MOCK_QUEUED_RE = re.compile(r"^enqueued batch id=\d+ with (\d+) kept comments", re.M)


def _is_go_toolchain(path):
    """True when `path` is the `go` command (vs a prebuilt `worker` binary)."""
    return os.path.basename(str(path)).lower() in ("go", "go.exe")


def go_binary():
    """Resolve how to run the Go worker.

    Priority: explicit GO_BIN (the `go` toolchain), then a prebuilt `worker`
    binary (baked into the Docker image so the scheduled cycle runs offline
    without the Go toolchain present), then `go` on PATH, then a vendored
    Windows go.exe. PATH used to be skipped, so a perfectly normal `go`
    install reported the worker as unavailable."""
    explicit = os.environ.get("GO_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    prebuilt = os.environ.get("JESTER_WORKER_BIN")
    if prebuilt and Path(prebuilt).exists():
        return prebuilt
    for candidate in (shutil.which("worker"), "/usr/local/bin/worker"):
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("go")
    if found:
        return found
    vendored = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "go-toolchain"
        / "bin"
        / "go.exe"
    )
    return str(vendored) if vendored.exists() else None


def queued_from_output(text: str):
    """The number of comments the worker says it queued, or None if it did not
    say. None is not zero: "could not tell" and "fetched nothing" lead to very
    different next actions."""
    for pattern in (_QUEUED_RE, _MOCK_QUEUED_RE):
        m = pattern.search(text or "")
        if m:
            return int(m.group(1))
    return None


def worker_argv(
    go_bin,
    config_dir,
    db_path,
    run_id,
    *,
    live=False,
    only=None,
    max_comments=None,
    max_posts=None,
):
    """The exact command line, split out so tests can assert on it without Go.

    `only` restricts the walk to those source names for this run without
    touching sources.yaml; the two ceilings are worker flags rather than
    post-filters, because the point of a ceiling is to not fetch the rest.
    """
    cmd = [go_bin]
    # A prebuilt `worker` binary is invoked directly; the `go` toolchain needs
    # the `run ./cmd/worker` wrapper. Tests pass the literal "go" and still get
    # the wrapper form.
    if _is_go_toolchain(go_bin):
        cmd.append("run")
        cmd.append("./cmd/worker")
    cmd += [
        # ABSOLUTE, always: the worker is launched with cwd=go/, so a relative
        # --db lands in go/data/ instead of data/. The console got away with a
        # relative path only because it absolutises in its constructor; the CLI
        # passed argparse's default straight through, and a scheduled run
        # cheerfully scraped 554 comments into a database nothing ever reads.
        "-config",
        os.path.abspath(config_dir),
        "-db",
        db_path if db_path == ":memory:" else os.path.abspath(db_path),
        "-run",
        run_id,
    ]
    names = [str(n).strip() for n in (only or []) if str(n).strip()]
    if names:
        # One flag, comma-joined: the worker resolves names against
        # sources.yaml and fails loudly on one it does not know, so a typo
        # cannot quietly become a zero-source fetch.
        cmd += ["-only", ",".join(names)]
    if max_comments:
        cmd += ["-max-comments", str(int(max_comments))]
    if max_posts:
        cmd += ["-max-posts", str(int(max_posts))]
    if live:
        cmd.append("-live")
    return cmd


def ingest(
    config_dir,
    db_path,
    run_id="jester-ingest",
    *,
    live=False,
    only=None,
    max_comments=None,
    max_posts=None,
    timeout=900,
    counts=None,
):
    """Run the Go worker once. Returns the same dict shape the console renders.

    Never raises: a scheduled job that dies on a missing toolchain leaves no
    account of itself, so every failure comes back as `{"ok": False, "error"}`.
    """
    go_bin = go_binary()
    if not go_bin:
        return {
            "ok": False,
            "error": "no Go toolchain found — install Go or set GO_BIN",
        }
    cmd = worker_argv(
        go_bin,
        config_dir,
        db_path,
        run_id,
        live=live,
        only=only,
        max_comments=max_comments,
        max_posts=max_posts,
    )
    env = os.environ.copy()
    if not live:
        env["JESTER_MOCK"] = "1"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            cwd=str(REPO_ROOT / "go"),
            env=env,
            capture_output=True,
            timeout=timeout,
            # The worker echoes scraped comment text, which is UTF-8 and full
            # of bytes the Windows locale codec cannot decode. With a bare
            # text=True the reader thread dies on the first smart quote,
            # subprocess hands back stdout=None, and the caller loses the
            # worker's ENTIRE account of the run — how many comments it
            # queued, which sources it skipped, why it failed. It read as a
            # clean run that happened to do nothing.
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"worker exceeded {timeout}s and was killed"}
    except OSError as exc:
        return {"ok": False, "error": f"could not start the worker: {exc}"}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    result = {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "live": bool(live),
        "output": out[-4000:],
        "error": err[-2000:] if proc.returncode != 0 else "",
        "queued_new_comments": queued_from_output(out),
    }
    if counts is not None:
        result["counts"] = counts() if callable(counts) else counts
    return result
