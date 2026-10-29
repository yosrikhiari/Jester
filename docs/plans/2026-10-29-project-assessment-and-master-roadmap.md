# Jester — Project Assessment and Master Roadmap

**Date:** 2026-10-29 · **Baseline commit:** `771d3d5` on `feat/ui-corporate-redesign` (working tree clean)
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
  image-digest pinning (Dependabot configured), non-root images, per-category dedup
  thresholds (tool shipped, labels pending), `/metrics` + OTel spans (access log is the
  first slice), `vitest` for the console static UI.
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
  nugget quality, and grep shows **no CI job runs it** (`.github/workflows/ci.yml`).
- `PROMPT_VERSIONS = {"extractor": 2, "synthesizer": 1, "critic": 1, "labeller": 1}`
  (`python/jester/llm.py:46`); recorded per run (`cli.py:314`) and exposed in the console
  (`console/api.py:277`).
- `dedup-sweep` (`python/jester/dedup_sweep.py`) is offline/deterministic and pure over a
  JSONL pairs file; **no labelled pairs dataset exists** (`testdata/` holds one Reddit
  thread fixture only). Per-category thresholds remain unapplied in the archivist.
- `runs` table (`python/jester/store.py:168-191`) records `models_used`, funnel,
  `trivial_share`, `origin` — but **no OS/interpreter/code fingerprint** of the kind the
  journal's `journal run` records for experiment runs.
- `pyproject.toml:10-16`: runtime `dependencies` still list `pytest>=8.0`; no
  `import pytest` exists in shipped code (`python/jester/**` grep clean).
- CI today (`.github/workflows/ci.yml`): clean-clone lock install, signals
  export-twice idempotence, `signals check` fixture gate, pytest+coverage, advisory
  pip-audit (`continue-on-error`), Go build/test + contract tests against a
  Python-created DB, frontend lint+build, Sonar scan when token present.

### 1.4 Final state (this cycle, after integration)

- Integration branch `integ/master-roadmap-2026-10-29` built from `771d3d5`; main untouched.
- All nine registered tasks landed (A1–A3, B1–B3, C1–C3); stretch T-A4 partial (`SCORE_BACKLOG` flag only).
- Final full Python suite: **1741 passed, 5 skipped, 13 deselected**; Go all ok; frontend lint/build ok; CI-mirror checks ok (eval 20, fixture gate 21/21, export idempotence, Go contract).
- Three verifier-fix commits after builder sign-off: console reembed ID, sweep cleanup, `runs.env` status half-record; plus a local registry-row fix for the config lint.
- Global critic score **8.9/10**; zero blocker/high findings open. Full detail in `docs/reports/2026-10-29-final-implementation-report.md`; chronology in `docs/plans/2026-10-29-implementation-iteration-log.md`.

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

Consolidated from the three review reports (full enumerations live there). Disposition key: **ADOPT** → task register below · **DEFER** → recorded, not in this cycle · severity: blocker/high/medium/low.

