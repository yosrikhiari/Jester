#!/usr/bin/env bash
# Retention entry point (§14 TTL sweep; R31 concurrency contract, §37.28).
#
# Wraps `jester retention` (wipes raw_text past TTL + drops completed batches).
# The Python command is the lock-aware surface: it skips-and-logs rather than
# fighting an active run for the write lock.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
exec "$PY" -m jester.cli retention "$@"
