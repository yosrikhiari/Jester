#!/usr/bin/env bash
# Retention entry point (§14 TTL sweep; R31 concurrency contract, §37.28).
#
# Wraps `jester retention` (wipes raw_text past TTL + drops completed batches).
# The Python command is the lock-aware surface: it skips-and-logs rather than
# fighting an active run for the write lock.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
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
[ -n "$PY" ] || { echo "retention: no working python interpreter found; set PYTHON" >&2; exit 127; }
exec "$PY" -m jester.cli retention "$@"
