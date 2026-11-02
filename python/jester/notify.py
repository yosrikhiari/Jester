"""R55 optional nightly failure hook: render + run JESTER_NOTIFY_CMD safely.

The hook is best-effort by contract: a broken hook must never mask the real
nightly exit code, so dispatch never raises.
"""
import subprocess


def render_tokens(
    tokens: list,
    *,
    exit_code: int,
    run_id: str = "nightly",
    reason: str = "failure",
    task: str = "",
) -> list:
    """Substitute {exit_code}/{run_id}/{reason}/{task} in each argv token (strict)."""
    ctx = {"exit_code": exit_code, "run_id": run_id, "reason": reason, "task": task}
    return [t.format(**ctx) for t in tokens]


def dispatch_notify(
    tokens: list,
    *,
    exit_code: int,
    run_id: str = "nightly",
    reason: str = "failure",
    timeout: int = 15,
    task: str = "",
    env: dict | None = None,
) -> tuple:
    """Run the rendered hook argv; return (ok, stdout). Never raises.

    `env` lets the caller hand the hook the task name and exit code in its
    environment, which is the only place a shell script can read them without
    knowing this project's argument names.
    """
    argv = render_tokens(tokens, exit_code=exit_code, run_id=run_id, reason=reason,
                         task=task)
    try:
        # utf-8/replace, not the locale codec: a hook that echoes scraped
        # text would otherwise raise in the reader thread and return None.
        proc = subprocess.run(argv, capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace", env=env)
        return proc.returncode == 0, proc.stdout or ""
    except Exception:
        return False, ""
