#!/bin/sh
# Regenerate python/pylock.toml the only way it can be regenerated.
#
# The lock pins LINUX wheels (`grpcio` is the one that bites), so it is
# generated inside python:3.12-slim, the same image docker/Dockerfile.python
# builds on. A lock resolved on the Windows host names wheels Docker and CI
# cannot install, and the failure shows up much later as "no matching
# distribution".
#
# Usage: scripts/refresh_lock.sh
#
# Raise a floor in python/requirements.txt first, then run this: every pip bump
# must land with a regenerated lock, or the Windows job and the Linux job
# resolve different versions from the same commit.
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"

if ! command -v docker >/dev/null 2>&1; then
    echo "refresh_lock: docker is not installed, and this lock cannot be" >&2
    echo "refresh_lock: generated without it -- it pins Linux wheels. Install" >&2
    echo "refresh_lock: Docker and run this again, or run the command by hand:" >&2
    echo >&2
    echo "  docker run --rm -v \"\$PWD:/src\" -w /src python:3.12-slim sh -c \\" >&2
    echo "    'pip install -q -U pip; pip lock -q -r python/requirements.txt -o /dev/stdout' \\" >&2
    echo "    > python/pylock.toml" >&2
    exit 1
fi

body=$(mktemp)
header=$(mktemp)
trap 'rm -f "$body" "$header"' EXIT INT TERM

echo "refresh_lock: resolving python/requirements.txt in python:3.12-slim ..."
docker run --rm -v "$repo:/src" -w /src python:3.12-slim sh -c \
    'pip install -q -U pip; pip lock -q -r python/requirements.txt -o /dev/stdout' > "$body"

if ! grep -q '^lock-version' "$body"; then
    echo "refresh_lock: the container produced no lock file; nothing was written." >&2
    exit 1
fi

# `pip lock` writes no commentary, and the header is the only place that says
# why this file is Linux-only. Keep it.
if grep -q '^lock-version' python/pylock.toml; then
    sed -n '1,/^lock-version/p' python/pylock.toml | sed '$d' > "$header"
fi

cat "$header" "$body" > python/pylock.toml.new
mv python/pylock.toml.new python/pylock.toml

echo "refresh_lock: wrote python/pylock.toml."
echo "refresh_lock: rebuild the image and run the suite before committing."
