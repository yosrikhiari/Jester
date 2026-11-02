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
* every specifier must hold for the locked version, whatever its operator.

The last rule is the whole point. A floor (``>=``) newer than the lock means
Windows installs something Linux does not -- real drift, and it fails. Every
other operator (``==``, ``===``, ``!=``, ``<=``, ``<``, ``>``, ``~=`` and a
wildcard ``==X.Y.*``) is evaluated the same way rather than ignored. When the
files disagree, one side has to move: regenerate the lock on Linux, or lower
the floor to the version the lock proves. Both are deliberate; a warning is
not.

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


def _compatible_bounds(wanted):
    """PEP 440 `~=`: (lower bound, exclusive upper prefix), or None if odd."""
    parts = []
    for piece in wanted.split("."):
        digits = re.match(r"\d+", piece)
        if digits is None:
            return None
        parts.append(int(digits.group()))
    if len(parts) < 2:
        return None
    lower = tuple(parts)
    if len(parts) == 2:
        upper = (parts[0] + 1,)
    else:
        upper = (parts[0], parts[1] + 1)
    return lower, upper


def _violation(operator, wanted, version):
    """None when `version` satisfies the specifier, else a short reason."""
    key = version_key(version)
    if operator in ("==", "===") and wanted.endswith(".*"):
        prefix = version_key(wanted[:-2])
        if key[: len(prefix)] != prefix:
            return f"is not {wanted}"
        return None
    if operator == "===":
        return None if version == wanted else f"is not exactly {wanted!r}"
    if operator == "==":
        return None if version == wanted else f"is not {wanted}"
    if operator == ">=":
        return None if key >= version_key(wanted) else f"is older than the required {wanted}"
    if operator == ">":
        return None if key > version_key(wanted) else f"is not newer than {wanted}"
    if operator == "<=":
        return None if key <= version_key(wanted) else f"is newer than the allowed {wanted}"
    if operator == "<":
        return None if key < version_key(wanted) else f"is not older than {wanted}"
    if operator == "!=":
        if version == wanted or key == version_key(wanted):
            return f"is the excluded {wanted}"
        return None
    if operator == "~=":
        bounds = _compatible_bounds(wanted)
        if bounds is None:
            return f"has an unusable ~= bound {wanted}"
        lower, upper = bounds
        if key < lower or key[: len(upper)] >= upper:
            return f"is outside the ~= {wanted} range"
        return None
    return f"uses an operator this checker does not understand ({operator})"


def check(requirements_path, lock_path):
    """Return (errors, locked names). Empty errors means the files agree."""
    rows = read_requirements(requirements_path)
    locked = read_lock(lock_path)
    errors = []
    for name, specs, raw in rows:
        key = normalize(name)
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
            reason = _violation(operator, wanted, version)
            if reason:
                errors.append(
                    f"{raw} does not match pylock.toml, which pins "
                    f"{name} {version} ({version} {reason})")
    return errors, sorted(locked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    try:
        errors, locked = check(args.requirements, args.lock)
    except (OSError, ValueError) as exc:
        print(f"lock parity: {exc}", file=sys.stderr)
        return 2
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
