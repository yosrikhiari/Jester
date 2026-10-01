<p align="center">
  <img src="docs/img/logo.png" alt="Jester" width="180">
</p>

<h1 align="center">Jester</h1>

<p align="center">
  <strong>Multi-agent web intelligence pipeline. A Go worker scrapes community forums, a chain of LLM agents distils the complaints into scored product ideas, and an operator console lets you verify every claim against the post it came from.</strong>
</p>

<p align="center">
  <a href="https://github.com/yosrikhiari/Jester/actions/workflows/ci.yml"><img src="https://github.com/yosrikhiari/Jester/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

<p align="center">
  <img src="docs/img/jester-ideas.png" alt="Jester operator console — Ideas: 1,468 scored ideas, each with its nugget and thread evidence" width="900">
</p>
<p align="center"><sub>The operator console on the real archive (2026-09-22): <b>98,890 nuggets → 1,468 scored ideas</b> from 130 sources. Each row carries demand / feasibility / competition from the critic agent and the evidence count behind it; open one to read the citations. <a href="docs/img/jester-overview.png">Overview</a> · <a href="docs/img/jester-signals.png">Problem signals</a>.</sub></p>

---

## What It Does

Jester crawls community forums — Reddit, Hacker News, Discourse, Lemmy, Stack Exchange, GitHub Issues, YouTube, podcasts — extracts complaints and unmet needs through a chain of LLM agents, deduplicates and clusters them in a vector store, then synthesizes and scores product ideas. Everything is reviewable: an idea links to its nuggets, a nugget links to the comment, the comment links to the post.

**The pipeline:**

```
130 sources  →  Go worker (scrape / ingest)  →  SQLite handoff queue
                                                        ↓
Operator console  ←  Python pipeline  ←────────  LLM agent chain
   (:8619)                  ↓                           ↓
                   ClickHouse + CSV            Qdrant (vectors + dedup)
```

**Numbers from the working archive**, not a demo: 35,118 ingest batches, 98,890 nuggets, 1,468 scored ideas, 205 recorded runs.

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| **Ingestion** | Go, CloakBrowser (stealth Chromium) | Scrapes via documented APIs or a headless browser; deposits raw comments into a SQLite handoff queue |
| **Processing** | Python, Ollama / Groq | Specialised agents — extractor, synthesizer, critic, labeller, archivist — each with its own provider, model and thresholds |
| **Storage** | SQLite, Qdrant, ClickHouse | SQLite for pipeline state; Qdrant for semantic dedup (0.87 cosine) and cluster search; ClickHouse for the queryable problem-signal archive |
| **Console** | Python stdlib HTTP, vanilla JS | The operator UI at `:8619` — no build step, ships inside the Python package |
| **Orchestration** | Docker Compose | One command for the stack; profiles add the scraping browser, local models and the archive |

## LLM Agent Chain

Each agent is independently configurable (provider, model, temperature, token budget):

1. **Extractor** — pulls structured pain points out of raw forum comments
2. **Synthesizer** — clusters related pain points and proposes product ideas
3. **Critic** — scores and challenges each idea on demand, feasibility and competition
4. **Labeller** — categorises and tags for console filtering
5. **Archivist** — deduplicates against the existing corpus by vector similarity

Providers: **Ollama** (fully offline) and **Groq** (cloud, OpenAI-compatible).

## Problem signals — a second, deterministic pipeline

<p align="center">
  <img src="docs/img/jester-signals.png" alt="Jester operator console — Problem signals: 455 live records, 3 buyer signals, 32 practitioner, 0 failed fetches" width="900">
</p>

The idea pipeline is generative and probabilistic. Sitting beside it is a **problem-signal collector** built to a different standard, because its output has to survive review:

> **Scope, fields and access status: [docs/scope-and-access.md](docs/scope-and-access.md)** — what each source collects and has produced, the 30-field map, and the open Reddit access decision. Regenerate it with `python -m jester.cli signals scope --db data/signals-live.db --out docs/`.
>
> **W1 evidence: [docs/w1-pack/](docs/w1-pack/README.md)** — a fresh clone, one command run twice (5 rows, then 0 new), the field map and the 20/20 fixture gate, captured to files.