| ID | Area | Severity | One-line statement (evidence, verified) | Disposition |
|---|---|---|---|---|
| EX-G1 | Extraction/eval | high | `jester eval` checks only the scoring arithmetic, no LLM, and runs in no CI job, scheduler task, doctor check, or test (`cli_review.py:46-68`; `ci.yml`) | ADOPT → T-A1 |
| EX-G2 | Scoring | high | `apply_scores` clobbers web-verified competition: wipes `competitor_notes`, flips `competition_checked`; `rescore_pending` never consults the TTL (`critic.py:76-88,115-140`) | ADOPT → T-A2 |
| EX-G4 | Extraction | high | Batched extraction trusts item order; acceptance is list+length only (`llm.py:412-480`) — reordered reply misattributes insights | ADOPT → T-A3 |
| EX-G3 | Scoring | high | 607 `needs_score` backlog (measured), no floor flag/alarm/attempt counter | DEFER (stretch for A-builder if all green) |
| EX-G5,G7,G8, L1-L6 | Extraction | med/low | Unmeasured promotion metrics; hung-model brake absent; run-granular prompt provenance; misnomers, minor coersion counters | DEFER (recorded in review) |
| EX-G6 | Scoring | medium | `competitor_ttl_days` knob dead-wired (zero non-test callers) | ADOPT → T-A2 |
| EX-G9 | Eval | medium | `cmd_eval` reads eval files with platform encoding → Windows crash on non-ASCII (`cli_review.py:54`) | ADOPT → T-A1 |
| DD-H1 | Dedup | high | Qdrant point IDs from process-salted `hash()` (`vector.py:195,227`, `archivist.py:76`, `cli.py` reembed, `idea_dedup.py:42`) — cross-process duplication demonstrated | ADOPT → T-B1 |
| DD-H2 | Dedup | high | Dedup self-match discards the matched key; kill between upsert and insert loses a nugget forever, unrecorded (`archivist.py:69-81`) | ADOPT → T-B1 |
| DD-H3 | Classification | high | Negative families cannot veto a buyer verdict; vendor launch post can clear the bar with "against it" printed but ungated (`filters.py:265-280`) | ADOPT → T-B3 |
| DD-M3 | Dedup | medium | Global 0.87; no labelled pairs anywhere; `dedup-sweep` has no per-category mode (`dedup_sweep.py`, `cli.py:3726`) | ADOPT → T-B2 |
| DD-M7 | Classification | medium | From-archive records lose `[matched: …]` provenance (`from_archive.py:261`) | ADOPT → T-B3 |
| DD-M1,M2 | Clustering | medium | Labeller `coherent:false` dropped; re-clustering orphans cluster drafts (schema churn) | DEFER (follow-up PR) |
| DD-M4 | Property dedup | medium | Semantic layer unreachable; empty-collection bypasses unknown-semantics gate | DEFER (needs vector-sync design) |
| DD-M5,M6 | Dedup ops | medium | `INDEXING_THRESHOLD` applies only on create; `reembed --all` cannot complete an embedding-model change | DEFER (follow-up PR) |
| DD-L1..L8 | Dedup | low | Hard-coded embedding_model/near-miss band/payload asymmetry/etc. | DEFER (recorded) |
| PR-B1 | Journal gate | **blocker** | Code provenance opt-in: omitted `--code` → `{}` envelope → `counts_for_gate` passes trivially (`lab.py:118-126,294-301`; `cli.py:3284,2943`) — contradicts the README's documented rule | ADOPT → T-C1 |
| PR-H2 | Pipeline runs | high | `runs` rows carry no environment/code fingerprint (`store.py:168-191`) | ADOPT → T-C2 |
| PR-M2 | Supply chain | medium | 4 compose images unpinned (2× `latest`, 1 untagged, 1 tag-no-digest); dependabot comment describes pinning that does not happen (`docker-compose.yml:39,163,209,235`) | ADOPT → T-C3 |
| PR-H1,H3,H4,M1..M5 | Provenance | med | `before_plan` display-only; export lacks figure re-verification; overwrite claims without audit rows; etc. | DEFER (recorded in review) |

## 5. Task register (approved remaining work — this cycle)

Nine tasks, three per workstream. Each task: one builder, one verifier, defined acceptance. Builder file ownership is exclusive per workstream; WS-A alone touches `ci.yml`.

### WS-A — Extraction / scoring / evaluation (builder A · worktree `wsa-eval-gate`)

