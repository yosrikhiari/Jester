# Build the Go ingestion worker, then ship a tiny runtime image.
FROM golang:latest AS build
WORKDIR /src
# Cache module downloads.
COPY go/go.mod go/go.sum ./
RUN go mod download
COPY go/ ./
# Static binary so it runs on a minimal base.
RUN CGO_ENABLED=0 go build -o /out/worker ./cmd/worker

FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Binary lives outside the bind-mounted /app so it survives the mount.
COPY --from=build /out/worker /usr/local/bin/worker

WORKDIR /app
# Mock mode enqueues fixture data into the shared SQLite queue, then the
# container stays up for `docker compose exec go-worker worker ...` re-runs.
CMD ["sh", "-c", "worker -config /app/config -db /app/data/jester.db \
     -fixture /app/go/testdata/reddit_thread_1.json && tail -f /dev/null"]
