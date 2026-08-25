#!/usr/bin/env bash
# M1.6 E2E smoke entry point (jester-setup.md §37.9).
#
# Mock mode is the first pass: runs the pipeline on a fresh DB and verifies its
# own gates (completed run summary WITH expected-vs-actual duration; >=1
# non-trivial nugget per the §7.2 bar via R37 tags).
#
# `--live` requires the external stack (CloakBrowser/Qdrant/Ollama, §4) and is
# refused until that migration lands.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
exec "$PY" -m jester.cli smoke "$@"
