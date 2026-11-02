"""V3-24: Windows `requirements.txt` and Linux `pylock.toml` must agree.

The Windows job installs requirements.txt while Docker/Linux install the lock.
The lock carries Linux-only wheels, so Windows cannot install it; this checker
compares the two files and fails if their direct dependencies drift.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_lock_parity.py"

LOCK = """\
lock-version = "1.0"
created-by = "pip"

[[packages]]
name = "PyYAML"
version = "6.0.3"
"""


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_the_checker_passes_at_head():
    res = _run()
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "agree" in res.stdout.lower()
    # Fix round 1, Important 1: the tree may not disagree with itself while the
    # gate is green. Floors and pins are aligned, so there is nothing to warn
    # about either.
    assert "warning:" not in res.stdout.lower(), res.stdout


def test_a_bumped_pin_fails(tmp_path):
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML==6.0.4\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "PyYAML" in res.stderr and "6.0.4" in res.stderr


def test_a_floor_above_the_lock_fails(tmp_path):
    """Fix round 1, Important 1: this is the form requirements.txt actually
    uses, and the form the checker used to wave through with exit 0. A floor
    the lock does not satisfy means the two platforms install different
    things: Windows gets 6.0.4+, Linux gets 6.0.3."""
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML>=6.0.4\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "PyYAML" in res.stderr and "6.0.4" in res.stderr
    assert "older than the required" in res.stderr


def test_a_floor_the_lock_satisfies_passes(tmp_path):
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML>=6.0,<7\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 0, (res.stdout, res.stderr)


def test_every_specifier_operator_is_compared(tmp_path):
    """Fix round 1, Important 1: only ==/=== / >= were looked at. `!=`, `<=`,
    `<` and `~=` were accepted whatever the lock said, which is the same
    "checked but never fails" hole in four more spellings."""
    lock = tmp_path / "pylock.toml"
    lock.write_text(LOCK, encoding="utf-8")
    req = tmp_path / "requirements.txt"

    for spec in ("PyYAML!=6.0.3", "PyYAML<=6.0.2", "PyYAML<6.0.3",
                 "PyYAML~=5.9", "PyYAML==6.1.*"):
        req.write_text(spec + "\n", encoding="utf-8")
        res = _run("--requirements", str(req), "--lock", str(lock))
        assert res.returncode == 1, (spec, res.stdout, res.stderr)
        assert "PyYAML" in res.stderr, (spec, res.stderr)

    for spec in ("PyYAML!=6.0.4", "PyYAML<=6.0.3", "PyYAML<6.1",
                 "PyYAML~=6.0", "PyYAML~=6.0.3", "PyYAML>5.0"):
        req.write_text(spec + "\n", encoding="utf-8")
        res = _run("--requirements", str(req), "--lock", str(lock))
        assert res.returncode == 0, (spec, res.stdout, res.stderr)


def test_an_unpinned_requirement_fails(tmp_path):
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1
    assert "unpinned" in res.stderr.lower()


def test_a_requirement_missing_from_the_lock_fails(tmp_path):
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML>=6.0\nqdrant-client>=1.19.1\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1
    assert "qdrant-client" in res.stderr


# --- R4-01: a failure says how to fix it, not only what is wrong -------------

def test_a_failure_prints_the_regeneration_command(tmp_path):
    """A red lock-parity gate used to name the drift and stop there. The fix is
    one command, and it has to run on Linux (the lock pins Linux wheels), so it
    belongs in the message rather than in someone's memory."""
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML>=6.0.4\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1
    assert "python:3.12-slim" in res.stderr, res.stderr
    assert "pip lock" in res.stderr, res.stderr
    assert "python/requirements.txt" in res.stderr
    assert "scripts/refresh_lock.sh" in res.stderr, res.stderr


def test_an_unreadable_file_prints_the_regeneration_command_too(tmp_path):
    """Every failure path, not just the drift one: a missing requirements file
    is also a state the operator has to be told how to recover from."""
    res = _run("--requirements", str(tmp_path / "absent.txt"),
               "--lock", str(tmp_path / "absent.toml"))

    assert res.returncode == 2
    assert "python:3.12-slim" in res.stderr, res.stderr
    assert "scripts/refresh_lock.sh" in res.stderr, res.stderr


def test_the_refresh_script_exists_and_is_posix_sh():
    path = ROOT / "scripts" / "refresh_lock.sh"
    text = path.read_text(encoding="utf-8")

    assert text.startswith("#!"), "a script needs an interpreter line"
    assert "sh" in text.splitlines()[0]
    assert "set -eu" in text
    assert "python:3.12-slim" in text
    assert "pip lock" in text
    assert "docker" in text, "it must say so when docker is missing"
    assert "\r\n" not in text, "a CRLF shebang does not run under sh"

