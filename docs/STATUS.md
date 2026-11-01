# Status

The one place to answer "is it done?". Every register in `docs/` maps an item
ID to its evidence; this page says which of them is current and where the
remaining work sits. **If this page and another document disagree about a
status, this page wins** — fix the other document in the same change.

**As of 2026-11-01.** Code status is against the working tree built on
`36d357b` (`main`); items marked *(branch)* are written and tested but not yet
committed. The live archive was **not** modified by this cycle.

Status words:

- **Shipped** — code, tests and docs are on `main` and the named suite passes.
- **Shipped (branch)** — same, but uncommitted at the time of writing.
- **Owner-gated** — the code side is ready; the next step needs your machine,
  your data, your credentials, your labels, or your judgement.
- **Not started** — no work done.
- **Not this cycle** — deliberately deferred.

## The products

| Product | Status | Evidence |
|---|---|---|
| Idea pipeline | **Paused since 2026-10-02.** Three scheduled tasks registered and disabled; last exit 0 means "switched off", not "succeeded". | [README §Operating state](../README.md#operating-state); `jester schedule status` |
| Problem signals | **Live**, five scheduled tasks; fixture gate green 2026-10-31. | `.github/workflows/ci.yml` (`signals check`); `config/schedule.yaml` |
| Journal | **Built through J3; 0 publications.** | [README §Journal](../README.md#journal--research-experiment-publish-in-progress); `data/journal.db` |
| Console chat | **Built; 0 sessions.** `chat_enabled: false`. | `python/jester/chat/`; `config/thresholds.yaml` |

## Registers, and which one to read

| Register | Covers | Authoritative section |
|---|---|---|
| **S-01 … S-24** | 2026-10-31 strategy items (reliability, evaluation, security, ops) | [2026-10-31 report §9](reports/2026-10-31-next-development-strategy.md), status in [§15](reports/2026-10-31-next-development-strategy.md) |
| **ND-01 … ND-14** | 2026-11-01 follow-up items | [2026-11-01 report §2.2](reports/2026-11-01-next-development-strategy-v2.md); status below |
| **F-01 … F-17** | 2026-10-29 codebase findings | [Master roadmap §1.5](plans/2026-10-29-project-assessment-and-master-roadmap.md); F-04/F-07/F-12/F-13/F-15 remain known issues |
| **DD-M4/M5/M6, J0–J3** | Dedup and journal slices | [2026-10-29 final report](reports/2026-10-29-final-implementation-report.md) |

## ND register

| ID | Status | What exists | What remains (owner unless stated) |
|---|---|---|---|
| ND-01 | **Owner-gated** | Verified archive snapshot taken 2026-11-01 (`data/backups/jester-20261101T071405Z.db.gz`, integrity OK) | `jester migrate`, then `jester rescore --pending` in bounded windows; 607 ideas unscored |
| ND-02 | **Shipped (branch)** | `schedule status` prints PAUSED/DISABLED and RUNNING/idle; tests in `test_schedule_apply.py` | — |
| ND-03 | **Shipped (branch)** | YAML sweep weekly = `SWEEP_CADENCE`; `apply` refuses an enabled task's cadence change without `--force`; contract test | `schedule apply` on the host (4 changes: reinstall 2, install 2) |
| ND-04 | **Sample drawn; labels owner-gated** | `eval sample`: 150 of 115,199, seed 20261031, 89 strata; `eval extract` wired into CI | Label 150 rows (50 doubled); publish κ/P/R/F1/fabrication |
| ND-05 | **Owner-gated** | `JesterNightlyRetention` declared weekly SUN 03:30, **`enabled: false`** | Apply only after ND-04's sample is labelled; then flip `enabled: true` |
| ND-06 | **Owner-gated** | `JesterNightlyBackup` declared daily 02:30, enabled; one backup taken and verified | `schedule apply`; restore drill on a copy |
| ND-07 | **Shipped (branch)** | `doctor --no-migrate` is byte-identical and creates no tables; prints the mode used | — |
| ND-08 | **Not started** | `.github/workflows/live.yml` authored, never dispatched | One dispatch, fix what breaks, weekly cron |
| ND-09 | **Partly done** | `pytest` out of runtime deps; CI `--cov-fail-under` at the measured baseline − 2 | Branch protection + required checks; Dependabot triage |
| ND-10 | **Shipped (branch)** | This page | Keep it current |
| ND-11 | **CHANGELOG written; tag owner-gated** | [CHANGELOG.md](../CHANGELOG.md) covers both versions | Tag `v0.1.0` after this branch is committed and verified |
| ND-12 | **Not started** | — | Exercise chat and journal once on fixtures, or demote them in the README |
| ND-13 | **Shipped (branch)** | [model-providers.md](model-providers.md) | Re-check whenever `roles:` changes |
| ND-14 | **Not this cycle** | — | Split god modules when real work touches them |

## S register (summary)

Full detail and tests are in [§15 of the 2026-10-31 report](reports/2026-10-31-next-development-strategy.md).
Still open there:

| ID | Status | Remaining |
|---|---|---|
| S-05 | Owner-gated | Same action as ND-01: backup → migrate → drain rescore |
| S-06 | Harness shipped; labels owner-gated | Same as ND-04 |
| S-07 | Owner-gated | Signals scope sign-off (`status`, `decided_by`, `decided_on`) |
| S-12 | Authored, not run | Same as ND-08 |
| S-14 | Not started | 30 blinded pairs, 3 raters, Spearman ≥ 0.7 |
| S-15 | Not started | 100-record recall sample with a 95% CI |
| S-16 | Shipped, green on GitHub | Make Windows a required check (ND-09) |
| S-19 | Owner-gated | Same as ND-11 |

S-01…S-04, S-08…S-11, S-13, S-17, S-18 and S-20 are **shipped**; S-20 gains
the sweep reconciliation, backup and retention declarations in this cycle.

## Verification

What was actually run, and where the output lives, is recorded per cycle in
the strategy report appendices. The current cycle's evidence is
[Appendix C of the 2026-11-01 report](reports/2026-11-01-next-development-strategy-v2.md#appendix-c--implementation-and-end-to-end-verification-2026-11-01-working-tree-on-36d357b);
the previous cycle's is [Appendix A of the 2026-10-31 report](reports/2026-10-31-next-development-strategy.md).

A status is not "done" because the code exists. It is done when a named
command in the register has been run and its output pasted somewhere a
reader can open.
