# Scrapling-informed enhancement strategy for Jester

**Date:** 2026-11-03 · **Scope:** research only — no Jester code touched · **Analyzed at:** Jester `main` = `15032e4` (jester-v3-hardening tree), Scrapling `main` = `95cf901` (v0.5.0, shallow-cloned to `%TEMP%\opencode\scrapling`)
**Licenses:** Scrapling BSD-3-Clause (LICENSE, pyproject) · Jester MIT — pattern reuse is fully permissive; code porting is unnecessary anyway (Go vs Python, different homes for every concern).

---

## 8. Executive summary (read this first)

Scrapling and Jester are both scraping systems, but they are **constitutionally different animals**:

- **Scrapling** is a *stateless, interactive library* (BSD-3, single-author, fast-moving): a Parser (`Selector`) wrapping lxml with CSS/XPath/text search, **element-fingerprint storage + adaptive relocation**, curl_cffi fetchers with browser impersonation, Playwright stealth fetchers, and a small anyio spider engine with robots/Crawl-delay handling, autothrottle, checkpoints and a dev-mode response cache. Correctness is the *caller's* problem at call time.
- **Jester** is a *long-running, unattended, curated archive*: a Go ingestion worker walking `sources.yaml` (Reddit/HN/Discourse/YouTube/StackExchange/GitHub/Lemmy/Steam/podcasts + 16 real-estate portals), parsing through **declarative YAML site profiles** (LD+JSON / `__NEXT_DATA__` / regex-anchored blocks), identity-managed stealth via a licensed CloakBrowser CDP multiplexer, an LLM agent pipeline over the resulting text, and a house-wide insistence on *honest, measured truth* (snapshot fixtures, coverage floors, `listingeval` gates, the R4 register).

**The verdict that matters:** Jester's per-page extraction machinery is **already stronger than Scrapling's in the dimensions that protect it** (schema.org JSON-LD walking, `__NEXT_DATA__` traversal, dual-source field patterns, block-boundary contracts, published-detail provenance rules like R29). Scrapling's celebrated "adaptive extraction" would, if adopted as designed, violate Jester's own documented principle — in `go/internal/siteprofile/siteprofile.go:748-754`: *"Wrong data that looks right is the worst outcome available here."* Scrapling's relocation **silently substitutes a lookalike element**; Jester must never do that unattended.

What transfers is not the library surface. It is **three system properties Scrapling has and Jester lacks**, each cheap and each aimed at the real risk of a 16-portal, 9-platform archive that runs while you sleep:

