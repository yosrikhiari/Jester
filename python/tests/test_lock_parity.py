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
