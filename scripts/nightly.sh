#!/usr/bin/env bash
# D-2 nightly entry point (jester-setup.md §13.2 / §37.7).
#
# R55 contract: exits non-zero ONLY when the pipeline fails (FAILED_BATCHES)
# or errors hard — `jester run` owns that policy and prints the floor flags.
# A quiet-but-valid night (THIN_HAUL / ALL_TRIVIAL / LOW_NUGGETS / NO_IDEAS)
# exits 0 so cron stays quiet; the morning's `jester runs` still shows flags.
#
# Env:
#   PYTHON            interpreter override   (default: python3)
#   JESTER_DB         passed as --db to jester run
#   JESTER_NOTIFY_CMD optional failure hook; argv template word-split by the
#                     shell; tokens may use {exit_code} {run_id} {reason}.
#                     Hook failures never mask the real exit code.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"

RUN_ARGS=()
[ -n "${JESTER_DB:-}" ] && RUN_ARGS+=(--db "$JESTER_DB")

# Phase C cutover (§37.22): optionally run the Go ingestion worker first so
# `jester run` consumes real fetched batches instead of seeding mock data.
#   JESTER_GO_WORKER=1     enable the pre-phase
#   JESTER_WORKER_LIVE=1   make the worker scrape live (cloakserve required);
#                          otherwise it runs its deterministic mock fetch.
# Worker failure is a warning, never a hard stop: existing batches still flow.
if [ "${JESTER_GO_WORKER:-}" = "1" ]; then
    GO_BIN_RESOLVED="${GO_BIN:-$(command -v go || true)}"
    if [ -n "$GO_BIN_RESOLVED" ]; then
        echo "[nightly] go worker pre-phase"
        if [ "${JESTER_WORKER_LIVE:-}" = "1" ]; then
            ( cd go && "$GO_BIN_RESOLVED" run ./cmd/worker \
                -config ../config -db "${JESTER_DB:-data/jester.db}" \
                -run nightly-go -live ) \
                || echo "[nightly] WARN: go worker failed; continuing with existing batches"
        else
            ( cd go && JESTER_MOCK=1 "$GO_BIN_RESOLVED" run ./cmd/worker \
                -config ../config -db "${JESTER_DB:-data/jester.db}" \
                -run nightly-go ) \
                || echo "[nightly] WARN: go worker failed; continuing with existing batches"
        fi
    else
        echo "[nightly] WARN: JESTER_GO_WORKER=1 but no Go toolchain found (set GO_BIN)"
    fi
fi

"$PY" -m jester.cli run "${RUN_ARGS[@]}" "$@"
code=$?

if [ "$code" -ne 0 ] && [ -n "${JESTER_NOTIFY_CMD:-}" ]; then
    # shellcheck disable=SC2086  # intentional word-split into argv tokens
    "$PY" -m jester.cli notify --exit-code "$code" ${JESTER_NOTIFY_CMD} >/dev/null 2>&1 || true
fi

exit "$code"
