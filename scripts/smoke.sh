#!/usr/bin/env bash
# M1.6 E2E smoke entry point (jester-setup.md §37.9).
#
# Runs the pipeline on a disposable DB with every model job pinned to the
# deterministic stand-in, then verifies its own gates (a completed run summary
# WITH expected-vs-actual duration; >=1 non-trivial nugget per the §7.2 bar via
# R37 tags; >=1 scored idea whose citations all name nuggets that exist).
#
# `--live` requires the external stack (CloakBrowser/Qdrant/Ollama, §4) and is
# refused until that migration lands.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
# S-17: pick an interpreter that actually runs. `command -v python3` succeeds
# on Windows even when the name belongs to the Microsoft Store stub, which
# exits with "Python was not found" — so existence is not the test, execution
# is. PYTHON still wins; otherwise the first of python/python3 that answers.
if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
else
    PY=""
    for candidate in python python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
                && "$candidate" -c "" >/dev/null 2>&1; then
            PY="$candidate"
            break
        fi
    done
fi
if [ -z "$PY" ]; then
    echo "smoke: no working python interpreter found; set PYTHON" >&2
    exit 127
fi
exec "$PY" -m jester.cli smoke "$@"
