# Quick start

Two ways in, both ending at the operator console or a green smoke run. Pick
the first if you have Docker; the second if you want the CLI with no
containers.

## The five-minute path: Docker, mock mode

```bash
git clone https://github.com/yosrikhiari/Jester.git
cd Jester
cp .env.example .env      # JESTER_MOCK=1 is already in it
docker compose up
```

Open **http://localhost:8619**. That is the operator console: the UI, the API
and the scheduler.

What is running while you read this:

- **Mock mode.** `JESTER_MOCK=1` makes the Go worker load fixtures instead of
  the live web, and `docker/config/thresholds.yaml` is bind-mounted over
  `config/thresholds.yaml` with every provider set to `fake`. No API keys, no
  model downloads, and the whole pipeline still runs.
- **A throwaway archive.** The stack writes to the `jester_docker_data`
  volume, never to your host's `./data/`.
- **Qdrant** on `:6333` for dedup and cluster search.
- **The React scaffold** on `:5173`. That is not the product UI — see
  [the frontend ADR](adr/0001-frontend-strategy.md). The console at `:8619`
  is.

The first build takes a few minutes; later starts are seconds. Stop with
`docker compose down` (the volume survives), or `docker compose down -v` to
discard the demo archive.

Optional profiles:

```bash
docker compose --profile live up                    # CloakBrowser + Ollama
docker compose --profile signals up -d clickhouse   # the problem-signal archive
```

Provider keys go in `.env`; every variable is documented in `.env.example`.

## The native path: Python only

Requires Python 3.11+. Everything below runs from the repository root.

```bash
python -m pip install --only-binary :all: -r python/pylock.toml
export PYTHONPATH=python          # Windows PowerShell: $env:PYTHONPATH="python"
```

### 1. The deterministic pipeline (no model, no network)

The problem-signal collector is rule-based and fixture-backed, so it is the
fastest proof that the code and the database layer work:

```bash
python -m jester.cli signals export --db /tmp/signals.db --out /tmp/signals-out
python -m jester.cli signals export --db /tmp/signals.db --out /tmp/signals-out  # again
```

The second run must say `0 new, 5 seen again`: the same fixture set is
recognised, not duplicated. This exact pair is what CI runs on every push.

### 2. The smoke run (pipeline end to end)

```bash
python -m jester.cli smoke
```

`smoke` is a **self-check, not a connectivity probe**: for the run it pins
every model job to the deterministic stand-in and the vector backend to the
per-database local store, so the verdict says something about the code and
nothing about which servers happen to be up. The run goes into a disposable
database under the system temp directory; a previous smoke archive (and its
two vector stores) is replaced first.

It checks its own gates:

- a completed run summary **with** expected-vs-actual duration;
- at least one nugget above the §7.2 non-trivial bar;
- at least one **scored idea**, every citation of which names a nugget that
  exists.

It prints `SMOKE PASS` or one `SMOKE FAIL:` line per unmet gate, and exits
non-zero on failure. The run record stamps the stand-ins under
`models_used.overrides`, so "the smoke passed" is never confused with "the
models answered". The real-stack form is `--live`, refused until the external
stack migration lands.

`scripts/smoke.sh` is the same command with `PYTHONPATH` set and `PYTHON`
overridable (`PYTHON=python scripts/smoke.sh` on machines where `python3` is
a Windows Store alias).

### 3. A real run and the console

```bash
JESTER_MOCK=1 python -m jester.cli run --db data/jester.db   # fixture corpus
python -m jester.console --db data/jester.db --port 8619
```

Drop `JESTER_MOCK=1` once the Go worker is filling the queue with real
fetches. `jester models` prints which provider and model each job resolved to
before you spend a single call; `jester doctor` checks the archive and the
backends.

## Where next

- Moving a model change between `config/` and `config-live/`:
  [config-promotion.md](config-promotion.md)
- What the retention sweep removes, and what it leaves:
  [data-retention.md](data-retention.md)
- The problem-signal pipeline's scope and access status:
  [scope-and-access.md](scope-and-access.md)
- Why the shipped UI is the Python console and `frontend/` is a scaffold:
  [adr/0001-frontend-strategy.md](adr/0001-frontend-strategy.md)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SMOKE FAIL: no scored idea` after deleting an old smoke database yourself | the derived vector stores beside it survived, so synthesis merges into an idea the database no longer has | remove `<name>.qdrant/` and `<name>.ideas.qdrant/` beside the database as well (`smoke` does this itself for its own temp archive) |
| `vector store unavailable: connection refused` on `jester run` | `.env` points `JESTER_QDRANT_URL` at a server that is not running | start Qdrant, or run with `JESTER_QDRANT_URL=` for the local store |
| `SMOKE FAIL: fewer than 1 non-trivial nugget` | every nugget was judged trivial (see `ALL_TRIVIAL` in `jester runs`) | expected on a corpus of noise; not a code failure |
| `python3` opens the Microsoft Store | Windows app-alias stub, not an interpreter | use `python`, or set `PYTHON=python` before `scripts/*.sh` |
