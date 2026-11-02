#!/usr/bin/env python3
"""Check that python/requirements.txt and python/pylock.toml agree (V3-24).

The Windows job installs requirements.txt; Docker and the Linux jobs install
pylock.toml. The lock pins Linux-only wheels, so Windows cannot install it.
That split is fine only while both files name the same direct dependencies at
versions that agree:

* every requirement must carry a version specifier (an unpinned row is drift
  by definition -- the two platforms may resolve anything);
* every requirement must appear in the lock (a missing row means Linux never
  installs something Windows does);
* an exact pin (``==``) must equal the locked version (a bumped pin on one
  side only is the drift this check exists to catch).

Floors (``>=``) are reported as warnings when the lock is older than the
floor: that is real drift, but the lock is Linux-generated and regenerating it
is a deliberate act, so it must not fail an otherwise healthy tree.

Stdlib only. TOML is parsed with ``tomllib`` on 3.11+; older interpreters fall
back to a small reader for the ``[[packages]]`` shape pip lock emits.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = ROOT / "python" / "requirements.txt"
DEFAULT_LOCK = ROOT / "python" / "pylock.toml"

_NAME = re.compile(r"^([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*(.*)$")
_SPEC = re.compile(r"(===|==|~=|!=|<=|>=|<|>)\s*([A-Za-z0-9_.*+-]+)")


def normalize(name):
    """PEP 503: PyYAML, pyyaml and py-yaml name the same package."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def version_key(text):
    parts = []
    for piece in re.split(r"[.+-]", text):
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def read_requirements(path):
    """Return [(name, specifiers, raw_line)] for every requirement row."""
    rows = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):  # options such as -e or --index-url
            continue
        requirement = line.split(";", 1)[0].strip()  # drop environment markers
        match = _NAME.match(requirement)
        if not match:
            raise ValueError(f"{path}: cannot parse requirement {raw!r}")
        name, rest = match.group(1), match.group(2).strip()
        rows.append((name, _SPEC.findall(rest), raw.strip()))
    return rows


def read_lock(path):
    """Return {normalized name: version} from a PEP 751 lock."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.10 and older only
        return _read_lock_fallback(path)
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    locked = {}
    for package in data.get("packages") or []:
        name = package.get("name")
        version = package.get("version")
        if name and version:
            locked[normalize(name)] = version
    return locked


def _read_lock_fallback(path):  # pragma: no cover - exercised on 3.10 only
    locked, current = {}, None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[[packages]]"):
            current = {}
            continue
        if line.startswith("[") and current is not None and not line.startswith("[[packages]]"):
            # A nested table such as [[packages.wheels]] ends the name/version
            # block; keep the entry already collected.
            if current.get("name") and current.get("version"):
                locked[normalize(current["name"])] = current["version"]
            current = None
            continue
        if current is None:
            continue
        match = re.match(r'(name|version)\s*=\s*"([^"]+)"', line)
        if match:
            current[match.group(1)] = match.group(2)
    if current and current.get("name") and current.get("version"):
        locked[normalize(current["name"])] = current["version"]
    return locked


def check(requirements_path, lock_path):
    """Return (errors, warnings). Empty errors means the files agree."""
    rows = read_requirements(requirements_path)
    locked = read_lock(lock_path)
    errors, warnings, seen = [], [], set()
    for name, specs, raw in rows:
        key = normalize(name)
        seen.add(key)
        if key not in locked:
            errors.append(f"{name} is in requirements.txt but not pylock.toml")
            continue
        if not specs:
            errors.append(
                f"{raw} is unpinned; add a version specifier so the lock and "
                "Windows cannot resolve different versions")
            continue
        version = locked[key]
        for operator, wanted in specs:
            if operator in ("==", "===") and wanted != version:
                errors.append(
                    f"{raw} does not match pylock.toml, which pins "
                    f"{name} {version}")
            elif operator == ">=" and version_key(version) < version_key(wanted):
                warnings.append(
                    f"{raw} asks for at least {wanted}, but pylock.toml pins "
                    f"{version}; regenerate the lock on Linux")
    return errors, warnings, sorted(locked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    try:
        errors, warnings, locked = check(args.requirements, args.lock)
    except (OSError, ValueError) as exc:
        print(f"lock parity: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"lock parity: {error}", file=sys.stderr)
        return 1
    print(
        f"lock parity: {len(read_requirements(args.requirements))} direct "
        f"requirement(s) agree with {len(locked)} locked package(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