| # | Borrow (adapted) | Why Jester needs it (evidence) | Case | Priority |
|---|---|---|---|---|
| 1 | **Run-outcome telemetry plane** — per-source, per-run parse/fetch yield records (rows, anchor hit-rate, LD+JSON blocks seen, per-field coverage, challenge counts, bytes, latency), persisted additively and diffed run-over-run like an SRE error budget **inspired by Scrapling `relocate()`'s scoring + `CrawlStats`** | A portal that drifts *gradually* — an anchor that stops matching on page 3, prices arriving on ⅔ of tiles — is caught today **only** if someone remembers to run `listingeval` by hand (`go/cmd/listingeval/main.go:1-9` says exactly this). Every profile fails "loudly" only at total-zero; partial drift reads as a quiet normal day. | Architectural adaptation | **P0** |
| 2 | **Wire the existing `listingeval` gates into the scheduled run** (grade the run's own harvest; alert into the console/signals instead of a manual CLI) — the tool and the gate math already exist | `listingeval` exists but nothing invokes it automatically (grep: only README, sonar exclusions, itself). The "extraction eval" philosophy (CI-passing, NOT MEASURED until labels) shows the intent: gates that run themselves. | Direct adoption | **P0** |
| 3 | **Robots.txt drift watcher + Crawl-delay honor** (Scrapling `RobotsTxtManager`, `engine.py:107-146`) | Portal profiles hard-code robots verdicts with dates (`config/profiles/zoopla.yaml:1-12` "Checked 2026-09-05") that go stale silently; `trackCrawl-delay` is read by Scrapling per domain and by nobody in Jester (HTTP mode honors `Retry-After` only — `realestate/http.go:39-67`). Jester is compliance-first by constitution; the watcher is cheap and on-brand. | Architectural adaptation | **P1** |
| 4 | **Declare the implied dependency + a capture/replay dev loop** (`Fetcher.dev cache` pattern in `spiders/engine.py:58-63`) | `fetchers/cloak.py:221` lazily imports `playwright.sync_api` — **not declared** in `python/pyproject.toml` (deps: PyYAML, qdrant-client, tzdata, ollama). The profile-dev workflow ("capture a page, iterate the YAML against it") exists as curated fixtures but has no one-command capture. | Direct adoption | **P1** |
| 5 | **TLS/H1 stack identity for `fetch:http` portals** (Scrapling's curl_cffi `impersonate` insight) | `realestate/http.go:23-30` presents Chrome/131 UA **over Go's stock TLS** — an un-browser-like JA3. Presently small surface (browser-mode profiles dominate), but it is the same class of mismatch Scrapling exists to fix; decide between uTLS and routing http-mode portals through cloakserve. | Selective inspiration | **P1** |
| 6 | Rich extras (adaptive-similarity *diff view* for the operator; fallback pattern chains in profiles; adapter-matrix page; harvest checkpoints for `listingharvest`; MCP-style browser tools for the console chat) | Each maps to a real file/method in Scrapling; each is optional polish, not a need. | Selective inspiration | **P2/P3** |

**Deliberately rejected** (with reasons, so this doesn't resurface): StealthyFetcher/Camoufox duplication (CloakBrowser already gives per-source *stable warmed identities* — `go/internal/cloakbrowser/cloakbrowser.go:5-12` — which StealthyFetcher cannot), the full spider engine (Jester's constitution is *curated sources, no link discovery*), silent adaptive substitution, wholesome adoption of Scrapling's MCP server (Jester's chat already has a tool protocol), Scrapy integration.

Full evidence, per-capability comparison, and the phase plan follow.

---

## 1. Repository analysis

### 1.1 Scrapling (v0.5, `95cf901`) — what it actually is

Public API (`scrapling/__init__.py:13-23`, lazy imports): `Selector`, `Selectors`, `Fetcher`, `AsyncFetcher`, `FetcherSession`, `StealthyFetcher`, `StealthySession`, `AsyncStealthySession`, `ProxyRotator`. Package layout is `parse` + `fetch` + `spider` + `agents-tooling`, with `tests/` covering every subsystem (spiders, fetchers incl. async/sync sessions, AI browser tools, integrations, CLI).

**A. The parser and the adaptive engine** — `scrapling/parser.py` + `scrapling/core/storage.py`

- `Selector` (`parser.py:77-110`) wraps `lxml.html` (not subclassed — "not pickleable"; composition with `_root` instead). Universal APIs: `css()`, `xpath()`, `find_all()` (tags/attrs/regex/callable — `parser.py:701-745`), `find_by_text()`, `find_by_regex()`, `find_similar()`, plus navigation (`parent/children/siblings/path/next/previous`, `parser.py:389-466`) and typed wrappers (`TextHandler`, `AttributesHandler` — `core/custom_types.py`).
- **Fingerprints** (`core/utils/_utils.py:84-114`): `element_to_dict` captures `tag`, cleaned `attributes`, direct `text`, ancestor `path` tuples, `parent_name/attribs/text`, `siblings` tag tuple, `children` tag tuple. Stored in `SQLiteStorageSystem` (`core/storage.py:74-120`): one SQLite file, `UNIQUE(url, identifier)`, identifier hashed sha256+length (`storage.py:61-70`), site scoped by base domain via `tld` (`storage.py:23-39`), `WAL` + `RLock` ("thread-safe", `storage.py:87-93`), class itself `lru_cache(1)` = one instance per process. **Only the latest fingerprint per (url, identifier) is kept — no layout history.**
- **Adaptive relocation** (`parser.py:513-569`): on a selector miss with `adaptive=True`, fetch the stored fingerprint by identifier and **score every node in the tree** (`__calculate_similarity_score`, `parser.py:814-879`): tag equality, `difflib.SequenceMatcher.ratio()` over text / path / parent tag / parent text / sibling tags, attribute-dict similarity (keys ×0.5 + values ×0.5, `parser.py:882-886`), and dedicated class/id/href/src ratios. Mean-of-checks ×100; keep **all ties at the max score** if ≥ `percentage` (default 40). `auto_save=True` then **re-saves the relocated winner** (`parser.py:668-679`) — the store learns the new layout. Miss below threshold → warning log, empty result.
- `css()` splits comma selectors so each stores under its own canonical id (`parser.py:596-624`).
- Extra finders: `find_similar` reuses `__are_alike` (sibling structural likeness, `parser.py:979-1084`); `find_by_text/regex` are convenience selectors.

**Honest properties of this machinery:** the miss-path resettlement is one full-tree scan with a `SequenceMatcher` per node per touched attribute — O(N) with Python-level regex/difflib cost, paid only on misses (the repo even ships `benchmarks.py` at root to assert speed claims). It recovers a *structurally similar element*, but knows nothing about *field-level* drift (prices vanishing while the container still matches), keeps no replacement history, and substitutes **silently above the threshold** — right for interactive scripts, dangerous for an unattended archive.

**B. The spider engine** — `scrapling/spiders/`

`CrawlerEngine` (`spiders/engine.py:29-463`) is a compact Scrapy-equivalent in anyio:
- **Concurrency**: one global `CapacityLimiter` + per-domain limiters (`engine.py:77-79`, `_rate_limiter`, `engine.py:139`).
- **robots.txt**: `RobotsTxtManager` with prefetch for seed domains (`engine.py:49-56, 337-355`), per-domain delay = max(spider `download_delay`, robots `Crawl-delay`/`Request-rate`) — cached (`engine.py:107-146`), and `can_fetch` counting disallows (`engine.py:188-193`).
- **Dev-mode response cache**: disk cache keyed by request fingerprint, replayed instead of re-fetching (`engine.py:58-63`, `200-212`, `267-268`) — a "develop the spider offline" affordance.
- **AutoThrottle** (`spiders/throttle.py:31-99`): per-domain delay from observed latency ÷ target concurrency, doubled on blocks honoring `Retry-After` (`block_backoff`), never dropping below the robots floor.
- **Blocked-request protocol** (`engine.py:241-265`): `spider.is_blocked(response)` hook → bounded `max_blocked_retries`, re-enqueue with lowered priority, `dont_filter=True`, proxy dropped, plus the spider's own `retry_blocked_request` hook (swap proxy/browser).
- **Scheduler** (`spiders/scheduler.py:12-87`): fingerprint-deduplicated enqueue/dequeue with BFS/DFS, checkpoint snapshot/restore; **checkpoints** on an interval (`engine.py:299-336`); pause/force-stop; `CrawlStats` (requests/bytes/statuses/blocked/robots-disallowed/cache hits — `engine.py:206-231`).
- **Templates** (`spiders/templates/`): `shopify.py` (canonical Shopify JSON walking), `sitemap.py`, `feed.py`, `site_to_markdown.py`, `crawler.py` — one-file spider presets.

**C. The fetch stack** — `scrapling/fetchers/` + `scrapling/engines/static.py`

- Static transport is **curl_cffi** (`pyproject.toml:75`, `fetchers/requests.py:28-65`, docstring "based on `curl_cffi`"): `impersonate` picks a real browser TLS/JA3+header set (list allowed → random choice per request, `engines/static.py:37-48`), plus proxy/proxy_auth/timeout/retries defaults via class-level `.configure()` merged into every call (`fetchers/requests.py:17-25`), and a validated `ProxyRotator` distinct from static proxies (`engines/static.py:90-96`, `toolbelt/proxy_rotation.py`).
- `FetcherSession` reuses one client; responses are `Selector`-ready (parse built into the response).
- **Stealth** (`fetchers/stealth_chrome.py`, `engines/_browsers/_stealth.py` + `_config_tools.py`): Playwright-driven stealth Chromium with sessions (sync+async), plus the tests for a Cloudflare-challenge solver path (`tests/fetchers/test_cloudflare_*.py`). *Not deeply traced here* — the stealth internals were skimmed at file/test-name level; conclusions about stealth internals are correspondingly modest.
- **AI layer** (`core/ai/server.py`, `_browser_actions.py`, `_snapshot_search.py`, `_network_formatting.py`): an MCP server exposing the browser as tools for LLM agents (fill fields, mouse, press, extract content, screenshot, network history) — "the page as an agent toolset", shipped with `agent-skill/Scrapling-Skill/` examples.
- `integrations/scrapy.py`: pipes Scrapling's `Selector` into a Scrapy pipeline.

**Engineering decisions of note:** one SQLite-per-tool simple state under `UNIQUE` upserts; anything that can fail returns "empty + warning" rather than throwing (library ergonomics); every knob is a per-call override over a class-level default (sessions configure, calls override); lazy imports everywhere (`__init__` maps, Playwright imported only `StealthyFetcher`-side); custom lightweight translators (`core/translator.py` CSS→XPath) instead of heavyweight deps; orjson for storage speed; an explicit benchmarks script. **Limitations:** single-author risk; stateless fingerprint store (no drift history); relocation cost is quadratic-ish in pathological pages and Python-bound; stealth story depends on external fingerprint ecosystems (`license=BSD-3` is fine, upstream churn is real); no built-in crawl politeness *across* the library (the Spider layer is optional and separate).

### 1.2 Jester (at `15032e4`) — what it actually is

**Constitution (from the tree's own words):** a *curated, unattended intelligence archive*: sources are declared in `sources.yaml` (Python `sources.py:1-90` — per-platform `kind`s, `enabled` flag, `SUPPORTED_KINDS` oracle with the honest-Gap pattern); the **Go worker** walks them ("Command worker…" `go/cmd/worker/main.go:1-4`); aim is one ingest_batch queue into SQLite; a Python console (aiohttp + agent pipeline) presents/labels/exports; house rules are explicit everywhere (R29 "don't invent data", "wrong data that looks right is the worst outcome", NOT MEASURED eval blocks, known-absent registers with dates).

**Ingestion (Go):** `go/cmd/worker/main.go` — config walk, `-only` run-scoped selection ("must not silently disable the other fourteen", `main.go:53-57`), caps (`max-comments/posts`), prefilter thresholds (`main.go:83-89`), mock-first (fixtures) with `-live` behind a flag, `backfill` requires `-live` explicitly (`main.go:91-97`). Adapters per platform in `go/internal/{reddit,discourse,github,hackernews,lemmy,stackexchange,steam,podcast,youtube}`; **real estate** in `go/internal/realestate` driven by YAML profiles (below).
- **Stealth/identity** (`go/internal/cloakbrowser/cloakbrowser.go`): cloakserve is a CDP *multiplexer*; `fingerprint` seed ⇒ **one stable canvas/WebGL/font identity + cookie jar per curated source, warmed between runs** (`cloakbrowser.go:5-12, 71-84`); timezone/locale/proxy/geoip ride the connection query. Python mirrors this for its own CDP adapters (`python/jester/fetchers/cloak.py`) with challenge regexes ("Prove your humanity", "Just a moment" — `cloak.py:104-123`) and a `blocked_response_action` policy (`pass_through|backoff|fail`), seconds-level pacing, and Reddit GraphQL-JSON capture with an SSR DOM fallback (`cloak.py:160-195, 228-270`).
- **HTTP path** (`go/internal/realestate/http.go`): redirects-following client (45 s), Chrome/131 static UA — "portals serve a different document to a client they do not recognise as a browser" (`http.go:19-24`) — and a **status taxonomy**: `Exhausted` (404/410 = pagination end), `RateLimited` (429/503/**202 as Redfin's soft block**, `http.go:58-67`), bounded retries honoring `Retry-After` ("Retrying a rate limit at the same pace is how a scraper turns a warning into a ban", `http.go:51-57`).
- **Extraction** (`go/internal/siteprofile/siteprofile.go`): declarative profiles under `config/profiles/*.yaml` (16 portals + drafts). Three modes — `ldjson` (schema.org walking), `nextdata` (`__NEXT_DATA__` dotted path), `anchored` (named-group anchor regex → id → block bounded by the *next* anchor with `block_len` backstop, optional `block_start` container back-up — `siteprofile.go:709-817`); fields = JSON path inside embedded LD+JSON *or* regex `pattern` on the block (`valuesFor`, `siteprofile.go:819+`), plus `transform`s, `filter` (drop boosted ads/cars — `siteprofile.go:138-144`), `classify` (deal_type from title), optional `detail` page fetches (`siteprofile.go:151-163`), and the "a row with no link is not a listing" validity rule (`siteprofile.go:802-812`, countedevidence: 4,141-listing run, 9/10 portals at 100% URL coverage).
- **Run pacing**: per-page `Pace(delay)` between pages (`realestate/realestate.go:43-80`), fail-depth bookkeeping (`firstErr` distinguishes "portal published nothing" from "every page 403'd" — `realestate.go:38-44, 82-87`), `pagePlan` honors an unset `page_param` as one page (not five, `realestate.go:91-110`).

**Intelligence (Python):** fetcher protocol `Fetch() → [{platform, source, thread_id, comments[]}]` (`fetchers/__init__.py`, mock-first); extractor agent maps *published* detail fields onto `Nugget` rows and **refuses to zero-out absences** ("overwriting that with a concrete value invents data (R29)" — `agents/extractor.py:62-84`); LLM = Ollama w/ FakeLLM for mock mode (`pyproject.toml:11-14`, `llm.py`); signals backend (`signals/` health/leads/digest/fixture calls), journal, backup/export, console + chat with tools.

**Instrumentation (the house specialty):**
- Parse-quality: fixture-locked coverage tests per profile — `TestReportFieldCoverage` (report-only instrument) + `TestFieldCoverageFloor` thresholds, each shipped profile measured against its own captured real page (`go/internal/siteprofile/coverage_eval_test.go:16-90`).
- At-volume gates: `go/cmd/listingeval` grades *harvested* JSONL against floors with a `knownAbsent` register of named, dated absences ("A gate that fails on every run teaches everyone to ignore the eval…" — `main.go:36-62`). **Runs manually only.**
- LLM extraction eval: deterministic seeded stratified sample (hash-ranked, platform×category×length strata), JSONL labels, Cohen's κ on keep/decision, P/R/F1 with a fabrication verdict, `unchecked` as a reported verdict — `python/jester/extraction_eval.py:1-110` — currently **NOT MEASURED** (owner labels pending; R4 register).
- Probes: `siteprobe` (one GET per portal, tiered classification JSON/HTML/Walled/Auth/Dead), `renderprobe` (browser-rendered parity check), `forumprobe`, `contractcheck`.

**Where Jester's own doc-tree already places risk:** the R4/STATUS register + the §35/§37 contract docs in this cycle's SDD reports; the live CDP path is manual-gated; the Python-side CDP deps are implicit (see 3.2-E2).

---

## 2. Comparative analysis (per-capability)

| # | Scrapling capability (source path) | Problem it solves | Jester equivalent | Verdict & difficulty |
|---|---|---|---|---|
| 1 | `Selector`/`Selectors` CSS/XPath/text finders (`parser.py`) | convenient page querying | Real-estate parse is regex-anchored; Reddit parse is GraphQL-JSON + SSR DOM JS; LLM extraction is text-based; Go is DOM-free by design (fewer deps, one shape) | **Not applicable** (as a transport of surface area). The *finders* would add a Go/Python HTML-parser dep without touching current failure modes. |
| 2 | Adaptive relocation (`parser.py:513-569, 814-879` + `core/storage.py`) | recover after DOM changes; auto-learn new layout | none — by policy: silent substitution is "wrong data that looks right" (`siteprofile.go:748-754`) | **Selective inspiration, P0-nucleus**: reuse the *fingerprint+score* concept as a **drift diagnostic** (never as an auto-substitute), run-over-run diffed. |
| 3 | Fingerprint storage in SQLite (`core/storage.py:74-120`) | persist per-site element identities, thread-safe | Jester's store already persists its own truth; but **no per-run parse outcome history** | **Architectural adaptation**: extend Jester's own SQLite store with `parse_outcome` rows (additive migration, W1-pattern). |
| 4 | Spider engine + scheduler + throttle (`spiders/engine.py`, `throttle.py`) | crawl control: concurrency, dedup, checkpoints, stats | curated sources; walks are bounded (`pagePlan`, caps); concurrentomatic headroom deliberately not taken | **Not applicable now / P2 seed**: park the per-domain **limiter + stats shapes** as the plan for any future parallelization. |
| 5 | robots.txt manager w/ Crawl-delay floors (`engine.py:49-146, 337-355`) | crawl politely & adaptively per domain | robots decisions are **manual, dated comments** in profiles; no runtime robots fetch; HTTP mode honors Retry-After only | **Architectural adaptation, P1**: a robots drift watcher + Crawl-delay floor for `fetch:http`; aligns with `config/compliance_test.go`. |
| 6 | curl_cffi impersonation (`fetchers/requests.py`, `engines/static.py:37-48`) | browser-like TLS/JA3+headers to avoid non-browser classification | Go net/http + Chrome UA — **mismatched JA3** (`realestate/http.go:23-30`) | **Selective inspiration, P1**: uTLS in Go or route http-mode profiles via cloakserve. |
| 7 | Proxy rotator + block-retry protocol (`engine.py:241-265`, `toolbelt/proxy_rotation.py`) | survive blocks at scale | CloakBrowser identity-per-source + `blocked_response_action` + `Pace` + rate-limit-as-STOP (`http.go:51-67`) | **Not applicable / already better-fitted**: Jester's policy is *stop and surface*, not rotate-and-plow; ScraperConfig has a proxy field already wired to cloakserve. |
| 8 | StealthyFetcher (Playwright stealth, CF solver) | bypass interstitials without infra | licensed **CloakBrowser** (per-source identities, warm jars) — strictly stronger fit for unattended runs | **Not applicable** (avoid two stealth stacks; one identity system). |
| 9 | Dev-mode response cache / replay (`engine.py:58-63, 200-212`) | iterate offline against captured pages | curated per-profile fixtures (`coverage_eval_test.go:18-34`) but no one-command capture/replay for *new* pages | **Direct adoption, P1**: `listingharvest --capture/--replay`; feeds fixture folder from live runs. |
| 10 | One-file spider templates (shopify/sitemap/feed/…) | instant per-platform adapters | per-platform Go adapters + profile YAMLs — richer and already proven on real pages | **Not applicable** (Jester's model is config + tested adapter, which it does with fixture evidence). |
| 11 | AI/MCP browser tools (`core/ai/server.py`) | let an LLM drive a browser session | console chat already has a tool protocol (python/jester/chat) | **P3 candidate**: expose sandboxed read-only page-inspection tools (screenshot/DOM peek) to the console chat via cloakserve; guardrails required. |
| 12 | JSON-LD/nextdata field pulls | — | **Jester is ahead**: `parseLDJSON`, `parseNextData`, embedded-in-block `ldjsonIn` (siteprofile.go:655-708, 781) | (credit where due) |
| 13 | Scrapy integration (`integrations/scrapy.py`) | plug into other frameworks | one-project discipline; no external scrape framework | **Not applicable**. |
| 14 | Text/attributes wrappers & clean text (`core/custom_types.py`, `clean_spaces`) | consistent text hygiene | `go/internal/htmltext` + R29 discipline already handle this | **Not applicable**. |

---

## 3. Enhancement opportunities (ranked, template-filled)

### 3.1 P0 — Run-outcome telemetry plane ("the drift ledger")

- **Name/description:** every worker run appends one outcome row per source per run: pages fetched/failed, listings parsed, **anchor hit-rate**, LD+JSON blocks seen, per-field coverage ratios, challenge/soft-block counts, bytes, latency, fresh-vs-dup ratio; console + signals expose last-N trend & regression alarms.
- **Jester files:** `go/cmd/worker/main.go` (run loop aggregation), `go/internal/siteprofile/siteprofile.go` (Parse() counters — expose a `ParseStats` alongside `[]Listing`; currently Parse returns rows or error only), `go/internal/store/store.go` (new tables via the additive-migration pattern used for `signals_source_state`), `python/jester/console/api.py` + page (golden pattern: sources page).
- **Scrapling reference:** `parser.py:814-879` (score shape), `engine.py:206-231` (CrawlStats), `core/storage.py:97-107` (table shape discipline).
- **Limitation answered:** partial drift today is invisible unless someone runs `listingeval` by hand (`listingeval/main.go:5-9`); zero-drift is the only loud state (`realestate.go:82-87`).
- **Solution sketch:** collect in FetchListingList (pages/results/firstErr/coverage of required fields), insert `run_outcome(run_id, source, ts, …)`; Python reads it for the console trend card + a signals-health digest line. **No parsing behavior change.**
- **Benefits:** converts silent drift into a *graded, queryable* signal; makes "this portal is quietly degrading" observable within one run; unifies with the κ/F1 story later.
- **Complexity:** Low-Medium (pure additive; no behavior risk).
- **Risks:** schema churn → additive-migration discipline; counter inflation (dup ratio) — define by `ContentHash` freshness, which exists (`store.go:606`).
- **Acceptance criteria:** every scheduled run yields one row per source; a fixture regression where an anchor fails on page 3 of 3 flips the trend card red; unit tests + `go test ./...` + `pytest` green; doc-contracts updated.
- **Test/benchmark:** golden fixture run recorded twice with a mutated profile → assert alarm row; measure overhead < 1% run time.

### 3.2 P0 — Wire `listingeval` gates into the scheduled pipeline

- **Jester files:** `go/cmd/listingeval/main.go` (exists; extract gate-core into `internal/siteprofile/grade` or a `pkg` the worker/console can call), worker run step, console alert surface.
- **Scrapling inspiration:** gates that *live in the loop* (spider logs blocked counts per run, `engine.py:241-265`) rather than in a doctor's notebook.
- **Current limitation:** the at-volume instrument requires manual harvest + manual invocation; nothing schedules it; drifted portals stay green in the console.
- **Solution:** worker emits each live realestate run's listings as JSONL (harvester pattern exists in `listingharvest`), then grade against the profile's floors (`TestFieldCoverageFloor` thresholds baked per profile YAML), record the verdict in `run_outcome` and raise the same alert channels used by signals health.
- **Benefits:** the eval's own warning ("failures that only appear at volume") becomes automatic; `knownAbsent` register stays the truth-setter.
- **Complexity:** Low. **Risks:** gate-file staleness — floors co-located in the profile with a written rationale (house pattern); false alarms → floors set from the coverage instrument, not guessed.
- **Acceptance criteria:** a scheduled live run produces a grade; failing a floor alerts in console + signals; grading is mocked-deterministic in CI (fixture JSONL → grade table).

### 3.3 P1 — Robots drift watcher + Crawl-delay honor (HTTP mode)

- **Jester files:** new `go/internal/robots/…` (mirroring Scrapling's manager at `engine.py:49-146`), profile YAML gains `robots_policy:` expectations, `realestate/http.go` delay floor, console alert; ties to `config/compliance_test.go`.
- **Evidence for the need:** profiles carry dated robots verdicts by hand (`zoopla.yaml:1-12` "Checked 2026-09-05"); policies go stale silently; Scrapling fetches + parses robots per run and applies Crawl-delay floors cached per domain.
- **Solution:** one paced background fetch per portal per cycle: parse robots; warn on any new Disallow touching the profile's list/detail paths; apply `Crawl-delay` as the *floor* under the profile's configured delay (never *speed up*); persist last-seen robots digest for the audit trail.
- **Complexity:** Low-Medium. **Risks:** robots fetch failures (serve cached digest, mark stale, never block runs); portals whose robots differ per subdomain.
- **Acceptance criteria:** a fixture robots file with a new Disallow raises the warning; Crawl-delay > configured delay raises the effective floor, measured in tests; no run blocks on robots fetch failure.

### 3.4 P1 — Declare `playwright`/CDP extras; name the "cloaked env" contract

- **Jester files:** `python/pyproject.toml` (add `[project.optional-dependencies] cloak = ["playwright>=…"]`), `fetchers/cloak.py:220-226` lazy import rationale documented in the console Config page text (already renders ScraperConfig).
- **Evidence:** today the live CDP path works only on hosts where playwright happens to be installed; nothing declares or versions that requirement; the doc-contract floor test (3.11 Python floor) doesn't cover it.
- **Benefit:** dependency parity with Scrapling's extras discipline (`pyproject.toml:72-76`); honest `pip install jester[cloak]` story.
- **Complexity:** Low. **Test:** pip-install dry-run in a clean venv with and without the extra; doc-contracts assertion.

### 3.5 P1 — Fetch-identity decision for `fetch: http` portals (TLS fingerprint)

- **Jester files:** `go/internal/realestate/http.go` (client), profile `fetch:` field dispatch (`realestate.go:35-49`).
- **Evidence:** Chrome-UA + Go-TLS mismatch; portals already prove they fingerprint (Consent interstitials, Redfin 202 soft-block).
- **Options:** (a) **uTLS** (refraction-networking/utls) in the HTTP client to speak a Chrome-like ClientHello; (b) route `fetch: http` through cloakserve profiles needing it; (c) keep as-is for profiles that demonstrably parse clean, with the drift plane watching. Recommend (c + a), profile-flagged — not a blanket change; measure via the new telemetry before/after.
- **Complexity:** Medium (uTLS wiring + identity testing in Go). **Acceptance:** the three annotated portals (behya/tunisieannonce/…) parse identically over a week; no robots/consent regression.

### 3.6 P2 — Capture/replay dev loop for profile work

- **Jester files:** `go/cmd/listingharvest/main.go` (+`siteprobe/renderprobe`), fixtures dir (`internal/siteprofile/testdata/*_tiles.html`).
- **Scrapling reference:** dev-mode cache (`engine.py:58-63, 200-212`).
- **Solution:** `listingharvest -capture profile=zoopla` writes `config/profiles/captures/…today.html` + a manifest (url, ts, sha256, fetch-mode); parser iteration replays captures (`-replay`) without touching portals; promotion path: capture → trimmed fixture → fixture-locked test update.
- **Benefit:** new-portal onboarding time collapses; the fixture folder stops being a one-time artifact ("The same applies to docs/*.html: captured evidence" philosophy, applied to profiles).
- **Complexity:** Low. **Acceptance:** capture → replay byte-identical parse; fixtures built from captures unchanged vs manual capture.

### 3.7 P2 — Profile fallback chains (`patterns:` list)

- **Jester files:** `go/internal/siteprofile/siteprofile.go` Field + `patternValues` (siteprofile.go:847+), YAML contract tests, docs (`§4.3` profile contract).
- **Scrapling reference:** its selector→relocate→`find_similar` *alternatives* notion, minus the bad part (silent substitution): in Jester the fallback is **declared in config**, and every pattern hit is recorded in `run_outcome.field_pattern` so the operator can see which fallback actually fired.
- **Solution:** `values: [pattern1, pattern2]` — first non-empty wins; telemetry shows per-field which pattern fired per run (portal changed classes ⇒ pattern2 alive, pattern1 dead ⇒ fix later).
- **Complexity:** Low-Medium. **Risk:** "works forever by accident" fallbacks — mitigated by firing-visibility; per-field warnings when pattern1 goes stale for N runs.

### 3.8 P2 — Adapter-matrix page in the console

- **Jester files:** console sources page (python/jester/console/api.py), `SUPPORTED_KINDS` oracle (sources.py:62-86), `contractcheck` precedent.
- **Scrapling reference (concept):** the sheets that made v0.5 shape visible (agent-skill examples, CLI commands).
- **Solution:** one view: source name × kind × worker-route (has adapter) × last-run outcome last 7d — collapses the "six enabled sources the Python side did not believe existed" class of error (sources.py:79-85, fixed once but the class remains).

### 3.9 P3 — Optional experimental set (use sparingly)

1. **Operator diff-view with Scrapling-style similarity scores** — when a drift alarm fires, show last-good capture vs today's parse with the *anchor hit-rate/location deltas* (borrow `__calculate_similarity_score` concept, port to Go text sim or Python console-side). *Not* auto-relocation.
2. **Harvest checkpoints** for `listingharvest` deep walks (Scrapling `CheckpointManager`, `engine.py:299-336`): pause/resume multi-hour harvests (P24 session: 2,400 photographs — `http.go:53-57`).
3. **Concurrent per-domain budget** — if sources ever parallelize across sources, adopt Scrapling's two-limiter shape (global + per-domain, `engine.py:77-79`); document as a design gate until needed (one-uid/shared-archive is the current constraint).
4. **Console chat page-tools** — an MCP-shaped read-only "inspect this page through cloakserve" toolset for the operator chat (Scrapling `core/ai/server.py`).

**Rejected with reasons (do not resurrect):** Camoufox/StealthyFetcher duplication (identity system conflict; CloakBrowser is licensed infra with warm per-source jars); Scrapling's silent adaptive pass; wholesale spider engine (curated-sources constitution); Scrapy/MCP integrations as services (surface area without need); orjson (Python side uses stdlib + Go encodes payloads where it matters).

---

## 4. Architecture recommendations

Current ingestion plane (verified):

```
sources.yaml ── Go worker ─┬─► cloakserve CDP (per-source fingerprint identity, warmed jar) ─► rendered HTML
                            └─► net/http (fetch:http portals; Chrome UA, Go TLS; Retry-After; STOP-on-429)
        siteprofile profile (ldjson|nextdata|anchored+pattern+filter+classify+detail) ──Parse()──► []Listing
        SQLite (jester.db): ingest_batch, listings(+payload), signals… ──► agents(LLM) ──► Nuggets ──► console/journal/export
        Quality: fixture-locked profile tests · listingeval (manual) · siteprobe/renderprobe · extraction_eval (κ/F1, NOT MEASURED)
```

Recommended shape after P0/P1 (the only structural addition — a **telemetry plane**, everything else stays):

```
        … same fetch/parse, but Parse() now emits (rows, ParseStats) …
                 │
                 ├─► store.run_outcome  (additive tables: parse_outcome, fetch_outcome, robots_digest)
                 │        └── console "Sources" trend cards · signals-health digest · drift alarms
                 └─► grade(JSONL) ── listingeval gates per profile (knownAbsent honored) ──► same alerts
```

Key architectural statements:
1. **Ingestion stays in Go; telemetry joins it there.** No new Python fetching stack; no Scrapling import ever.
2. **No silent substitution.** Adaptive ideas enter only as outcome counters and diagnostics.
3. **Every new state is additive-migrational** (house W1 pattern) and square with the export/backup contract.
4. **Console is the single place an operator reads drift** (Sources page trend + alerts), reusing signals plumbing.

---

## 5. Implementation roadmap (in priority order; phases are parallelizable from B onward)

**Phase A — Drift ledger (P0, ~2 waves)**
- Tasks: ParseStats through `siteprofile.Parse` (incl. anchored/ldjson/nextdata counters); `run_outcome`/`fetch_outcome` tables + worker writes; console trend cards + alert thresholds; fixture-locked tests; doc-contracts + STATUS register rows.
- Order: counters → store → worker wiring → console → docs. **DoD:** every run leaves a row; a mutated-profile fixture flips the card; suites green; report committed like `5236fab`.

**Phase B — Gates in the loop (P0, overlappable)**
- Extract grade-core from `listingeval`; worker emits run JSONL + grade; alerts; mocked grading in CI. **DoD:** failing-floor fixture alarms; harvest artifact recorded under the run's evidence dir.

**Phase C — Robots + transport identity (P1)**
- robots watcher (cached-digest semantics), Crawl-delay floor, `robots_digest` table; uTLS behind a profile flag for the three annotated portals; `cloak` extra in pyproject. **DoD:** watcher tests; one-portal A/B telemetry run comparing fetch outcome before/after uTLS.

**Phase D — Developer loop & profile power (P2)**
- capture/replay in `listingharvest`; profile `patterns:` fallback chains with per-run firing visibility; adapter-matrix console page. **DoD:** new-portal onboarding documented end-to-end; no portal traffic in tests beyond existing probes.

**Phase E — Experiments gate (P3)**
- diff-view similarity report; harvest checkpoints; concurrency budget design-note; chat page-tools (behind `--live` and audit).

**Priority math (Impact × Confidence ÷ Cost):** A/B outrank all (they convert existing manual truth into autonomous truth over 16 portals); C is compliance + reliability (medium impact, high confidence, low-medium cost); D is throughput-of-maintenance; E is clearly labelled exploration.

---

## 6. Validation & quality strategy

**Baseline to beat (measurable, from existing instruments):**
- Deterministic fixtures green today: `go test ./...` (all packages), `pytest` full suite (2315 passed at `5236fab`; suites unchanged).
- Current drift posture: zero-run-only (firstErr), fixtures 100%, `listingeval` floors verified manually through the 2026-09-04 harvest evidence embedded in the tool.

**Metrics & where they land after Phases A/B:**

| Metric | Source (today) | After changes |
|---|---|---|
| Extraction success rate per portal | "no listings" only | `parse_outcome.rows / pages` per run + trend |
| Robustness to structure change | fixture-locked tests (point-in-time) | anchor hit-rate, field-coverage deltas run-over-run; days-to-detect target ≤ 1 run |
| Field-level accuracy | `listingeval` floors (manual) | automatic grade per run (+κ/F1 when labels ready) |
| Request latency / bytes | unrecorded | `fetch_outcome.latency_s/bytes` per page |
| Failure & retry rates | ad-hoc prints | `StatusError` counts by class in `fetch_outcome` |
| Memory/CPU | untouched | unchanged; overhead budget <1% run time |
| Maintainability | Sonar gate (back green): A security, coverage 93.69% | new tables covered by additive-migration tests; sonar new-code stays A |
| Extraction provenance | R29 discipline | unchanged (telemetry never feeds rows) |

**Regression protocol:** every phase lands with (1) full `go test ./...` + `pytest` green under `JESTER_MOCK=1`, (2) new golden-fixture tests for each new table/view, (3) no live network in CI (house rule — manual `--live` only), (4) doc-tests updated same-commit (doc-contracts + doc-links), (5) the R4-style register row in STATUS.

**Two-week A/B benchmark (C):** before/after per-portal runs; success endpoints: pages fetched, listings, bytes, latency, robots alarms; zombie profile detection via fresh ratio.

---

## 7. Research references (verified points + contexts)

- **Scrapling v0.5 source (BSD-3-Clause)** — `scrapling/parser.py` (adaptive scoring + relocation), `core/storage.py` (SQLite fingerprint store: WAL/RLock/UNIQUE), `core/utils/_utils.py` (`element_to_dict` fingerprint codec), `spiders/engine.py` + `scheduler.py` + `throttle.py` (anyio engine, robots manager, AutoThrottle, checkpoints, dev-cache), `fetchers/requests.py` + `engines/static.py` (curl_cffi impersonation config), `core/ai/server.py` (MCP server), `benchmarks.py`. Repository analysis claims beyond these files (stealth internals, Cloudflare solver) rest on the module/test surface and are marked as such.
- **Jester source (MIT) at `15032e4`**: `python/jester/sources.py`, `fetchers/{__init__,cloak,youtube}.py`, `agents/extractor.py`, `extraction_eval.py`, `pyproject.toml`; `go/cmd/worker/main.go`, `go/internal/realestate/{realestate,http,pace}.go`, `go/internal/siteprofile/siteprofile.go` (+`coverage_eval_test.go`), `go/cmd/listingeval/main.go`, `go/internal/cloakbrowser/cloakbrowser.go`, `go/internal/store/store.go`; `config/profiles/*.yaml` (16 portals), `docs/STATUS.md` (R4 register; main history including today's PR #87 gate fix `324bbb4`/`1f774a2`/`15032e4`).
- **Verified cross-facts:** duration-limited evidence in-place (Property24 503-mid-session; Redfin 202 soft-block; Mubawab promo banners; 4,141-listing coverage run) — these are Jester's own field notes, not assumptions; the SAME failure classes Scrapling equips appear in Jester's annotated code, which is why the borrow list contains what it does.
- **Standards/practice context behind the recommendations:** robots.txt semantics + Crawl-delay as floors (RFC 9309: "Crawl-delay as de facto pacing"), TLS fingerprinting findings around ClientHello/JA3 mismatching (curl_cffi's raison d'être — confirmed by Scrapling's dependency choice `pyproject.toml:75`), SLO-style health instruments (Google SRE error-budget framing for the run-outcome ledger), SRE "alert on symptoms" (partial-yield trend, not single-run zeros), and Kushmerick's wrapper-induction lineage for why fingerprint-based relocation without history cannot detect field drift (structural relocation ≠ semantic field presence). These are context, not CrEEDS-citations; each recommendation stands or falls on the Jester file evidence cited inline.

---

## Method & honesty notes

- Everything in `§2/§3` cites files and line ranges read this session, both repositories.
- Read-limitations declared: Scrapling stealth internals, cloudflare solver, AI browser-action code, and the furthest spider templates (`shopify/feed/site_to_markdown`) were skimmed at name/test level, not traced line-by-line; Jester `go/internal/reddit/live.go`, `signals/*` python in-depths and `cmd/worker` runLive continuation beyond `main.go:1-120` were sampled, not exhaustively read. Nothing in this report depends on unverified internals of those areas.
- No Jester code was modified. This report is the only artifact (this file); the clone sits in `%TEMP%\opencode\scrapling`.
