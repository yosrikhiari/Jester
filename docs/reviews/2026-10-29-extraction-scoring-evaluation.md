# Review — Extraction / Scoring / Evaluation (2026-10-29)

**Reviewer:** independent review agent (read-only) · **Baseline:** `771d3d5`
**Scope:** `cli.py`, `cli_review.py`, `llm.py`, `llm_quota.py`, `roles.py`, `config.py`, `scoring.py`, `ops.py`, `triviality.py`, `agents/*`, `store.py`, console eval surface, CI workflow, `eval/*`, `jester-setup.md` claims, tests.
**Editor verification (orchestrator):** re-checked before scoping: `jester eval` computes `compute_overall` on file-supplied subscores with no LLM involved (`cli_review.py:46-68`); no CI job runs it (grep over `.github/workflows`); `apply_scores` unconditionally overwrites `competition`, flips `competition_checked`, and wipes `competitor_notes` (`agents/critic.py:76-88`); `rescore_pending` has no TTL/checker consult (`critic.py:115-140`); the batch prompt trusts order with no identity check and acceptance is list+length only (`llm.py:412-480`).

---

## Post-merge status (2026-10-29)

Baseline `771d3d5`; re-checked against `main` at `70f966f` (clean, `== origin/main`). Findings are kept as written; this table is the current disposition.

| Finding | Status | Current evidence |
|---|---|---|
| G-1 eval gate never automated | **RESOLVED** | `.github/workflows/ci.yml:107-108` runs `python -m jester.cli eval`; `cli_review.py:54-66` reads UTF-8; eval reports **20 cases (gate=7.0)** |
| G-2 re-scores clobber competition | **RESOLVED** | `critic.py:66-110` preserves a web-checked verdict when the reply has no number; `idea_dedup.py:99-140` consults `needs_recheck` |
| G-3 607-idea re-score backlog | **PARTIAL / OPEN** | `SCORE_BACKLOG` ships (`ops.py:14,55-56`); no attempt counter; live DB still **607 `needs_score` of 2627 ideas** |
| G-4 batch order trusted | **RESOLVED** | `llm.py:444-505` validates the per-item `i` echo and falls back per comment on mismatch |
| G-5 promotion metrics never computed | **OPEN** | no promotion-metric computation found |
| G-6 `competitor_ttl_days` dead-wired | **RESOLVED** | non-test callers now in `idea_dedup.py:114-140` |
| G-7 hung-model brake absent | **OPEN** | no consecutive-failure brake found |
| G-8 run-granular prompt versions | **OPEN** | per-comment fallback still records the run-level version only |
| G-9 eval platform encoding | **RESOLVED** | `cli_review.py:54` passes `encoding="utf-8"` |
| L-1…L-6 | **OPEN** | misnomers/derived-count/doctor-coverage/coercion-counter/duplication unchanged; not promoted into the next batch |

**Not covered by this review, now on `main`:** `chat/`, `scheduler.py`, `edits.py`, `specs.py`, `maintenance.py`, console chat. They need a separate audit; this review's scope was the extraction/scoring/evaluation chain.

---

## 1. Chain map (verified highlights)

Ingest → `ingest_batch` → `cmd_run` (roles resolved; per-model quota pauses; stale-run reap; run row stamped `models_used` incl. role records, env overrides, `prompt_versions`/`rubric_version`; bounded bite ≈120 comments with 60-min reap pairing). Per-comment prefilter → extraction via single call or batched path (`extractor_batch_size`, malformed/short/throwing batches degrade to the same single path; fake-stamp refusal; quota stop). R37 triviality tag → archivist (dedup, model-mismatch guard, defer on outage) → run summary incl. `trivial_share`.

Synthesizer: clusters by (platform, thread), split oversized groups, skip singletons (R50), **refuses to archive stand-in synthesis** (seen live: 26 archived ideas once), rate-limit brakes, golden gate R48, idea dedup by cosine + R49 re-score on merge. Critic: `apply_scores` accepts only the asked critic's stamp; new ideas left unscored, existing filed `needs_score=1`; `rescore_pending` (config `rescore_per_run: 1` default) drains oldest-first, stops on quota. Rubric pure and locked (`0.4d + 0.3f + 0.3(11−c)`, imputed 1.8 checked-free). Repair path for the stand-in-contaminated archive exists with CSV audit (`data/audit/standin-scores-*.csv`).

## 2. What the eval harness actually is

