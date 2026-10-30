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
PY="${PYTHON:-python3}"
exec "$PY" -m jester.cli smoke "$@"