| Task | Scope | Acceptance criteria |
|---|---|---|
| T-A1 Wire the eval gate in | `cli_review.py` (encoding `utf-8`), new `python/tests/test_eval_gate_repo.py` running `cmd_eval` against the repo's `eval/known_good.md`+`known_bad.md`, `.github/workflows/ci.yml` (step after Fixture gate: `python -m jester.cli eval`), correct `config.py:175-179` comment so it names what the gate really checks (formula calibration; batch parity = the J2 e2e equality test) | `pytest -k eval_gate` green; mutating a known_good subscore to fail-overall makes the test fail; CI yaml parses (yaml.safe_load) and contains the eval step; `python -m jester.cli eval` exits 0 on the repo files |
| T-A2 Honor checked competition (R53) | `agents/critic.py`: a critic reply without `competition` on an idea previously web-checked preserves `competition`/`competitor_notes`/`competition_checked` and recomputes overall with the preserved value; reply with a number keeps today's fresh-check semantics; wire `competitors.needs_recheck` (or its stamp) into the merge re-check path `idea_dedup.py` so a fresh check is not re-spent; tests in `test_critic_honest.py` (or new `test_rescore_competition.py`) | `pytest -k rescore_competition or needs_recheck` green: preserved-competition case, fresh-supplied case, TTL-fresh merge skips the fake search transport (`calls` unchanged), TTL-stale merge re-checks once |
| T-A3 Batch order echo | `llm.py` batch prompt gains a per-item `i` (echo of the comment index) validated on parse; mismatch (missing/wrong `i`) → per-comment fallback as today; `agents/extractor.py` passes nothing extra (indices derive from bodies order in `llm.py`); scripted reversed-reply test | `pytest -k extract_batch` all green incl. new `test_extract_batch_reversed_reply`: reversed items → fallback engaged (`fallbacks==1`), comments carry their own insights (compared against single-call oracle), no mis-zipped row archived |

Stretch (only if T-A1..A3 green): T-A4 `SCORE_BACKLOG` floor flag + rescore attempt counter (EX-G3).
**Status: PARTIAL** — `SCORE_BACKLOG` shipped in `a445ab7` and is wired into the run floor flags; the per-idea attempt counter did not ship and remains tracked under EX-G3.

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

## 6. Checklist (acceptance gate conditions)

| # | Condition | Status | Evidence |
|---|---|---|---|
| 1 | All three review reports accepted with evidence-graded findings. | **PASS** | `docs/reviews/2026-10-29-*.md`; premises re-verified by the orchestrator before scoping. |
| 2 | Every task register item implemented by its named builder in an isolated worktree. | **PASS** | A `wsa/eval-and-scoring` `a445ab7`; B `wsb/dedup-points-and-labels` `1b4c424`; C `wsc/run-provenance-and-pins` `a265647`. T-A4 stretch partial by declaration. |
| 3 | Every workstream passes its independent verifier (PASS verdict, evidence included). | **PASS with caveat** | Branch acceptance selectors all green; after quota exhaustion the orchestrator ran verification itself. Independent sign-off exists only for iterations 1–4; disclosed in §10 of the final report. |
| 4 | Full Python suite, Go suite, frontend lint/build, and the new gates pass on the integrated branch. | **PASS** | Python **1741 passed, 5 skipped, 13 deselected**; Go all ok + 3 contract tests; frontend lint/build exit 0; eval 20 cases; fixture gate 21/21; export idempotence; dedup-sweep exit 0. |
| 5 | Global critic score ≥ 8.5/10 with weighted breakdown. | **PASS** | **8.9/10**; dimension table in final report §2. |
| 6 | Zero unresolved blocker/high findings; medium/low tracked as warnings. | **PASS** | Only medium/low warnings remain (final report §7); no blocker/high open. |
| 7 | Documentation: roadmap + reviews + verification log merged on the integration branch. | **PASS** | This roadmap, the iteration log, the final report, three reviews, and `docs/dedup-calibration.md` are committed on `integ/master-roadmap-2026-10-29`. |

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

*(Final state recorded above; the closing report is `docs/reports/2026-10-29-final-implementation-report.md` and the chronology is `docs/plans/2026-10-29-implementation-iteration-log.md`.)*
