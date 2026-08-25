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
) -> list:
    """Substitute {exit_code}/{run_id}/{reason} in each argv token (strict)."""
    ctx = {"exit_code": exit_code, "run_id": run_id, "reason": reason}
    return [t.format(**ctx) for t in tokens]


def dispatch_notify(
    tokens: list,
    *,
    exit_code: int,
    run_id: str = "nightly",
    reason: str = "failure",
    timeout: int = 15,
) -> tuple:
    """Run the rendered hook argv; return (ok, stdout). Never raises."""
    argv = render_tokens(tokens, exit_code=exit_code, run_id=run_id, reason=reason)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, proc.stdout or ""
    except Exception:
        return False, ""
