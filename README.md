<p align="center">
  <img src="download6.png" alt="Jester" width="180">
</p>

<h1 align="center">Jester</h1>

<p align="center">
  <strong>Multi-agent web intelligence pipeline that scrapes community forums, extracts pain points with LLMs, and surfaces scored startup and product ideas through a themed review dashboard.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white" alt="Go">
  <img src="https://img.shields.io/badge/React_19-TypeScript-61DAFB?logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Qdrant-Vector_DB-DC382D" alt="Qdrant">
  <img src="https://img.shields.io/badge/Ollama-Local_LLM-000000" alt="Ollama">
</p>

---

## What It Does

Jester crawls community forums (Reddit, Hacker News, YouTube, Discourse, Stack Exchange, GitHub Issues), extracts user complaints and unmet needs through a chain of LLM agents, deduplicates and clusters them into a vector store, then synthesizes and scores actionable product ideas — all surfaced through a jester-themed React dashboard.

**The pipeline:**

```
Sources (80+)  →  Go Worker (scrape/ingest)  →  SQLite handoff queue
                                                      ↓
React Dashboard  ←  Python Console (serve/cron)  ←  LLM Agent Chain
                                                      ↓
                                              Qdrant (vectors + dedup)
```

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| **Ingestion** | Go, CloakBrowser (stealth Chromium) | Scrapes forums via documented APIs or headless browser; deposits raw comments into a SQLite handoff queue |
| **Processing** | Python, Ollama / Groq | Chain of specialized agents — extractor, synthesizer, critic, labeller, archivist — each with configurable LLM provider and thresholds |
| **Storage** | SQLite, Qdrant | SQLite for the relational pipeline state; Qdrant for semantic dedup (0.87 cosine threshold) and cluster search |
| **Frontend** | React 19, TypeScript, Vite | Review dashboard for browsing pain points, nuggets, and scored ideas with filtering and bulk actions |
| **Orchestration** | Docker Compose | One command brings up the full stack; `--profile live` adds CloakBrowser and Ollama |

## LLM Agent Chain

Each agent is independently configurable (provider, model, temperature, token budget):

1. **Extractor** — pulls structured pain points from raw forum comments
2. **Synthesizer** — clusters related pain points and generates product/startup ideas
3. **Critic** — scores and challenges each idea for feasibility and market fit
4. **Labeller** — categorizes and tags for dashboard filtering
5. **Archivist** — deduplicates against the existing corpus via vector similarity

Supported providers: **Ollama** (fully offline), **Groq** (cloud, OpenAI-compatible).

## Quick Start

```bash
# Clone and configure
git clone https://github.com/yosrikhiari/Jester.git
cd Jester
cp .env.example .env

# Run the full stack (mock mode — no API keys needed)
docker compose up

# Open the dashboard
# http://localhost:5173
```

The default stack runs in **mock mode** (`JESTER_MOCK=1`) — the entire pipeline works offline with fixture data, no API keys or model downloads required.

### Live mode

```bash
# Adds CloakBrowser (stealth scraping) and Ollama (local LLM)
docker compose --profile live up
```

Set your provider keys in `.env` (see `.env.example` for documentation).

## Services

| Service | Port | Description |
|---------|------|-------------|
| `frontend` | 5173 | React/Vite dashboard with hot-reload |
| `jester-console` | 8619 | Python operator console — serves the API, runs scheduled cron jobs |
| `go-worker` | — | Go ingestion worker — scrapes sources and deposits into the handoff queue |
| `qdrant` | 6333 | Vector database for semantic dedup and search |
| `cloakbrowser` | 9222 | *(live profile)* Stealth Chromium for bot-detected sites |
| `ollama` | 11434 | *(live profile)* Local LLM inference |

## Configuration

All tuning lives in `config/`:

- **`sources.yaml`** — the 80+ community sources to scrape (subreddits, HN, Discourse forums, YouTube channels)
- **`thresholds.yaml`** — per-agent LLM provider/model selection, scraping depth per platform, dedup cosine threshold, prefilter rules
- **`scraper.yaml`** — browser stealth settings, request delays, proxy configuration

## Project Structure

```
.
├── python/              # Core pipeline, CLI, agents, console server
│   └── jester/
│       ├── agents/      # Extractor, synthesizer, critic, labeller, archivist
│       ├── console/     # Operator console serving /api and the dashboard
│       └── ...
├── go/                  # Ingestion worker and probe commands
│   ├── cmd/             # worker, forumprobe, siteprobe, renderprobe
│   └── internal/        # Adapters (Reddit, HN, Discourse, YouTube, StackExchange)
├── frontend/            # React 19 + TypeScript + Vite dashboard
├── config/              # YAML configuration (sources, thresholds, scraper)
├── docker/              # Dockerfiles for Python, Go, and frontend
├── docker-compose.yml   # Full stack orchestration
└── .env.example         # Environment variable documentation
```

## Tech Stack

**Languages:** Python 3.11+, Go 1.22+, TypeScript

**Backend:** SQLite (handoff queue + pipeline state), Qdrant (vector store), Ollama / Groq (LLM providers)

**Frontend:** React 19, Vite, TypeScript

**Infrastructure:** Docker Compose, CloakBrowser (stealth Chromium), residential proxy support

## License

This project is for educational and personal use.
