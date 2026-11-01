# Status

The one place to answer "is it done?". Every register in `docs/` maps an item
ID to its evidence; this page says which of them is current and where the
remaining work sits. **If this page and another document disagree about a
status, this page wins** — fix the other document in the same change.

**As of 2026-11-01.** Code status is against `main` at `d2da8de`, which is
tagged `v0.1.0` (annotated tag object `45d755c`). Push CI `41305838637` is
green in all six checks, `SonarCloud Code Analysis` reports success on the
same commit, and `main` now requires the four PR-visible checks (C.8). Earlier
in the day the Sonar gate cleared at `a8be9c0` (`41300116727`), the scheduled
live lane's first run failed one test at `acd424e` (`41300973142`), and the
dispatched lane `41302659108` passed 11 live tests with none skipped after the
fix (C.7). Four frontend dependency PRs were merged; three are held with
reasons (C.8). The live archive was **not** modified by this cycle.

Status words:

- **Shipped** — code, tests and docs are on `main` and the named suite passes.
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
| ND-02 | **Shipped** | `schedule status` prints PAUSED/DISABLED and RUNNING/idle; tests in `test_schedule_apply.py` | — |
| ND-03 | **Shipped** | YAML sweep weekly = `SWEEP_CADENCE`; `apply` refuses an enabled task's cadence change without `--force`; contract test | `schedule apply` on the host (4 changes: reinstall 2, install 2) |
| ND-04 | **Sample drawn; labels owner-gated** | `eval sample`: 150 of 115,199, seed 20261031, 89 strata; `eval extract` wired into CI | Label 150 rows (50 doubled); publish κ/P/R/F1/fabrication |
| ND-05 | **Owner-gated** | `JesterNightlyRetention` declared weekly SUN 03:30, **`enabled: false`** | Apply only after ND-04's sample is labelled; then flip `enabled: true` |
| ND-06 | **Owner-gated** | `JesterNightlyBackup` declared daily 02:30, enabled; one backup taken and verified | `schedule apply`; restore drill on a copy |
| ND-07 | **Shipped** | `doctor --no-migrate` is byte-identical and creates no tables; prints the mode used | — |
| ND-08 | **Shipped** | The lane's first scheduled run (`41300973142`) found a test that had never passed; fixed in `0697e1d`, dispatched lane `41302659108` green (11 passed, 0 skipped), weekly cron live | — |
| ND-09 | **Shipped** | `pytest` out of runtime deps; `--cov-fail-under=86`; `main` requires Python, Go, Frontend and Sonar (CI scanner); 4 of 7 Dependabot PRs merged | Re-check the three held PRs (C.8); Windows and the live lane are not PR-visible, so they cannot be required |
| ND-10 | **Shipped** | This page | Keep it current |
| ND-11 | **Shipped** | [CHANGELOG.md](../CHANGELOG.md) is one `0.1.0` entry dated 2026-11-01; `v0.1.0` tags `d2da8de` | — |
| ND-12 | **Not started** | — | Exercise chat and journal once on fixtures, or demote them in the README |
| ND-13 | **Shipped** | [model-providers.md](model-providers.md) | Re-check whenever `roles:` changes |
| ND-14 | **Not this cycle** | — | Split god modules when real work touches them |

## S register (summary)

Full detail and tests are in [§15 of the 2026-10-31 report](reports/2026-10-31-next-development-strategy.md).
Still open there:

| ID | Status | Remaining |
|---|---|---|
| S-05 | Owner-gated | Same action as ND-01: backup → migrate → drain rescore |
| S-06 | Harness shipped; labels owner-gated | Same as ND-04 |
| S-07 | Owner-gated | Signals scope sign-off (`status`, `decided_by`, `decided_on`) |
| S-12 | Shipped, green on GitHub | Same as ND-08 |
| S-14 | Not started | 30 blinded pairs, 3 raters, Spearman ≥ 0.7 |
| S-15 | Not started | 100-record recall sample with a 95% CI |
| S-16 | Shipped, green on GitHub | Windows is not a required check: it did not run on the PRs open when protection was enabled |
| S-19 | Shipped | Same as ND-11: `v0.1.0` pushed at `d2da8de` |

S-01…S-04, S-08…S-11, S-13, S-17, S-18 and S-20 are **shipped**; S-20 gains
the sweep reconciliation, backup and retention declarations in this cycle.

## Verification

What was actually run, and where the output lives, is recorded per cycle in
the strategy report appendices. The current cycle's evidence is
[Appendix C of the 2026-11-01 report](reports/2026-11-01-next-development-strategy-v2.md#appendix-c--implementation-and-end-to-end-verification-2026-11-01-the-tree-pushed-as-a8be9c0-plus-the-live-lane-fix-in-c7),
whose C.7 covers the live lane and whose C.8 covers the tag, the dependency
triage and branch protection;
the previous cycle's is [Appendix A of the 2026-10-31 report](reports/2026-10-31-next-development-strategy.md).

A status is not "done" because the code exists. It is done when a named
command in the register has been run and its output pasted somewhere a
reader can open.
