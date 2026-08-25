#!/usr/bin/env bash
# Schema migration entry point (R31 concurrency contract, §37.28).
#
# Refuses while a `runs` row is `running` — a migration mid-run must not be
# possible. Otherwise applies open_db's idempotent ALTERs and prints the
# current schema_version.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
exec "$PY" -m jester.cli migrate "$@"