- **No model in the acceptance path.** Classification is rules over text — same input, same verdict, no network and no quota. A model may one day propose new phrases; it never decides one.
- **Search, not listing walks.** Measured: reading a community's `/new/` returns a 0.65% buyer rate, because what a community posts today is mostly not a buyer. Searching for the sentences a buyer actually writes is the same number of requests at a far higher hit rate.
- **Every record says how it was collected.** `mode` is a column (`live` or `synthetic`), so a synthetic row can never be counted as a real one — the count is a `GROUP BY`, not a promise in a report.
- **Failures are rows, not log lines.** A query that fails is stored, counted and re-runnable by the recovery pass. A run that collects nothing is a valid run and is recorded as one.
- **Verdicts are checkable.** Each record stores the words that classified it: `buyer signal: owner_voice + work_named [matched: i run a, reporting, how do you]`.
- **Nothing is inferred about people.** `company` and `buyer_intent` stay `unknown` unless the source states them (a job advert names its company). Handles are stored as published, never enriched, never contacted.

```bash
python -m jester.cli signals sources     # the collectors, and the authority each runs on
python -m jester.cli signals run         # collect from an approved source
python -m jester.cli signals load        # SQLite -> ClickHouse, idempotent
python -m jester.cli signals queries     # the saved reconciliation SQL
python -m jester.cli signals digest      # weekly digest: repeated needs, links, unknowns
python -m jester.cli signals evidence    # one folder a reviewer can read in an hour
```

The archive is a `ReplacingMergeTree` keyed on the record id alone, so a repeat import collapses to the same rows; every saved query says `FINAL`, because a count without it counts parts rather than records. `signals evidence` reconciles SQLite against ClickHouse against the CSV and exits non-zero if the three disagree.

## Journal — research, experiment, publish (in progress)

A third part, separate from the problem-signal work above and not part of its scope or milestones. The journal turns a question into a published engineering write-up, and refuses to publish anything it cannot trace: every number must point at an experiment run or a query, and every quote at a stored copy of its source.

Built so far (slice J0): its own database (`data/journal.db`), articles as Markdown in their own git repository (`data/journal/`, so drafts stay private and every saved version is a commit), and a state machine where each step forward has a gate:

```
captured -> proposed -> chosen -> researching -> experimenting -> drafting
  -> in_review -> ready -> published -> shared -> measured      (+ parked, abandoned)
```

The decisions — choosing a topic, approving an experiment plan, approving a version, publishing, approving each post — are gates only a person can open: approvals from any other actor are recorded and never counted. Gates for the later slices (experiment runs, posting, metrics) already read their tables, so until those slices land the honest answer is "blocked", not a silent pass.

Slice J1 adds the proof checks a saved version must pass before review. Each one is offline and deterministic, and stores its verdict against that version:

