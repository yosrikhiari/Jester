# Jester — Project Assessment and Master Roadmap

**Date:** 2026-10-29 · **Baseline commit:** `771d3d5` on `feat/ui-corporate-redesign` (working tree clean)
**Current-state re-check:** `main` at `70f966f` (== `origin/main`, clean tree). The baseline sections below are kept as the historical record; §1.5 maps every load-bearing claim forward, and §5.1 records the next approved batch.
**Author:** orchestrated assessment (owner + three independent review agents + multi-builder implementation)
**Scope of this document:** current state, bibliography, workstream mapping, gap register, task register, checklist, verification log. Companion review reports live in `docs/reviews/2026-10-29-*.md`.

---

## 1. State (verified facts at baseline)

### 1.1 Repository

| Fact | Value | Evidence |
|---|---|---|
| Branch | `feat/ui-corporate-redesign`, 68 commits ahead of `main`, clean tree | `git status`, `git log main..HEAD --oneline` |
| Last audit | 2026-10-14, commit `7aef02c`; all 12 findings implemented and merged to `main` as PR #54 | `docs/CODEBASE_IMPROVEMENT_REPORT.md` Appendix C |
| Branch delta vs main | 44 files, +18,765/−378 (console redesign + tests) | `git diff main...HEAD --stat` |
| Toolchain | Python 3.12.10, Go 1.26.7, Node 24.11 | `python --version` etc. |
| Go suite | green, all packages ok | `go test ./...` this session |
| Python suite | **1679 passed, 5 skipped, 13 deselected** (431.6 s) — baseline green | `pytest python/tests -q`, this session |

### 1.2 Prior-work ledger (what is already approved and done)

