# Implementation iteration log — 2026-10-29

**Baseline:** `771d3d5` on `feat/ui-corporate-redesign`, clean tree.
**Integration branch:** `integ/master-roadmap-2026-10-29`.
**Remediation budget:** 20 iterations; **6 used** (each iteration ends in a verification result, not a commit count).

This log is chronological and auditable. It records the builders, what verification actually ran, what failed, and how each failure was closed. The final report (`docs/reports/2026-10-29-final-implementation-report.md`) carries the verdict and score.

| # | Iteration | Action | Result / evidence |
|---|---|---|---|
| 1 | Baseline | Ran the full Python suite, Go suite, and recorded toolchain and branch state. | Python **1679 passed, 5 skipped, 13 deselected** (431.6 s); `go test ./...` all packages ok; Python 3.12.10 / Go 1.26.7 / Node 24.11. |
| 2 | Assessment | Three independent review agents produced extraction/scoring/evaluation, dedup/clustering/classification, and provenance/reproducibility reports. Orchestrator re-verified every load-bearing premise (line numbers, callers, CI steps, config keys) before scoping work. | `docs/reviews/2026-10-29-*.md`; gap register in the roadmap §4. |
| 3 | Build launch | Three builders in isolated git worktrees with exclusive file ownership and defined acceptance: A (`wsa/eval-and-scoring`), B (`wsb/dedup-points-and-labels`), C (`wsc/run-provenance-and-pins`). | A: 4 commits. C: 3 commits. B: 2 commits, T-B3 in flight. |
| 4 | Quota exhaustion | Free subagent quota was exhausted mid-cycle. Verifier A had run its checks but did not deliver a final sign-off; no further verifier or critic subagent could start. | Disclosed as a process limitation; orchestrator completed verification and critique itself. |
| 5 | Builder B completion | Orchestrator finished T-B3 (vendor veto, 21-case fixture gate, shared `reason_with_matched` for from-archive), ran its selectors, and committed it as `8ee7ad6`. | 34 passed (vendor/veto/fixtures/from_archive); 22 passed (point-id/ghost/per-category); 105 passed (adjacent suites); `signals check` **21/21**, all five categories. |
| 6 | Builder B audit | Orchestrator audited T-B1/T-B2 and found two real leftovers: console reembed still stamped `abs(hash(key))` (the exact bug B1 fixed elsewhere), and `dedup_sweep.py` defined the sweep grid twice plus an unused helper whose docstring promised stems but returned Paths. | Fixed on the B branch in `1b4c424`; added a console-stamp regression assertion. Targeted suites **84 passed**. |
| 7 | Builder A verification | Orchestrator inspected the full diff and every existing-test edit, then ran the acceptance selectors and the gate itself. | **80 passed** (eval gate, R53, score-backlog, extract batch, critic, e2e, dedup eval); `python -m jester.cli eval` → **eval OK: 20 cases passed (gate=7.0)**. Test edits are contract updates (new `i` echo / run envelope), not weakened assertions. |
| 8 | Builder C verification | Orchestrator inspected the full diff and test edits, then ran the acceptance selectors. | **62 passed** (run env, journal, journal lab, docker pins); `journal run` without `--code` is a usage error; `attach_ledger` carries `code:{source:ledger}`. |
| 9 | Integration | Created `integ/master-roadmap-2026-10-29` from `771d3d5`; fast-forwarded A; merged C and resolved the single conflict in `store.py` `_ADD_COLUMNS` by keeping both `ideas.competition_checked_at` (T-A2) and `runs.env` (T-C2); merged B with no conflicts. | Merge commits `eeeb0e0` (C) and `61783bc` (B). |
| 10 | Full regression, attempt 1 | Ran the complete Python suite on the integrated branch. | **1739 passed, 5 skipped, 13 deselected, 1 failed**: `test_config_registry.py::test_every_shipped_config_key_is_registered` — `dedup_threshold_by_category` was live in `thresholds.yaml` but absent from the registry table in the gitignored `jester-setup.md`. Real doc-lint failure, not a flaky test. |
| 11 | Remediation | Added the registry row to `jester-setup.md` (local-only by design), corrected README's `journal run` usage to show the now-required `--code`, and re-ran the lint. | `test_config_registry.py` **9 passed**. |
| 12 | Cross-workstream tests | Ran the ten suites that exercise the integration seams (point ids, per-category, R53, run env, journal lab, extract batch, eval gate repo, signals filters/fixtures, new-code coverage). | **166 passed**. |
| 13 | Full regression, attempt 2 | Re-ran the complete Python suite, Go suite, and frontend lint/build. | Python **1740 passed, 5 skipped, 13 deselected** (416.3 s); Go all ok; frontend lint exit 0 and build exit 0. |
| 14 | CI mirrors | Ran the non-pytest CI steps locally: documented `signals export` twice on a fresh DB, Go contract tests against a Python-created DB, `signals check`, `eval`, and the new `dedup-sweep --pairs-dir`. | Export: **5 new / 0 seen**, then **0 new / 5 seen**, manifest reconciles, 5 DB rows = 5 CSV rows. Contract: 3 tests PASS. Fixture gate **21/21**. Eval **20 cases**. Sweep prints three category tables, exit 0. |
| 15 | C2 audit | Orchestrator found one half-record in C2: a failed `git status` was written as `dirty: false` even though `rev-parse` had proved the checkout. | Fixed in `1f999ac`: keep the commit, write `dirty: null` plus the reason; `test_run_env` pins the half-record case. Target suites **60 passed**. |
| 16 | Final regression + critique | Orchestrator re-ran the full Python suite after the C2 fix: **1741 passed, 5 skipped, 13 deselected** (414.3 s); assembled the weighted critic score and remaining-issue register; committed documentation. | Score **8.9/10**; final report and roadmap §7 updated. |

