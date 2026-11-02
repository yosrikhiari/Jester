#!/bin/sh
# Launch the operator console, as a non-root user.
#
# There is no cron daemon to start any more. It was here so the Schedule tab
# could register a job inside the container, which made this container the
# second scheduler on a machine that already has one — the host's. The host's
# scheduler is the scheduler (see docker/Dockerfile.python), so the only thing
# left to do is check the archive volume and hand over to the console.
set -e

# A named volume created by the root-era image is still owned by root, and the
# failure that produces ("unable to open database file") reads like a code
# bug. Say what it is, once, and let the console fail for its own reason if
# the operator does not fix it.
if [ ! -w /app/data ]; then
    echo "console-entrypoint: /app/data is not writable by uid $(id -u)." >&2
    echo "  This volume was probably created by the old root image." >&2
    echo "  Recreate it once (docker compose down -v) or, to keep its data:" >&2
    echo "  docker run --rm -v jester_jester_docker_data:/d alpine chown -R 10001:10001 /d" >&2
fi

# The console listens on 0.0.0.0 inside the container, which is reachable from
# the compose network. Without a token a non-loopback bind is refused, and
# refusing would leave the default stack dead. `--read-only` is the signal
# `_bind_refusal` accepts for that bind: it disables every write route without
# needing a secret. When a token IS set, the operator asked for writes, so the
# flag is omitted. Extra flags from the command (--read-auth, --port, ...) pass
# through untouched.
if [ -z "${JESTER_CONSOLE_TOKEN:-}" ]; then
    echo "console-entrypoint: JESTER_CONSOLE_TOKEN is unset; starting the console read-only." >&2
    set -- "$@" --read-only
fi

exec python -m jester.console "$@"