- F-01…F-12 of the 2026-10-14 audit: landed (Appendix C, PR #54), except deferred items:
  non-root images, per-category dedup thresholds (tool shipped, labels pending),
  `/metrics` + OTel spans (access log is the first slice; `/metrics` is in the next
  batch), `vitest` for the console static UI. Image-digest pinning was listed as
  deferred at the baseline and **landed after it**; see §1.5.
- Signals: deterministic classifier, `mode` column, failures-as-rows, tri-way reconcile
  (`signals evidence`), fixture gate in CI (`.github/workflows/ci.yml` lines 72–97).
- Journal J0–J3: publishes with gates, run fingerprints, plan locks, export to site
  worktree, variants that cannot add numbers.
- Edit regime: chat/scheduler archive edits route through `python/jester/edits.py`
  proposals + append-only `edit_log` (no code path may UPDATE/DELETE it — tested).

### 1.3 Verified spot-checks feeding the gap register (baseline)

- `jester eval` (`python/jester/cli_review.py:46-68`) checks only the scoring arithmetic
  (`compute_overall` vs `good_idea_min` gate) on the labelled lines of
  `eval/known_good.md` / `known_bad.md`. It does **not** exercise the extractor or
  nugget quality. At the baseline no CI job ran it; **resolved after the baseline** —
  `.github/workflows/ci.yml:107-108` now runs it. See §1.5.
- `PROMPT_VERSIONS = {"extractor": 2, "synthesizer": 1, "critic": 1, "labeller": 1}`
  (`python/jester/llm.py:46`); recorded per run (`cli.py:314`) and exposed in the console
  (`console/api.py:277`).
- `dedup-sweep` (`python/jester/dedup_sweep.py`) is offline/deterministic and pure over a
  JSONL pairs file. At the baseline no labelled pairs dataset existed; **resolved after
  the baseline** — `testdata/dedup/pairs/` now holds three labelled files and the sweep
  has a `--pairs-dir` mode. The per-category map is still empty by default, so production
  dedup remains at the global 0.87 until an operator promotes a table (§1.5).
- `runs` table (`python/jester/store.py:168-191`) records `models_used`, funnel,
  `trivial_share`, `origin`. At the baseline it had **no OS/interpreter/code
  fingerprint**; **resolved after the baseline** by the `runs.env` column (§1.5).
- `python/pyproject.toml:10-16`: runtime `dependencies` still list `pytest>=8.0`; no
  `import pytest` exists in shipped code (`python/jester/**` grep clean). The old
  root-level `pyproject.toml` path in the original note no longer exists; the package
  metadata has always lived under `python/`.
- CI today (`.github/workflows/ci.yml`): clean-clone lock install, signals
  export-twice idempotence, `signals check` fixture gate, pytest+coverage, advisory
  pip-audit (`continue-on-error`), Go build/test + contract tests against a
  Python-created DB, frontend lint+build, Sonar scan when token present.

### 1.4 Final state (this cycle, after integration)

- Integration branch `integ/master-roadmap-2026-10-29` built from `771d3d5`; `origin/main` merged, then `main` fast-forwarded to `7ce8d44` and pushed to `origin/main`.
- All nine registered tasks landed (A1–A3, B1–B3, C1–C3); stretch T-A4 partial (`SCORE_BACKLOG` flag only).
- Final full Python suite: **1741 passed, 5 skipped, 13 deselected**; Go all ok; frontend lint/build ok; CI-mirror checks ok (eval 20, fixture gate 21/21, export idempotence, Go contract).
- Three verifier-fix commits after builder sign-off: console reembed ID, sweep cleanup, `runs.env` status half-record; plus a local registry-row fix for the config lint.
- Global critic score **8.9/10**; zero blocker/high findings open. Full detail in `docs/reports/2026-10-29-final-implementation-report.md`; chronology in `docs/plans/2026-10-29-implementation-iteration-log.md`.

### 1.5 Current-state re-check (2026-10-29, post-merge)

Re-checked every load-bearing claim in this roadmap and the three reviews against the tree now on `main`, not against the baseline. `HEAD == origin/main == 70f966f`, clean tree; `main` carries 76 upstream commits beyond the baseline (chat, scheduler, specs, maintenance, console chat). The comparison changes no historical finding; it changes which ones are still open.

| Baseline claim / task | Current state | Evidence |
|---|---|---|
| T-A1 eval gate wired in, UTF-8 input | **RESOLVED** — CI runs `python -m jester.cli eval` after the fixture gate; both eval files are read with `encoding="utf-8"` | `.github/workflows/ci.yml:107-108`; `python/jester/cli_review.py:54-66`; eval reports **20 cases (gate=7.0)** |
| T-A2 competition preservation + TTL | **RESOLVED** — a missing competition number preserves the web-checked value/notes/stamp; `needs_recheck` is consulted on merge | `python/jester/agents/critic.py:66-110`; `python/jester/idea_dedup.py:99-140`; `python/jester/competitors.py:179` |
| T-A3 batch order echo | **RESOLVED** — each batch item must echo its comment index; missing/wrong `i` falls back per comment | `python/jester/llm.py:444-505`; `test_extract_batch_reversed_reply` |
| T-B1 deterministic point IDs + self-match guard | **RESOLVED** — `point_id_for` is sha256-derived; a match carrying the candidate's own key proceeds to insert | `python/jester/vector.py:45-66`; `python/jester/agents/archivist.py:94-115` |
| T-B2 per-category thresholds + labelled pairs | **SHIPPED, LABELS STILL ADVISORY** — per-category map and `--pairs-dir` exist; three pair files (20/24/24) exist; `dedup_threshold_by_category` is still `{}` in config, so production behaviour is unchanged until an operator promotes a table | `config/thresholds.yaml:6-25`; `python/jester/agents/archivist.py:48-55`; `testdata/dedup/pairs/`; `docs/dedup-calibration.md` |
| T-B3 vendor veto + from-archive provenance | **RESOLVED** — `veto` role forces `NONE`; from-archive records keep `[matched: …]` | `python/jester/signals/filters.py:233-245`; `python/jester/signals/from_archive.py`; fixture gate **21/21** |
| T-C1 `--code` required / code envelope gate | **RESOLVED** — parser requires `--code`; `counts_for_gate` rejects an absent code envelope; attached ledger runs carry `code.source=ledger` | `python/jester/cli.py:3372-3381`; `python/jester/journal/lab.py:302-320` |
| T-C2 pipeline `runs.env` | **RESOLVED** — `runs.env` exists and is populated at start; failed collection records a reason instead of a false clean flag | `python/jester/store.py:363`; `test_run_env.py` |
| T-C3 digest pinning | **RESOLVED** — all four compose images carry `sha256:` digests; `qdrant`/`ollama` keep a `latest` tag but are immutable by digest, which `test_docker_pins.py` deliberately accepts | `docker-compose.yml:45,176,225,253`; `python/tests/test_docker_pins.py` |
| T-A4 `SCORE_BACKLOG` + attempt counter | **RESOLVED (2026-10-30 batch)** — the per-idea attempt counter ships; `rescore_pending` rotates least-tried-first; success and `mark_needs_score` reset it; the stalled count surfaces in the run line, maintenance and `/metrics` | `python/jester/store.py:114,284`; `python/jester/agents/critic.py:150,163-210`; `python/jester/ops.py:21` |
| EX-G3 607-idea re-score backlog | **RESOLVED mechanism (2026-10-30 batch); live backlog still 607/2627** — the remediation this finding named (assertion + alarm + staleness bound) is implemented and verified. The live archive has not migrated (`rescore_attempts` column absent), so the backlog drains at `rescore_per_run: 1` only after the operator migrates — an observation, not a test failure | schema/code evidence in §5.2; read-only `sqlite3` re-measure 2026-10-30: **607 `needs_score` of 2627 ideas** |
| P-H1 `before_plan` is display-only | **RESOLVED (N6)** — `_verify_run` and `_block_runs` now refuse a pre-plan run as proof, with a message that says why | `python/jester/journal/checks.py:194-222,258-273`; `test_a_before_plan_run_cannot_back_a_number` |
| P-H3 export does not re-hash figures | **RESOLVED (N7)** — `publish.export` re-hashes every `figures/*` image with `artifact_problem` before touching the site worktree | `python/jester/journal/publish.py:356-377`; `test_export_refuses_a_figure_edited_after_it_was_made` |
| DD-M1/M2 re-clustering schema churn | **RESOLVED (2026-10-30 batch)** — the labeller's `coherent:false` verdict is persisted (`clusters.incoherent`, payload carried, console "no shared subject" pill); drafts re-home by `supporting_nuggets` overlap, no-overlap drafts surface as the synthetic `DETACHED_CLUSTER_ID` group | `python/jester/store.py:58,141,287,869-949`; `python/jester/clustering.py:126,580`; `python/jester/console/api.py:942-1035`; `python/jester/console/static/app.js` (`clusterHeadHTML`) |
| DD-M4/M5/M6, PR-H4/M1..M5 | **OPEN, DEFERRED** — recorded in the reviews; no code has landed for them | review reports; no matching tests or symbols |
| Upstream Batch 1 (2026-10-25) | **PARTIAL** — N1–N7 implemented 2026-10-30; the `extractor_batch_size` cap, model digest, and UI smoke are still absent | `python/jester/journal/__init__.py` (`output_sha256`, `parse_metric_at`); `python/jester/cli.py` (`measure`); `python/jester/console/server.py` (`/metrics`, preflight); `python/jester/console/static/app.css` (`--line-control`); `python/jester/journal/{checks,publish}.py` |
| Post-merge batch N1–N7 | **IMPLEMENTED 2026-10-30** — each task test-first; full Python **1760 passed, 5 skipped, 13 deselected**; Go all ok; frontend lint/build; eval **20**; signals **21/21** | §5.1, §7 rows 24–27, review addenda |
| Source-scope decision | **STILL PROVISIONAL** — `status: provisional`, `decided_on: ""` | `config/signal_rules.yaml:1-9` |
| New subsystems not covered by the 2026-10-29 reviews | **NOT YET AUDITED** — `chat/`, `scheduler.py`, `edits.py`, `specs.py`, `maintenance.py`, console chat | file inventory; no section in any of the three reviews |

**Deliberate exclusions from this re-check.** The reviews keep their original severity labels and line citations as the historical record; only this table and their status addenda say what is resolved. Live archive numbers are observations, not test assertions: `data/jester.db` is not reproducible from the repository.

---

## 2. Bibliography (working references, carried from the repo's own reports)

1. Repo evidence: `docs/CODEBASE_IMPROVEMENT_REPORT.md` (2026-10-14 audit + PR #54 status),
   `docs/scope-and-access.md`, `docs/realestate-dedup-validation.md` (dedup precision 5/7,
   recall unmeasured), `JESTER-FRONTEND-SYSTEM-DESIGN.md`, `jester-setup.md`,
   `docs/console-redesign/` (mockups + ADAPTATION-DEVIATIONS.md).
2. Malkov & Yashunin, *HNSW*, IEEE TPAMI 2020 (DOI 10.1109/TPAMI.2018.2889473) — dedup/vector
   design rationale.
3. Reimers & Gurevych, *Sentence-BERT*, EMNLP 2019 (DOI 10.18653/v1/D19-1410) — cosine
   dedup primitive, per-domain threshold calibration.
4. Kwon et al., *PagedAttention*, SOSP 2023 (DOI 10.1145/3600006.3613165) — batching lever
   for the extractor.
5. SQLite docs: WAL, PRAGMAs, query planner; Qdrant batch APIs; ClickHouse
   ReplacingMergeTree+FINAL; OWASP Top-10/ASVS; OpenTelemetry/Prometheus; PEP 751.
   (Full citations with URLs in `docs/CODEBASE_IMPROVEMENT_REPORT.md` §12 — not duplicated here.)

---

## 3. Mapping (workstreams ↔ owners ↔ files)

> Filled after the gap register; each workstream is one builder in its own worktree with a
> separate verifier. File ownership is exclusive; shared files (CI workflow) belong to one
> builder only.

| Workstream | Area | Builder files (exclusive) | Review report |
|---|---|---|---|
| WS-A | Extraction / scoring / evaluation | `python/jester/cli_review.py`, `eval/*`, `.github/workflows/ci.yml` | `docs/reviews/2026-10-29-extraction-scoring-evaluation.md` |
| WS-B | Dedup / clustering / classification | `python/jester/archivist path`, `config/thresholds.yaml`, labelled-pairs fixture, calibration doc | `docs/reviews/2026-10-29-dedup-clustering-classification.md` |
| WS-C | Provenance / reproducibility | `python/jester/store.py` (runs columns), run fingerprinting, Docker pins (the `pyproject.toml` dev-dep split is deferred; see final report §7) | `docs/reviews/2026-10-29-provenance-reproducibility.md` |

---

## 4. Gap register

Consolidated from the three review reports (full enumerations live there). Disposition key: **ADOPT** → task register below · **DEFER** → recorded, not in this cycle · severity: blocker/high/medium/low. This table records the **2026-10-29 cycle disposition**; the post-merge status of each item is in §1.5 and is not rewritten here except where the disposition itself is now misleading.

| ID | Area | Severity | One-line statement (evidence, verified) | Disposition |
|---|---|---|---|---|
| EX-G1 | Extraction/eval | high | `jester eval` checks only the scoring arithmetic, no LLM, and runs in no CI job, scheduler task, doctor check, or test (`cli_review.py:46-68`; `ci.yml`) | ADOPT → T-A1 |
| EX-G2 | Scoring | high | `apply_scores` clobbers web-verified competition: wipes `competitor_notes`, flips `competition_checked`; `rescore_pending` never consults the TTL (`critic.py:76-88,115-140`) | ADOPT → T-A2 |
| EX-G4 | Extraction | high | Batched extraction trusts item order; acceptance is list+length only (`llm.py:412-480`) — reordered reply misattributes insights | ADOPT → T-A3 |
| EX-G3 | Scoring | high | 607 `needs_score` backlog (measured), no floor flag/alarm/attempt counter | **RESOLVED 2026-10-30** — attempt counter + rotation + stalled/alarm surfacing (§5.2); `SCORE_BACKLOG` shipped earlier |
| EX-G5,G7,G8, L1-L6 | Extraction | med/low | Unmeasured promotion metrics; hung-model brake absent; run-granular prompt provenance; misnomers, minor coersion counters | DEFER (recorded in review) |
| EX-G6 | Scoring | medium | `competitor_ttl_days` knob dead-wired (zero non-test callers) | ADOPT → T-A2 |
| EX-G9 | Eval | medium | `cmd_eval` reads eval files with platform encoding → Windows crash on non-ASCII (`cli_review.py:54`) | ADOPT → T-A1 |
| DD-H1 | Dedup | high | Qdrant point IDs from process-salted `hash()` (`vector.py:195,227`, `archivist.py:76`, `cli.py` reembed, `idea_dedup.py:42`) — cross-process duplication demonstrated | ADOPT → T-B1 |
| DD-H2 | Dedup | high | Dedup self-match discards the matched key; kill between upsert and insert loses a nugget forever, unrecorded (`archivist.py:69-81`) | ADOPT → T-B1 |
| DD-H3 | Classification | high | Negative families cannot veto a buyer verdict; vendor launch post can clear the bar with "against it" printed but ungated (`filters.py:265-280`) | ADOPT → T-B3 |
| DD-M3 | Dedup | medium | Global 0.87; no labelled pairs anywhere; `dedup-sweep` has no per-category mode (`dedup_sweep.py`, `cli.py:3726`) | ADOPT → T-B2 |
| DD-M7 | Classification | medium | From-archive records lose `[matched: …]` provenance (`from_archive.py:261`) | ADOPT → T-B3 |
| DD-M1,M2 | Clustering | medium | Labeller `coherent:false` dropped; re-clustering orphans cluster drafts (schema churn) | **RESOLVED 2026-10-30** (§5.2) — verdict persisted; drafts re-homed / detached group |
| DD-M4 | Property dedup | medium | Semantic layer unreachable; empty-collection bypasses unknown-semantics gate | DEFER (needs vector-sync design) |
| DD-M5,M6 | Dedup ops | medium | `INDEXING_THRESHOLD` applies only on create; `reembed --all` cannot complete an embedding-model change | DEFER (follow-up PR) |
| DD-L1..L8 | Dedup | low | Hard-coded embedding_model/near-miss band/payload asymmetry/etc. | DEFER (recorded) |
| PR-B1 | Journal gate | **blocker** | Code provenance opt-in: omitted `--code` → `{}` envelope → `counts_for_gate` passes trivially (`lab.py:118-126,294-301`; `cli.py:3284,2943`) — contradicts the README's documented rule | ADOPT → T-C1 |
| PR-H2 | Pipeline runs | high | `runs` rows carry no environment/code fingerprint (`store.py:168-191`) | ADOPT → T-C2 |
| PR-M2 | Supply chain | medium | 4 compose images unpinned (2× `latest`, 1 untagged, 1 tag-no-digest); dependabot comment describes pinning that does not happen (`docker-compose.yml:39,163,209,235`) | ADOPT → T-C3 |
| PR-H1,H3,H4,M1..M5 | Provenance | med | `before_plan` display-only; export lacks figure re-verification; overwrite claims without audit rows; etc. | P-H1/P-H3 **resolved** by N6/N7 (§5.1); H4/M1..M5 still deferred |

## 5. Task register (approved remaining work — this cycle)

Nine tasks, three per workstream. Each task: one builder, one verifier, defined acceptance. Builder file ownership is exclusive per workstream; WS-A alone touches `ci.yml`.

### WS-A — Extraction / scoring / evaluation (builder A · worktree `wsa-eval-gate`)

| Task | Scope | Acceptance criteria |
|---|---|---|
| T-A1 Wire the eval gate in | `cli_review.py` (encoding `utf-8`), new `python/tests/test_eval_gate_repo.py` running `cmd_eval` against the repo's `eval/known_good.md`+`known_bad.md`, `.github/workflows/ci.yml` (step after Fixture gate: `python -m jester.cli eval`), correct `config.py:175-179` comment so it names what the gate really checks (formula calibration; batch parity = the J2 e2e equality test) | `pytest -k eval_gate` green; mutating a known_good subscore to fail-overall makes the test fail; CI yaml parses (yaml.safe_load) and contains the eval step; `python -m jester.cli eval` exits 0 on the repo files |
| T-A2 Honor checked competition (R53) | `agents/critic.py`: a critic reply without `competition` on an idea previously web-checked preserves `competition`/`competitor_notes`/`competition_checked` and recomputes overall with the preserved value; reply with a number keeps today's fresh-check semantics; wire `competitors.needs_recheck` (or its stamp) into the merge re-check path `idea_dedup.py` so a fresh check is not re-spent; tests in `test_critic_honest.py` (or new `test_rescore_competition.py`) | `pytest -k rescore_competition or needs_recheck` green: preserved-competition case, fresh-supplied case, TTL-fresh merge skips the fake search transport (`calls` unchanged), TTL-stale merge re-checks once |
| T-A3 Batch order echo | `llm.py` batch prompt gains a per-item `i` (echo of the comment index) validated on parse; mismatch (missing/wrong `i`) → per-comment fallback as today; `agents/extractor.py` passes nothing extra (indices derive from bodies order in `llm.py`); scripted reversed-reply test | `pytest -k extract_batch` all green incl. new `test_extract_batch_reversed_reply`: reversed items → fallback engaged (`fallbacks==1`), comments carry their own insights (compared against single-call oracle), no mis-zipped row archived |

Stretch (only if T-A1..A3 green): T-A4 `SCORE_BACKLOG` floor flag + rescore attempt counter (EX-G3).
**Status: COMPLETE** — `SCORE_BACKLOG` shipped in `a445ab7`; the per-idea attempt counter, least-tried rotation and stalled-surfacing shipped in the 2026-10-30 batch (§5.2), closing the EX-G3 mechanism.

### WS-B — Dedup / clustering / classification (builder B · worktree `wsb-dedup-labels`)

| Task | Scope | Acceptance criteria |
|---|---|---|
| T-B1 Deterministic point IDs + self-match ghost guard | `vector.py`: `point_id_for(unique_key)` = stable 63-bit (sha256-based); adopt at `vector.py:195,227`, `archivist.py:76`, `cli.py` reembed id sites, `idea_dedup.py:42`. `agents/archivist.py`: ignore matches whose payload/returned key equals `n.unique_key` when that key is not yet in the DB (proceed to insert); record `self_match` handling in code comment; tests: cross-process determinism (subprocess prints id), upsert-twice idempotency (`count == 1`), seeded own-vector then insert proceeds exactly once, kill-window simulation (upsert without insert; re-run archives once) | `pytest -k point_id or self_match or ghost` green; existing `test_vector_batch.py`, `test_reembed.py`, `test_archivist.py` still green |
| T-B2 Per-category dedup thresholds + labelled pairs | `config.py` `Thresholds` gains `dedup_threshold_by_category: {cat: float}` (bounds-checked, inherits global when absent); `thresholds.yaml` documents the map (empty by default); `agents/archivist.py` applies the per-category value (fallback global) and the near-miss band derives from the applied threshold (no hard-coded 0.85); `dedup_sweep.py`/`cli.py` `--pairs-dir` / `--category` mode: one annotated P/R/F1 table per `testdata/dedup/pairs/<category>.jsonl`, refuse tables under a pair-count floor; seeds: honest starter pairs derived from `docs/realestate-dedup-validation.md` + clearly-synthetic unit-grade pairs; summary table doc `docs/dedup-calibration.md` (marked: advisory, offline, corpus-derived, P measured / R from labels only) | `pytest -k per_category or dedup_sweep or near_miss_band` green; archivist test asserts the per-category value is the one applied; three seeded category files produce three tables; archive untouched (advisory only) |
| T-B3 Classifier veto + from-archive provenance | `signals/filters.py`: `negative` families gain a veto role (e.g. `vendor_pitch`) that forces `NONE` when fired, reason naming the veto (rules file already declares "a launch post is never a buyer"); one new fixture case in `python/tests/fixtures/signals/` (launch-post shape); `REQUIRED_TOTAL` bumps accordingly; `signals/from_archive.py:261` mirrors `apply_to`'s `[matched: …]` provenance line; tests `test_signals_filters.py` + fixture-gate tests updated | `pytest -k vendor or veto or fixtures or from_archive` green; the launch-post fixture classifies `none` with veto named; all existing fixture cases still pass |

### WS-C — Provenance / reproducibility (builder C · worktree `wsc-run-provenance`)

| Task | Scope | Acceptance criteria |
|---|---|---|
| T-C1 Journal code-provenance hardening | `journal/lab.py`: `counts_for_gate` returns False when the code envelope is absent/`{}` (no evidence ≠ not dirty) — `dirty is not True` semantics apply only when a code envelope exists and names a repo; `attach_ledger` writes an explicit `code:{source: "ledger"}` envelope; `cli.py`: `journal run` rejects a missing `--code` with a usage error (wrapper command names an explicit reason); tests in `test_journal_lab.py` | `pytest -k counts_for_gate or journal_run or attach` green: gate fails without `--code`; dirty code still blocks; attached ledger runs carry the ledger envelope and keep the documented attach semantics |
| T-C2 Pipeline run fingerprint | `store.py`: `runs.env` column via the existing ALTER reconcile; populate at run finish (or `finish_run` helper) with `{"os","python","machine","cpu_count","code":{commit,dirty}}` of the jester repo itself; collection wrapped — failures recorded `{"unavailable": reason}`, never fatal to a run; `jester runs` prints nothing extra (readable by SQL) unless trivially cheap; tests `test_store_migration`-style + a run-end test | `pytest -k run_env` green: fresh runs carry env JSON; legacy DB migrates; an env-collection failure does not fail the run |
| T-C3 Docker pinning | `docker-compose.yml`: all four `image:` lines pinned `repo:tag@sha256:…` (digests resolved via registry HTTP API; if unreachable, explicit version tags + honest note); `docker/Dockerfile.python`: base images versioned (no `golang:latest`); `.github/dependabot.yml` comment aligned with what pinning actually buys; small test reads the compose file and asserts every image line carries a tag+digest | compose has zero `latest`/untagged `image:` lines; test `test_docker_pins.py` green; if digests were not resolvable this run, the report says exactly which images defer |

---

## 5.1 Next approved batch (2026-10-29, post-merge) — implemented 2026-10-30

Selected from the still-open items in §1.5 and the 2026-10-25 Batch 1 work order. The batch is deliberately **journal + console + provenance**: it closes both open 2026-10-29 high findings and the upstream P1/P2 tasks that need no human labels or product decision. Every task is test-first; none changes an existing published article or the live archive by default.

| ID | Task | Scope | Acceptance criteria |
|---|---|---|---|
| N1 | `journal measure` | `python/jester/journal/__init__.py` (validation helper only if needed), `python/jester/cli.py` (parser + handler), journal tests, README journal section | `jester journal measure SLUG --channel CH --at ISO --views N [--points N] [--comments N]` records one `channel_metric` row on that channel's latest **posted** publication; refuses a channel with no posted article; refuses an empty metric row and a timestamp without a timezone; prints the next blocked reason. A journal posted on two channels reaches `measured` after 7-day and 30-day rows per channel. |
| N2 | Per-run output hash | `python/jester/journal/__init__.py` (`experiment_run` DDL + `ADDED_COLUMNS` + `add_run`), `python/jester/journal/lab.py` (`run_command`/`_finish_run`), `python/jester/cli.py` (`_print_run`, `journal show`), journal tests | `experiment_run.output_sha256` migrated in place; writer hashes canonical `result` + `stdout` + `stderr`; same output → same hash, different output with the same fingerprint → different hash; legacy/empty rows render “not recorded”; no hand-edited hash path. |
| N3 | Console `/metrics` | `python/jester/console/server.py`, `python/jester/console/api.py` (gauges only), console tests | `GET /metrics` returns Prometheus text (`text/plain; version=0.0.4`); process counters for requests/errors/duration by route+status; DB gauges for queue depth, pending batches, active runs, re-score backlog, nugget/idea totals. Queue gauge matches `/api/overview` counts. Metrics never require a schema migration. |
| N4 | Console schema preflight | `python/jester/store.py` (`schema_problem`), `python/jester/console/api.py`, `python/jester/console/server.py`, tests | A stale archive yields **one** 503 JSON message naming missing tables/columns and the exact `python -m jester.cli migrate --db <path>` command; static UI still serves; `/metrics` still serves; `/api/migrate` remains reachable as the recovery route. A current archive has no preflight error. |
| N5 | `--line-control` contrast | `python/jester/console/static/app.css`, new contrast/encoding test | Light and dark `--line-control` tokens are ≥ 3:1 against their `--surface`; `input/select/textarea` use `--line-control`; static assets contain no U+FFFD replacement characters. |
| N6 | `before_plan` gate (P-H1) | `python/jester/journal/checks.py`, journal checks tests | `run:N` used as a number's proof or as a `backed-by` block is refused when the run's env says `before_plan`; the message says why. Runs without the flag are unchanged. |
| N7 | Export figure re-hash (P-H3) | `python/jester/journal/publish.py`, publish tests | `export` calls `artifact_problem` for every `figures/*` image before copying; an edited, missing, or unregistered figure blocks export with a specific message; a clean figure exports unchanged. |

**Batch status (2026-10-30): IMPLEMENTED and gated.** Every task was written as a failing test first, then implemented; the new test files/assertions were observed failing for the missing behaviour before the code changed. Evidence:

- **Targeted:** `test_journal_{cli,lab,checks,publish}.py` **107 passed**; `test_console_server.py` + `test_console_api.py` **54 passed**; `test_console_contrast.py` **3 passed**; `test_store_schema.py` **3 passed**.
- **Full gates:** Python **1760 passed, 5 skipped, 13 deselected** (459.9 s); Go `go build ./...` + `go test ./...` all ok; frontend `npm run lint` and `npm run build` exit 0; `python -m jester.cli eval` **20 cases (gate=7.0)**; `python -m jester.cli signals check` **21/21**.
- **Contract change disclosed:** N6 intentionally changed the old behaviour where a ledger run attached before the plan could back a number. `test_attach_a_ledger_run` now dates the plan before its ledger run, preserving that test's attach/number intent; the refusal itself is pinned by `test_a_before_plan_run_cannot_back_a_number`.
- **No independent critic sign-off was available** (free-subagent quota). The results above are orchestrator-run and are disclosed as such, consistent with the cycle's standing limitation.

**Deferred from this batch, with reasons:** `extractor_batch_size` cap (needs a batching measurement, not a guess), model digest/quantisation (provider API work), UI smoke (no browser harness), per-role LLM counters (needs hooks in every client), DD-M1/M2/M4/M5/M6 and the remaining PR findings (separate designs; see reviews), and the source-scope decision (owner input).

**Batch exit criteria:** targeted tests green; full Python suite, Go suite, frontend lint/build, eval gate, and signals fixture gate green; independent verification where quota allows (orchestrator-run verification disclosed if not); critic ≥ 8.5/10; zero unresolved blocker/high findings; documents updated with evidence.

**Status: all test and quality gates met; the independent critic score is not claimed.** The free-subagent quota did not allow a second-party review, so the last independent score remains **8.9/10 from the 2026-10-29 cycle**; this batch's verification is orchestrator-run and disclosed. P-H1 and P-H3 are closed. **EX-G3 remains open** — rated **high** in the extraction review and carried as a warning in the final report — and was explicitly deferred, so this batch does not claim that every historical high finding is closed. *(Addendum 2026-10-30: the follow-on batch in §5.2 implemented the EX-G3 attempt-counter work and closed the mechanism; the live backlog number is an observation tracked in §1.5.)*

---

## 5.2 Second post-merge batch (2026-10-30) — EX-G3 completion, DD-M1, DD-M2

Locked from the §1.5 open register after the N1–N7 batch. Three items, all test-first: the new tests were authored and the first targeted run was started **before** any implementation edit, and that run failed at the first missing symbol (`stalled_scores` unimportable from `jester.agents.critic`) — the batch's expected-failure gate.

| ID | Task | Scope | Acceptance criteria |
|---|---|---|---|
| EX-G3 | Fair re-score queue + staleness alarm | `store.py`: `ideas.rescore_attempts` (schema + `_ADD_COLUMNS`), `mark_needs_score` reset; `critic.py`: `stalled_scores`, least-tried-first rotation, increment on refusal, reset on success; `ops.SCORE_STALL_ATTEMPTS = 5`; run line (`cli.py`), `maintenance.queue_counts`/`check_queues`/`cmd_maintenance`; `/metrics` gauge `jester_rescore_stalled` | `rescore_pending` orders by attempts then id; a refusal rotates the queue and increments; a success and `mark_needs_score` clear the counter; `stalled` counts only ideas at ≥5 failed attempts; exact shapes of `rescore_pending` and `queue_counts` extended; the count surfaces in the run line, the maintenance ledger line and `/metrics` |
| DD-M1 | Persist the labeller's incoherence verdict | `clustering.Cluster.incoherent` + `to_payload`; `clusters.incoherent` column (`BASE_SCHEMA` + `_ADD_COLUMNS`), inserted by `replace_clusters`; console list/detail expose it; `app.js` renders a "no shared subject" pill | `to_payload` carries the verdict; a reclustering pass persists `incoherent=1`; `ConsoleAPI.clusters()` and `cluster()` round-trip it; the page marks the theme in list and detail |
| DD-M2 | Re-home drafts across re-clustering | `replace_clusters` re-homes surviving drafts to the new cluster sharing the most `supporting_nuggets`; no-overlap drafts move to `DETACHED_CLUSTER_ID = 0` and surface as a synthetic "Detached drafts" group; drafting from the sentinel stays refused (`cluster_generate_idea(0)` → `no cluster 0`); `app.js` renders the group without a draft button | overlap re-home test (draft follows its comments); no-overlap test (sentinel, not deleted); the detached group is clickable in list and detail; the sentinel has no clusters row |

**Batch status: IMPLEMENTED and gated.** Evidence:

- **Targeted:** the seven touched suites — `test_critic_honest.py`, `test_clustering.py`, `test_console_clusters.py`, `test_maintenance.py`, `test_console_server.py`, `test_rescore_standin.py`, `test_console_api.py` — **137 passed**.
- **Full gates:** Python **1775 passed, 5 skipped, 13 deselected, 41 warnings** (411.95 s; +15 tests over the N1–N7 baseline of 1760); Go `go build ./...` + `go test ./...` all ok; frontend `npm run lint` and `npm run build` exit 0; `python -m jester.cli eval` **20 cases (gate=7.0)**; `python -m jester.cli signals check` **21/21**.
- **Live archive re-measure (read-only, observation not assertion):** still **607 `needs_score` of 2627 ideas**; **85 clusters, 5 drafts, 2 drafts pointing at clusters that no longer exist** — the DD-M2 orphan, visible in production data; neither `ideas.rescore_attempts` nor `clusters.incoherent` is present, because the live DB has not been migrated. The columns arrive with the next operator `jester migrate` (or a nightly `open_db`), the drafts re-home on the next clustering pass, and the backlog drains at `rescore_per_run: 1`.
- **No independent critic sign-off was available** (free-subagent quota) and none is claimed: verification is orchestrator-run and disclosed; the 2026-10-29 cycle's **8.9/10** remains the last independent score.

**Design contract kept:** `_ADD_COLUMNS` entries were also added to `BASE_SCHEMA`, because `schema_problem` compares a live archive against the declared columns — a migrate in place and a fresh create produce the same shape.

---

## 6. Checklist (acceptance gate conditions)

| # | Condition | Status | Evidence |
|---|---|---|---|
| 1 | All three review reports accepted with evidence-graded findings. | **PASS** | `docs/reviews/2026-10-29-*.md`; premises re-verified by the orchestrator before scoping. |
| 2 | Every task register item implemented by its named builder in an isolated worktree. | **PASS** | A `wsa/eval-and-scoring` `a445ab7`; B `wsb/dedup-points-and-labels` `1b4c424`; C `wsc/run-provenance-and-pins` `a265647`. T-A4 stretch partial by declaration. |
| 3 | Every workstream passes its independent verifier (PASS verdict, evidence included). | **PASS with caveat** | Branch acceptance selectors all green; after quota exhaustion the orchestrator ran verification itself. Independent sign-off exists only for iterations 1–4; disclosed in §10 of the final report. |
| 4 | Full Python suite, Go suite, frontend lint/build, and the new gates pass on the integrated branch. | **PASS** | Python **1741 passed, 5 skipped, 13 deselected**; Go all ok + 3 contract tests; frontend lint/build exit 0; eval 20 cases; fixture gate 21/21; export idempotence; dedup-sweep exit 0. |
| 5 | Global critic score ≥ 8.5/10 with weighted breakdown. | **PASS** | **8.9/10**; dimension table in final report §2. |
| 6 | Zero unresolved blocker/high findings; medium/low tracked as warnings. | **PASS** | Only medium/low warnings remain (final report §7); no blocker/high open. |
| 7 | Documentation: roadmap + reviews + verification log merged on the integration branch, then promoted to `main`. | **PASS** | This roadmap, the iteration log, the final report, three reviews, and `docs/dedup-calibration.md` were committed on `integ/master-roadmap-2026-10-29` and promoted to `main` at `70f966f`; the 2026-10-29 post-merge refresh is in §1.5 and §5.1. |

---

## 7. Verification log

| # | What was run | Result | Time (local) |
|---|---|---|---|
| 1 | `git status` / `git log` / `git branch -a` | clean tree on `feat/ui-corporate-redesign` | session start |
| 2 | `go test ./...` | all packages ok | session start |
| 3 | `python -m pytest python/tests -q --tb=no` | **1679 passed, 5 skipped, 13 deselected**, 41 warnings (41 previously known: utcnow deprecations, qdrant version-check warning), 431.6 s | session start |
| 4 | Reviews by three independent agents | **complete** — `docs/reviews/2026-10-29-{extraction-scoring-evaluation,dedup-clustering-classification,provenance-reproducibility}.md`; all load-bearing premises re-verified by the orchestrator before scoping builders | — |
| 5 | Task register finalized in §5; three builders launched in isolated worktrees (`wsa-eval-gate` → `wsa/eval-and-scoring`, `wsb-dedup-labels` → `wsb/dedup-points-and-labels`, `wsc-run-provenance` → `wsc/run-provenance-and-pins`) | running | — |
| 6 | Builder A acceptance selectors + gate | **80 passed**; `jester eval` **20 cases (gate=7.0)**; test edits audited (contract updates, no weakening) | post-build |
| 7 | Builder C acceptance selectors | **62 passed** (`test_run_env`, `test_journal`, `test_journal_lab`, `test_docker_pins`); `--code` usage error and ledger envelope verified | post-build |
| 8 | Builder B selectors + fixture gate | **34 + 22 + 105 passed**; `signals check` **21/21** (ambiguous, edits, failures, irrelevant, relevant) | post-build |
| 9 | Orchestrator completes T-B3 and commits it | `8ee7ad6`; 58 passed (filters/fixtures) + 21/21 gate | iteration 5 |
| 10 | B-branch audit | Found console hash stamp + duplicate sweep constants/dead helper; fixed in `1b4c424`; **84 passed** | iteration 6 |
| 11 | Integration branch + merges | A fast-forward `a445ab7`; C merge `eeeb0e0`; B merge `61783bc`; one `store.py` `_ADD_COLUMNS` conflict resolved by keeping both ALTERs | iteration 9 |
| 12 | Full suite, attempt 1 | **1739 passed, 5 skipped, 13 deselected, 1 failed**: config registry missing `dedup_threshold_by_category` | iteration 10 |
| 13 | Registry remediation | Row added to local gitignored `jester-setup.md`; README `journal run` usage corrected; lint **9 passed** | iteration 11 |
| 14 | Cross-workstream suites | **166 passed** across the ten integration-seam suites | iteration 12 |
| 15 | Full suite, attempt 2 | **1740 passed, 5 skipped, 13 deselected** (416.3 s); Go all ok; frontend lint/build exit 0 | iteration 13 |
| 16 | CI mirrors | export **5 new/0** then **0 new/5** with manifest reconcile; Go contract **3 PASS**; fixture gate **21/21**; eval **20**; dedup-sweep three tables, exit 0 | iteration 14 |
| 17 | C2 audit fix | Failed `git status` no longer recorded as clean (`1f999ac`); **60 passed** in run-env/journal suites; README committed | iteration 15 |
| 18 | Final full suite + critique | **1741 passed, 5 skipped, 13 deselected** (414.3 s) at final head; weighted critic **8.9/10**; zero blocker/high open | iteration 16 |
| 19 | Upstream sync + promotion | `origin/main` was 8 commits ahead; merged as `7ce8d44` with no conflicts; full suite re-run **1741 passed** (409.8 s); `main` fast-forwarded to `7ce8d44` and pushed to `origin/main` | promotion |
| 20 | Post-merge re-check: repository state | `HEAD == origin/main == 70f966f`; clean tree; 76 upstream commits beyond the baseline; new `chat/`, `scheduler.py`, `edits.py`, `specs.py`, `maintenance.py`, console chat present | post-merge |
| 21 | Post-merge re-check: task claims | T-A1/A2/A3, T-B1/B2/B3, T-C1/C2/C3 re-verified against current symbols; T-A4 partial; EX-G3 **607/2627** on the live DB; P-H1/P-H3 re-verified open | post-merge |
| 22 | Post-merge re-check: upstream Batch 1 | `journal measure`, `output_sha256`, `/metrics`, schema preflight, batch cap, model digest, UI smoke all still absent; `signal_rules.yaml` still provisional | post-merge |
| 23 | Next batch scoped | §5.1 N1–N7 selected; deferred items listed with reasons | post-merge |
| 24 | N1–N7 test-first implementation | 13 expected failures observed first (2 measure, 2 output-hash, 1 before-plan, 2 export, 5 metrics/preflight, 2 contrast minus 1 expected pass); then implemented `measure`, `output_sha256`, `/metrics`, schema preflight, `--line-control`, before-plan refusal, export re-hash | 2026-10-30 |
| 25 | Targeted suites | journal **107 passed**; console **57 passed** (server/API + contrast); store schema **3 passed** — one pre-existing journal-lab assertion was updated because N6 intentionally changed its premise (plan now predates the ledger run) | 2026-10-30 |
| 26 | Full gates | Python **1760 passed, 5 skipped, 13 deselected** (459.9 s); Go build + all tests ok; frontend lint/build exit 0; eval **20 cases (gate=7.0)**; signals **21/21** | 2026-10-30 |
| 27 | Documents refreshed with evidence | §1.5 statuses, §5.1 batch status, review addenda (P-H1/P-H3), work-order pre-flight, final-report addendum, iteration log | 2026-10-30 |
| 28 | Promotion | Batch commit `ad52b87` pushed to `origin/main`; `HEAD == origin/main` at close | 2026-10-30 |
| 29 | Post-merge re-check of the N1–N7 batch | docs audited against actual code; stale-claim correction: no independent critic score claimed, EX-G3 not claimed closed; next batch locked (EX-G3 completion, DD-M1, DD-M2) | 2026-10-30 |
| 30 | EX-G3/DD-M1/DD-M2 failing tests authored on top of `a71ea1f`; targeted run started before any implementation edit | failed at the first missing symbol (`stalled_scores` unimportable) — the batch's expected-failure gate | 2026-10-30 |
| 31 | Implementation + targeted suites | **137 passed** (critic-honest, clustering, console-clusters, maintenance, console-server, rescore-standin, console-api) | 2026-10-30 |
| 32 | Full gates | Python **1775 passed, 5 skipped, 13 deselected** (411.95 s); Go build + all tests ok; frontend lint/build exit 0; eval **20 cases (gate=7.0)**; signals **21/21** | 2026-10-30 |
| 33 | Live archive re-measure (read-only) | **607/2627** needs_score; 85 clusters, 5 drafts, **2 orphaned** (DD-M2 visible); `rescore_attempts`/`clusters.incoherent` absent | 2026-10-30 |
| 34 | Documents updated + promotion (this batch) | §1.5, §4, §5 stretch, §5.2, review addenda; batch commit `9d1157d` + docs commit pushed; `HEAD == origin/main` at close | 2026-10-30 |

*(Final state recorded above; the closing report is `docs/reports/2026-10-29-final-implementation-report.md` and the chronology is `docs/plans/2026-10-29-implementation-iteration-log.md`.)*
