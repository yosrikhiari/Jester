#!/usr/bin/env python3
"""Fail if any source file contains an invisible control character.

WHY THIS EXISTS. Three times in one session a patch script wrote a literal
backspace (0x08) into source where a regex word-boundary was intended. Every
time the result compiled, gofmt was satisfied, and the pattern silently matched
nothing:

  * the Discourse quote stripper let every quoted block through
  * the bot filter's clause-anchored rules were dead, which made the
    "real complaints must survive" tests pass for the wrong reason
  * this project's own CI workflow, whose job is to catch exactly this

A control character is invisible in a diff, in a code review, and in most
editors. Nothing else in the toolchain looks for it. This does.

Tabs, newlines and carriage returns are legitimate and allowed. Everything else
below 0x20, plus DEL, is not.
"""

import sys
from pathlib import Path

#: Extensions worth checking: anything a human edits and a machine parses.
SUFFIXES = {".go", ".py", ".js", ".ts", ".tsx", ".yml", ".yaml", ".json", ".sh"}

#: Directories that are not ours to police.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".ruff_cache", "data", "dist", "build",
}

#: Tab, newline, carriage return. Everything else in the C0 range is a bug.
ALLOWED = {0x09, 0x0A, 0x0D}


def offending(data: bytes):
    """Byte offsets of disallowed control characters."""
    return [
        i for i, b in enumerate(data)
        if (b < 0x20 and b not in ALLOWED) or b == 0x7F
    ]


def main(root: str = ".") -> int:
    base = Path(root)
    problems = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        hits = offending(data)
        if hits:
            # Report the first one with its line and a readable context, so
            # the fix is obvious rather than a hunt through a binary diff.
            first = hits[0]
            line = data[:first].count(b"\n") + 1
            ctx = data[max(0, first - 40):first + 20]
            problems.append(
                f"{path}:{line}: {len(hits)} control character(s), "
                f"first is 0x{data[first]:02X} near {ctx!r}"
            )

    if problems:
        print("Invisible control characters found. These compile and silently")
        print("break regexes and string literals:")
        print()
        for p in problems:
            print(f"  {p}")
        print()
        print("If a regex word-boundary was intended, write two characters:")
        print("  backslash followed by b — not the 0x08 escape a shell or")
        print("  Python patch script produces from the same source text.")
        return 1

    print("no control characters found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
