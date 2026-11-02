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
#
# A read-only ConsoleAPI never creates the schema (POST /api/migrate is refused
# too), so on a fresh `jester_docker_data` volume every page would answer 503
# forever while /metrics stayed green. Bootstrap the archive here, once, before
# the console starts. This is the container's own archive setup, not an HTTP
# write path -- the server still refuses writes, including /api/migrate.
#
# The bootstrap is `open_db`, the documented schema path (go/cmd/contractcheck
# documents the same call): it creates and reconciles the schema and is
# idempotent. Deliberately NOT `jester migrate`: that command refuses while any
# run row says status='running' (the R31 guard), so one stale row left by a
# crashed run would make the viewer exit 1 and look like a missing schema.
#
# The archive is whatever the console was told via --db VALUE or --db=VALUE,
# last one winning. An empty or valueless --db falls back to $JESTER_DB and
# then the image CMD's /app/data/jester-docker.db, because an empty path is not
# a path and must never be handed to open_db.
fallback_db=${JESTER_DB:-/app/data/jester-docker.db}
db=
want_db=
for arg in "$@"; do
    if [ -n "$want_db" ]; then
        want_db=
        case $arg in
            -*) ;;              # a flag is not a path; keep the fallback
            *) db=$arg ;;
        esac
        continue
    fi
    case $arg in
        --db) want_db=1 ;;
        --db=*) db=${arg#--db=} ;;
    esac
done
db=${db:-$fallback_db}

if [ -z "${JESTER_CONSOLE_TOKEN:-}" ]; then
    if [ -z "$db" ]; then
        echo "console-entrypoint: refusing to bootstrap an empty archive path." >&2
        exit 1
    fi
    echo "console-entrypoint: JESTER_CONSOLE_TOKEN is unset; bootstrapping $db, then starting the console read-only." >&2
    # Fail loudly: with no schema every route answers 503, which /metrics hides.
    if ! python -c 'import sys; from jester.store import open_db; open_db(sys.argv[1])' "$db"; then
        echo "console-entrypoint: schema bootstrap of $db failed; refusing to start a console with no schema." >&2
        exit 1
    fi
    set -- "$@" --read-only
fi

exec python -m jester.console "$@"
