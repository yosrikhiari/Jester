#!/usr/bin/env python3
"""Print the numbers the README quotes, so nobody has to hand-maintain them (S-17).

Three counts:
  - Python tests: collected by pytest with the repo's own addopts, i.e. the
    offline suite CI runs (live-marked tests are deselected).
  - Go test functions: `func Test...` declarations under go/.
  - Archive rows: comments / nuggets / ideas, opened with `mode=ro` so this
    can never write to the archive. Skipped when the file is absent.

Usage:
    python scripts/counts.py                 # archive defaults to data/jester.db
    python scripts/counts.py --db other.db
    python scripts/counts.py --no-archive    # code counts only
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_tests() -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "python/tests", "--collect-only", "-q"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    match = re.search(r"(\d+)(?:/\d+)? tests? collected", tail)
    if not match:
        raise SystemExit(f"counts: could not read pytest's total:\n{proc.stdout[-800:]}")
    return int(match.group(1))


def go_tests() -> int:
    pattern = re.compile(r"^func (Test\w*)\(", re.MULTILINE)
    return sum(len(pattern.findall(path.read_text(encoding="utf-8")))
               for path in (ROOT / "go").rglob("*_test.go"))


def archive_rows(db_path: Path) -> dict:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        existing = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table in ("comments", "nuggets", "ideas"):
            if table in existing:
                counts[table] = db.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "jester.db"),
                        help="archive to count (opened read-only)")
    parser.add_argument("--no-archive", action="store_true",
                        help="print the code counts only")
    args = parser.parse_args(argv)

    print(f"python tests: {python_tests()}")
    print(f"go test functions: {go_tests()}")
    if args.no_archive:
        return 0
    path = Path(args.db)
    if not path.is_file():
        print(f"archive: {path} not present, skipped")
        return 0
    for table, count in archive_rows(path).items():
        print(f"{table}: {count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