| Check | Passes when |
|---|---|
| `citations` | every external link has a saved copy that answered 2xx, and every quote of 5+ words is attributed to a link whose saved copy contains those words |
| `numbers` | every number in the prose links to its proof — `[91.2%](run:12#accuracy)` (value checked against the run), `[3.5%](https://…)` (must appear in the saved copy), `[4 hours](obs:notes)` — or names go in backticks |
| `lint` | the prose does not read as generated (Wikipedia's "signs of AI writing" list plus structure signals) and the title is not clickbait |
| `origin` | no paragraph a tool wrote speaks in the first person, gives an opinion or claims an observation |

`journal snapshot` is the only network step: it saves a copy of each cited page (status, final URL, text), so a page that later disappears still has its words on file. `journal fixtures` runs the checkers against labelled cases: 31 citation cases, a clean draft plus 12 planted unbacked numbers, and 15 + 15 lint paragraphs (local-model output vs. public-domain PEP prose), plus a held-out set the rules never saw — 10/15 AI paragraphs flagged, 0/15 human.

```bash
python -m jester.cli journal new "Rules vs a decision model" --question "Which sorts hiring posts best?"
python -m jester.cli journal show rules-vs-a-decision-model     # brief, versions, and what blocks the next step
python -m jester.cli journal set rules-vs-a-decision-model --metric "macro F1" --baseline "the rule set"
python -m jester.cli journal approve rules-vs-a-decision-model choose
python -m jester.cli journal state rules-vs-a-decision-model chosen
python -m jester.cli journal save rules-vs-a-decision-model -m "first draft"
python -m jester.cli journal snapshot rules-vs-a-decision-model   # save copies of every cited page
python -m jester.cli journal check rules-vs-a-decision-model      # citations, numbers, lint, origin
python -m jester.cli journal fixtures                             # the checkers against labelled cases
```

## Quick Start

```bash
git clone https://github.com/yosrikhiari/Jester.git
cd Jester
cp .env.example .env

# Full stack in mock mode — no API keys, no model downloads
docker compose up
```

Then open the operator console at **http://localhost:8619**.

The default stack runs in **mock mode** (`JESTER_MOCK=1`): the whole pipeline works offline against fixture data.

### Profiles

```bash
docker compose --profile live up        # CloakBrowser (stealth scraping) + Ollama (local models)
docker compose --profile signals up -d clickhouse   # the problem-signal archive
```

Set provider keys in `.env` (documented in `.env.example`).

## Services

| Service | Port | Description |
|---------|------|-------------|
| `jester-console` | 8619 | **The operator console** — serves the UI, the API and the scheduler |
| `go-worker` | — | Go ingestion worker; scrapes sources into the handoff queue |
| `qdrant` | 6333 | Vector database for semantic dedup and cluster search |
| `cloakbrowser` | 9222 | *(live profile)* Stealth Chromium for bot-detected sites |
| `ollama` | 11434 | *(live profile)* Local LLM inference |
| `clickhouse` | 8123 | *(signals profile)* Problem-signal archive, HTTP interface only |
| `frontend` | 5173 | *(scaffold)* React/Vite shell — see the note below |

> **On the React frontend.** `frontend/` is a Vite + React 19 scaffold, not the product's UI. The console that ships and is screenshotted above is served by Python at `:8619` and written in vanilla JS with no build step, so the UI travels with the package. The scaffold is kept for a future port and is not where the work is.

## Configuration

All tuning lives in `config/`:

- **`sources.yaml`** — the 130 sources: 44 Discourse forums, 33 subreddits, 16 real-estate portals, 10 GitHub repos, 8 Lemmy communities, 6 Stack Exchange sites, 6 podcasts, 3 Hacker News feeds, 3 Steam apps, 1 YouTube channel
- **`thresholds.yaml`** — per-agent provider/model, scraping depth per platform, dedup cosine threshold, prefilter rules
- **`scraper.yaml`** — browser stealth settings, request delays, proxy configuration
- **`signal_rules.yaml`** — the problem-signal rule set: scope, search phrases, scoring families and thresholds

## Project Structure

```
.
├── python/              # Pipeline, CLI, agents, console server
│   └── jester/
│       ├── agents/      # Extractor, synthesizer, critic, labeller, archivist
│       ├── console/     # Operator console: API + the static UI it serves
│       ├── signals/     # Problem-signal collector, rules, ClickHouse, digest
│       ├── journal/     # Research -> experiment -> article, with gated states
│       └── ...
├── go/                  # Ingestion worker and probe commands
│   ├── cmd/             # worker, forumprobe, siteprobe, renderprobe
│   └── internal/        # Adapters (Reddit, HN, Discourse, Lemmy, YouTube, …)
├── frontend/            # React 19 + Vite scaffold (not the shipped UI)
├── config/              # YAML configuration
├── docs/                # Screenshots and the problem-signal work notes
├── docker-compose.yml   # Full stack orchestration
└── .env.example         # Environment variable documentation
```

## Tests

```bash
PYTHONPATH=python python -m pytest python/tests -q
```

940 Python tests and 285 Go test functions (counted 2026-09-24; CI runs both on every push). They lean towards the things that can be quietly wrong rather than the things that are obviously wrong: that a repeat import creates no duplicates, that a failed fetch is counted rather than dropped, that a fixture row cannot be read as a live one, that a counting query says `FINAL`, and that the fixture gate itself fails when a category goes missing.

## Tech Stack

**Languages:** Python 3.11+, Go 1.22+

**Data:** SQLite (handoff queue + pipeline state), Qdrant (vectors), ClickHouse (signal archive)

**Models:** Ollama (local), Groq (cloud, OpenAI-compatible)

**Infrastructure:** Docker Compose, CloakBrowser (stealth Chromium), residential proxy support

## License

This project is for educational and personal use.
