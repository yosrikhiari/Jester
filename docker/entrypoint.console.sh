#!/bin/sh
# Start the cron daemon (so the Schedule tab's registered job actually fires
# inside the container), then launch the operator console.
set -e

# cron needs its spool dirs; create them idempotently.
mkdir -p /var/spool/cron/crontabs /etc/cron.d
chmod 1730 /var/spool/cron/crontabs 2>/dev/null || true
chmod 644 /etc/cron.d 2>/dev/null || true

# Start cron if present; never fail the container if it can't.
if command -v cron >/dev/null 2>&1; then
    cron 2>/dev/null || service cron start 2>/dev/null || true
fi

exec python -m jester.console "$@"