## Promotion (after iteration 16)

- `origin/main` had moved 8 commits ahead of the cycle head (PR #74 UI follow-ups plus the earlier 2026-10-25 roadmap/research set). It was merged into the integration branch as `7ce8d44`; no conflicts, including the README.
- The full gate was re-run on the merged tree: Python **1741 passed, 5 skipped, 13 deselected** (409.8 s); Go, frontend lint/build, `eval`, and `signals check` all green.
- Local `main` was 76 commits behind `origin/main`; it was fast-forwarded to `origin/main` (`86c7a31`), then fast-forwarded to the merged tip `7ce8d44` and pushed (`86c7a31..7ce8d44 main -> main`).
- Branches fully merged into `main` were deleted, including all three builder branches and the integration branch; the three cycle worktrees were removed. Unmerged branches and the user worktrees that hold them were left intact.

## Post-merge batch (2026-10-30)

| # | Phase | What was done | Evidence |
|---|---|---|---|
| 17 | Re-check + re-scope | Re-verified every load-bearing 2026-10-29 claim against `main` at `70f966f`; updated the roadmap §1.5, the three review addenda, and the 2026-10-25 work order; selected N1–N7 | Documents committed with the batch; no code changed in this step |
| 18 | RED | Added failing tests for N1–N7 and fixed one test regex that matched the font-reset rule instead of the form-control rule | 13 expected failures / 2 expected passes before implementation |
| 19 | GREEN | Implemented `journal measure` + metric-time validation, `experiment_run.output_sha256`, `/metrics`, schema preflight, `--line-control`, before-plan refusal, export figure re-hash | Journal **106**, console **54**, contrast **3** passed |
| 20 | Gate + document | Full Python, Go, frontend, eval and signals gates; added characterization tests for a locked archive and a missing figure; one pre-existing ledger assertion re-dated because N6 changed its premise; appended evidence to the roadmap, review addendum, work order, README and final report | Python **1760 passed, 5 skipped, 13 deselected** (459.9 s); Go all ok; frontend lint/build 0; eval 20; signals 21/21 |

## What the iterations changed about the plan

- **No task was silently dropped.** T-A4 (stretch) is explicitly **partial**: the `SCORE_BACKLOG` flag shipped, the per-idea rescore-attempt counter did not. It remains tracked under EX-G3.
- **Three verifier fixes were needed after builder sign-off:** B's console stamp and sweep cleanup, C's status half-record, and the config-registry row. Each has a regression test except the registry row, which is enforced by the existing lint.
- **Independence was reduced, not simulated.** After the quota failure the orchestrator ran the verifier commands itself. The final report states which sign-offs are second-party (none after iteration 4) and which are orchestrator-executed.
