"""The version contract as a test: ONE Python floor, ONE Go floor.

A contract that lives in prose drifts: `pyproject.toml` said `>=3.10` while
README and quickstart said "Python 3.11+", and README said "Go 1.22+" while
`go/go.mod` said `go 1.26`. This file is the prose, read back.

- `python/pyproject.toml` `requires-python` is THE Python floor.
- `go/go.mod`'s `go` directive is THE Go floor; README must echo it exactly.
- `docs/quickstart.md` states the same floor, and names 3.12 as the TESTED
  matrix (CI and the container) rather than as a higher floor.
- `.github/workflows/ci.yml` runs the matrix, which has to be at-or-above the
  floor while named as such.

The point is the next drift failing here instead of being discovered by the
third person to hit it. Deliberately narrow: only the five files that declare
a floor are read, never a historical report (those describe the tree as it
was, and are allowed to be older than this contract).
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PYTHON_FLOOR = "3.11"
GO_FLOOR = "1.26"
MATRIX_PYTHON = "3.12"


def test_pyproject_declares_the_python_floor():
    data = tomllib.loads(
        (ROOT / "python" / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == f">={PYTHON_FLOOR}"


def test_readme_echoes_both_floors():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        f"**Languages:** Python {PYTHON_FLOOR}+, Go {GO_FLOOR}+" in text
    )


def test_quickstart_states_the_same_floor_and_names_the_matrix():
    text = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    assert f"Requires Python {PYTHON_FLOOR}+" in text
    # 3.12 is the tested matrix, named as such — never as the floor.
    assert "3.12 is what CI and the container run" in text
    assert "Python 3.10" not in text and ">3.10" not in text
    # Native installs resolve the platform themselves; the Linux-only lock is
    # never handed to a native pip.
    assert "pip install --only-binary :all: -r python/pylock.toml" not in text


def test_gomod_declares_the_go_floor():
    text = (ROOT / "go" / "go.mod").read_text(encoding="utf-8")
    match = re.search(r"(?m)^go (\d+)\.(\d+)$", text)
    assert match
    assert (int(match.group(1)), int(match.group(2))) == (1, 26)


def test_ci_runs_the_matrix_named_in_quickstart():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    python_versions = re.findall(r'python-version:\s*"([^"]+)"', text)
    assert python_versions == [MATRIX_PYTHON, MATRIX_PYTHON]
    floor = tuple(int(p) for p in PYTHON_FLOOR.split("."))
    for version in python_versions:
        assert tuple(int(p) for p in version.split(".")) >= floor
    assert re.findall(r'go-version:\s*"([^"]+)"', text) == [GO_FLOOR]
