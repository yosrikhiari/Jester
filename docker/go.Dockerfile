# Build the Go ingestion worker, then ship a tiny runtime image.
#
# Same golang pin as Dockerfile.python (F-07, 2026-10-29, go/go.mod declares
# `go 1.26`): one digest for one toolchain, so the console image and this
# one cannot rebuild the worker against different Go versions without an
# edit saying so.
FROM golang:1.26@sha256:670bb5aa5349ae03b8516764718486f4f16399134bdc9acd0c46a23f043ab05c AS build
WORKDIR /src
# Cache module downloads.
COPY go/go.mod go/go.sum ./
RUN go mod download
COPY go/ ./
# Static binary so it runs on a minimal base.
RUN CGO_ENABLED=0 go build -o /out/worker ./cmd/worker

# ONE uid for both writers (R4-05). This worker and the console container write
# the same SQLite database through a shared volume, and that shared handoff
# queue IS the cross-process contract. Different uids on one file is precisely
# the corruption class this image used to ship: whichever container created the
# file decided who could write it, and the loser read "unable to open database
# file". So the uid is the console image's — 10001 — and it is FIXED rather
# than "whatever useradd picks", so a volume written by one build stays
# writable by the next.
#
# /app/data is created and chowned HERE, before USER, because Docker copies a
# mount point's ownership into a fresh named volume. Whichever of the two
# containers creates `jester_docker_data` first therefore initialises it with
# the uid both of them run as. See docker/Dockerfile.python for the other half.
#
# MIGRATION (one time): a volume created by the old root-era image is still
# owned by root and 10001 cannot write it (the console entrypoint says so on
# stderr). Either discard the throwaway archive:
#     docker compose down -v
# or keep it and hand it to 10001 once:
#     docker run --rm -v jester_jester_docker_data:/d alpine chown -R 10001:10001 /d
# After that the volume is owned by 10001 for good; this is not a per-start
# action.
FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Binary lives outside the bind-mounted /app so it survives the mount.
COPY --from=build /out/worker /usr/local/bin/worker

WORKDIR /app
# The group is created with the same gid so a chown by name and a uid:gid pair
# mean the same thing. --no-log-init keeps the image free of a spurious
# lastlog entry; the home directory is created because some tools want $HOME.
RUN groupadd --gid 10001 jester \
    && useradd --uid 10001 --gid 10001 --no-log-init \
       --home-dir /home/jester --create-home --shell /usr/sbin/nologin jester \
    && mkdir -p /app/data \
    && chown -R 10001:10001 /app
# Everything above runs as root at BUILD time; from here the container is uid
# 10001 for its whole life, exactly like the console.
USER 10001:10001
# Mock mode enqueues fixture data into the shared SQLite queue, then the
# container stays up for `docker compose exec go-worker worker ...` re-runs.
CMD ["sh", "-c", "worker -config /app/config -db /app/data/jester-docker.db \
     -fixture /app/go/testdata/reddit_thread_1.json && tail -f /dev/null"]
