#!/usr/bin/env bash
# Schema migration entry point (R31 concurrency contract, §37.28).
#
# Refuses while a `runs` row is `running` — a migration mid-run must not be
# possible. Otherwise applies open_db's idempotent ALTERs and prints the
# current schema_version.

set -u
cd "$(dirname "$0")/.."
. scripts/_pythonpath.sh
# S-17: `python3` may be the Microsoft Store stub — it exists but does not run.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in python python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
                && "$candidate" -c "" >/dev/null 2>&1; then
            PY="$candidate"; break
        fi
    done
fi
[ -n "$PY" ] || { echo "migrate: no working python interpreter found; set PYTHON" >&2; exit 127; }
exec "$PY" -m jester.cli migrate "$@"
