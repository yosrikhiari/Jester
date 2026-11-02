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


def test_a_bumped_pin_fails(tmp_path):
    req = tmp_path / "requirements.txt"
    lock = tmp_path / "pylock.toml"
    req.write_text("PyYAML==6.0.4\n", encoding="utf-8")
    lock.write_text(LOCK, encoding="utf-8")

    res = _run("--requirements", str(req), "--lock", str(lock))

    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "PyYAML" in res.stderr and "6.0.4" in res.stderr


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