`jester eval` (+ console `/api/eval`) checks the **scoring arithmetic** against hand-labelled subscores in `eval/known_good.md`/`eval/known_bad.md` (20 cases, gate 7.0 — passes today). **No LLM invoked; no CI step runs it;** the scheduler and doctor don't run it either; no test reads the repo's real eval files. Tokens/idea is documented as a promotion metric but computed nowhere. Documented claims not implemented: critic calibration of known titles (`jester-setup.md`), golden-fixture regression layer (`python/jester/eval/fixtures/` — directory does not exist), R51 cross-run three-night tests (`testdata/cross_run/` — absent), versioned dedup-eval pairs.

## 3. Gaps

- **G-1 (high) — the critic calibration gate never touches the critic and runs nowhere automated.** A critic model bump or prompt rewrite leaves every automated check green while the archive's `overall` drifts. Remediation: wire the existing gate into CI (`python -m jester.cli eval` step + a repo-files pytest), correct the `config.py:175-179` comment to name what the gate really checks (formula calibration; batch parity lives in the J2 e2e test), and optionally add an advisory script-critic calibration mode.
- **G-2 (high) — re-scores clobber web-verified competition (R53 violated).** `apply_scores` wipes `competitor_notes` and flips `competition_checked` on every scoring pass; `rescore_pending` never consults `competitors.needs_recheck` (which has zero non-test callers); merges always re-spend a web call. A verified competition 4.0 + notes can silently become 9/null → imputed 5 — an §7.2 comparability regression stamped as fresh by `last_scored_at`.
- **G-3 (high) — the 607-idea `needs_score` backlog has no assertion, alarm, or staleness bound** (measured on `data/jester.db` read-only). At `rescore_per_run: 1` with synthesize/critic on the same daily quota, the backlog can outlive days of green runs. No floor flag; no attempt counter.
- **G-4 (high) — batched extraction trusts item order with no verification.** The prompt asks for order ("item i describes comment i"), acceptance is `isinstance(list) and len == len` — a reordered reply misattributes insights to comments silently under the real model's stamp, and parity tests use order-aligned fakes.
- **G-5/G-6/G-7 (medium)** — promotion metrics documented, never computed (G-5); `competitor_ttl_days` is an editable knob no production code reads (G-6, dead-wire config); a hung (non-rate-limited) model costs `TIMEOUT_S` per comment with no consecutive-failure brake (G-7).
- **G-8/G-9 (medium)** — prompt versions are run-granular only, so a v2 run with v1 per-comment fallback records a single version (G-8); `cmd_eval` reads eval files with platform-default encoding → `UnicodeDecodeError` crash risk on Windows for non-ASCII rows (`cli_review.py:54`, fixed only on the console twin) (G-9).
- **Low:** L-1 `n_discarded_trivial` misnomer (nothing is discarded); L-2 `merge_supporting` sets `source_threads = len(merged)` (nugget count, not thread count); L-3 doctor doesn't check the re-score backlog or run the eval gate; L-4 ALS_TRIVIAL counts by caller-supplied run ids; L-5 silent category coercion has no counter; L-6 CLI/console eval duplicated rather than shared.

## 4. Measured vs hypothesized

Measured: 602/2,588 ideas matched the stand-in fingerprint (historical), 430 above the bar; 3 residual fingerprint rows + 607 `needs_score` counted read-only this session; `jester eval` passes 20/20 today; 3-hour treat hang and per-call timeout arithmetic (15/29 s per comment on Ollama; `TIMEOUT_S=180`); bounded-bite pairing asserted in tests.
Hypothesized: tokens/idea gains from batching; critic behavior against known titles (needs the key + a live run); competition-verdict quality at free DuckDuckGo budget.

## 5. Candidate workstreams

1. **Wire the eval gate in:** repo-files pytest + CI step + encoding fix + comment truthing.
2. **Honor checked competition (R53):** preserve checked competition/notes on critic re-scores whose reply lacks competition; consult the TTL before web re-checks so the knob becomes live.
3. **Backlog visibility:** `SCORE_BACKLOG` floor flag + rescore attempt counters + run-line surfacing.
4. **Batch order echo:** item `i` echoed and verified; mismatch → per-comment fallback; scripted reversed-reply test.
5. (Advisory) critic calibration harness + corpus-level batch-vs-single metrics.
6. (Advisory) prompt-version stamping on drafts/rows.
