# Status

The one place to answer "is it done?". Every register in `docs/` maps an item
ID to its evidence; this page says which of them is current and where the
remaining work sits. **If this page and another document disagree about a
status, this page wins** — fix the other document in the same change.

> **Branch `v3-hardening` (base `5a4bd36`).** The V3 hardening cycle lives on
> the branch `v3-hardening`, which is **not pushed and not merged**. `main` is
> `5a4bd36` — one docs-only commit after the `v0.1.0` tag (`d2da8de`);
> nothing in [V3 hardening cycle](#v3-hardening-cycle-branch) below is a claim
> about `main`, and it becomes one only after review and merge. The live
> archive was **not** modified by this work: no command here opened
> `data/jester.db` for writing.

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

## V3 hardening cycle (branch)

The register is the 2026-11-01 V3 strategy report (`V3-01` … `V3-26`), which is
not in this tree. The per-row evidence is the task brief and report under
`.superpowers/sdd/v3-hardening/`, and the code is on this branch. The status
words above are about `main`; the words below are about the branch only:

- **On branch** — code, tests and docs are on `v3-hardening`; the full Python
  suite is green at `69f2353` (this task's parent), and each focused suite
  passed at the commit named in its task report.
- **On branch, owner half named** — the mechanism exists; the remaining half
  needs the owner's machine, data, labels or judgement.
- **Ruling** — decided this cycle, with no code change.
- **Open** — not done on this branch.

| ID | Task | What is on the branch | Status |
|---|---|---|---|
| V3-01 | T1 | Idea claim and insert in one `BEGIN IMMEDIATE`; a deterministic `ideas.support_key` plus index stops a second idea on the same evidence; fault-injection tests | On branch |
| V3-02 | T1 | A corrupt `blocked_until` fails closed with a named window; quota spend is one conditional statement whose rowcount is the verdict; the rescore queue takes a per-idea claim | On branch |
| V3-03 | — | Nothing. Chat history still returns the oldest 24 messages. | **Open** (not in this plan) |
| V3-04 | T3 | `source_state` streak table; `jester health --days 7` exits 1 on a barren, stale or erroring source; the launcher turns a non-zero exit into a notify | On branch |
| V3-05 | T5 | An approval records a manifest over revision, figures and saved copies, and export and publish recompute it; `obs:<label>\|<reason>`; headings and inline code are prose | On branch |
| V3-06 | T4 | CSV export is atomic and reconciled by id and content hash; ClickHouse schema v2 with additive `ALTER`; `signals retention --days 365`, declared paused | On branch |
| V3-07 | T2 | A malformed or refused reply is filed `ok=0`; the rescore path files `llm_calls`; archivist failures are counted and logged once per run | On branch |
| V3-08 | T6 | No DDL on read paths; `jester doctor` adds an advisory WAL note and still never changes the mode | On branch, owner half named: enable WAL on the live archive after the benchmark-backed decision |
| V3-09 | T2 | One retry policy (socket, DNS, TLS and 5xx alike) with one deadline per call | On branch |
| V3-10 | T6 | Unknown top-level config keys are refused; a `roles:` block must name a model for each role it lists | On branch |
| V3-11 | T6 | Dependabot gains `gomod`; `govulncheck` and `npm audit --audit-level=high` are blocking CI steps | On branch; CI did not run on this branch |
| V3-12 | T6 | 1 MiB body cap answered 413, Host allow-list answered 403, 30 s per-connection timeout | On branch |
| V3-13 | T8 | κ is a gate at 0.6 with its denominator printed; `jester eval agreement` reports per-axis MAE with denominators and `--min 30` | On branch, owner half named: label the 150 extraction rows and the 30 scorer-agreement ideas |
| V3-14 | T7 | Every mutable signal field is refreshed; a correction keeps its replaced row; `problem_signal_read` makes an HN removal visible instead of guessed | On branch |
| V3-15 | T5 | Approvals expire after 30 days; `journal snapshots --verify` re-checks stored copies and only re-fetches with `--network`; a retracted article leaves the index, feed and export | On branch |
| V3-16 | — | Nothing extracted. | **Ruling** (below) |
| V3-17 | T2 | Crash recording is scoped to the run the process started; a failing subcommand no longer brands a live run as errored | On branch; the register's other half (moving the decoy databases) is not done |
| V3-18 | T3 | The Windows launcher and the POSIX cron line run `jester notify` only on a non-zero exit and keep the run's code | On branch |
| V3-19 | T9 | README names SQLite the source of truth and ClickHouse an optional manual mirror; `JesterClickHouseLoad` is declared after the brief, paused | On branch |
| V3-20 | — | No frontend change. | **Ruling** (below) |
| V3-21 | T9 | `docs/plans/archive/README.md` states the archive rule and records that no plan qualifies; `python/tests/test_doc_links.py` checks every relative path in the tracked `.md` files; one broken link repaired | On branch |
| V3-22 | T1 | `GET /api/config` blanks any secret-named value and returns a `secrets_set` map; the UI reads that map | On branch |
| V3-23 | T7 | Backup covers `journal.db`, `chat.db` and `.env`, verifies the snapshot, restores into a new file (`backup restore`), supports `--also-to`, and a monthly drill | On branch, owner half named: choose and provide the second volume |
| V3-24 | T6 | `scripts/check_lock_parity.py` runs in both Python CI jobs and fails on unpinned, missing or older-than-floor requirements | On branch, partial: Windows still installs `requirements.txt`, and `pyproject.toml` is not read |
| V3-25 | T7 | The console image runs as uid 10001 with cron removed; the built image reports `uid=10001(jester)`, no `cron`, and a writable `/app/data` | On branch; the compose stack was not started (no second writer against a live archive) |
| V3-26 | T7 | `/rl LIMITED` on every task, `--run-as` for an account, 5 MB x 3 log rotation, launcher format marker v2 | On branch, owner half named: choose the Windows task account before `schedule apply` |

### Rulings

- **V3-16 — no wholesale extraction of `cli.py` or `ConsoleAPI` this cycle.**
  The register's own solution says "only when a change touches them", and this
  branch did touch them, surgically and without moving an interface: the
  evaluation commands went into `cli_review.py`'s existing dispatch, and every
  other edit is inside a function. Global constraint 4 of the plan forbids
  wholesale refactors, so a move-only extraction would have been the one change
  in the cycle with no test that could fail. Cost if this is wrong: the two god
  modules stay as they are, and the next change that touches them carries the
  same review cost it carried here.
- **V3-20 — no frontend change.** `docs/adr/0001-frontend-strategy.md` stands:
  the console served by Python at `:8619` is the shipped UI and `frontend/` is
  a scaffold behind promotion criteria nobody has met. The cycle changed the
  console's payloads (redacted config, `format_only` drift) and its limits, not
  its identity.

### Verification evidence

Only what was run on this branch is listed, with the commit it was run against.
The branch was pushed for PR #79, so the CI run that gates the merge is cited
beside the local runs.

| Command | Where | Result |
|---|---|---|
| `python -m pytest python/tests -q` (with `PYTHONPATH=python JESTER_MOCK=1`, `TMP`/`TEMP` set) | tree committed as `69f2353` | `2220 passed, 12 skipped, 13 deselected, 41 warnings in 463.58s (0:07:43)` |
| the same command | tree committed as `d4ecc28` (four prose-only edits followed the Task 9 run; `test_doc_links` and `test_schedule_apply` were re-run green) | `2225 passed, 12 skipped, 13 deselected, 41 warnings in 466.21s (0:07:46)` |
| the same command | tree of the final fix commit (the whole-branch review's two Importants and the re-review's notes; two tests added, three edited; this row is the only edit made after the run, and `test_doc_links` was re-run green) | `2227 passed, 12 skipped, 13 deselected, 41 warnings in 480.63s (0:08:00)` |
| the same command | tree of the CI fix commit `94ba3f2` (two platform-fragile assertions corrected: sorted directory names, a 50 ms tolerance on the whole-call budget timeout; no test count change) | `2227 passed, 12 skipped, 13 deselected, 41 warnings in 508.10s (0:08:28)` |
| GitHub Actions CI run `41508062030` (Python job) | `94ba3f2` | `2225 passed, 14 skipped, 13 deselected, 41 warnings in 322.00s (0:05:22)`; coverage 89.85% (gate 86%); the Go, Frontend, Windows and Sonar checks also succeeded |
| `python scripts/counts.py --no-archive` | this commit | `python tests: 2239` / `go test functions: 299` |
| `go vet ./...` | `go/`, this commit | no output (clean); no Go file changed after `5f66917` |
| `go test ./...` | `go/`, this commit | every package `ok`; the `cmd/*` probe packages have no test files |
| `python scripts/check_lock_parity.py` | this commit | `lock parity: 6 direct requirement(s) agree with 30 locked package(s)` |
| the focused suites named in the task reports | throughout the cycle | `test_idea_atomic_write`, `test_signals_health`, `test_signals_archive`, `test_journal_publish`, `test_journal_checks`, `test_config_strictness`, `test_console_server`, `test_dependency_gates`, `test_backup_restore`, `test_container_user`, `test_schedule_apply`, `test_doctor`, `test_scoring_eval`, `test_extraction_eval`, `test_doc_links` |

CI's frontend job (lint and build) ran at `94ba3f2` and succeeded. Locally the
frontend gates were not re-run for this task: `node_modules` is absent in this
worktree and Task 9 changed no frontend file. The only local frontend gate
output on the branch is Task 6's `npm audit` at `ffcb072` (0 vulnerabilities),
recorded in that task's report.

### Owner actions outstanding

- Label the 150 extraction rows (50 doubled) and the 30 scorer-agreement ideas;
  the gates are wired and cannot report until the labels exist.
- Enable WAL on the live archive after the benchmark-backed decision, on local
  disk only.
- Choose and provide a second backup volume; `backup --also-to` needs a path.
- Choose the Windows task account before `jester schedule apply`, then apply.
- Re-approve any live journal approval once after merge: approvals recorded
  before the manifest binding are refused until taken again.
- Flip `JesterNightlyRetention`, `JesterSignalsRetention`,
  `JesterNightlyBackupDrill` and `JesterClickHouseLoad` only by owner decision;
  each is declared and disabled for a stated reason.

### Known limits carried from this branch

All of these were parked in the task ledger; none is a surprise.

- A launcher written by a *newer* format and then rolled back is reported as
  "older format" after the downgrade.
- A companion backup restored without its set manifest beside it gets neither a
  hash nor a counts check.
- `pyproject.toml` is a third manifest that `check_lock_parity.py` does not
  read, so a dev install can drift from the lock.
- `govulncheck@latest` is unpinned in CI; its first-run baseline is unproven.
- The `eval agreement` CI step is deferred until scored rows exist.
- Retraction removes an article from the index, feed and export, but an
  already-exported page stays on disk until the next export.
- A stale `running` job row is reaped on the next job or scheduler start, not
  when it goes stale.

## The products

| Product | Status | Evidence |
|---|---|---|
| Idea pipeline | **Paused since 2026-10-02.** Three scheduled tasks registered and disabled; last exit 0 means "switched off", not "succeeded". | [README §Operating state](../README.md#operating-state); `jester schedule status` |
| Problem signals | **Live**, six scheduled tasks; fixture gate green 2026-10-31. | `.github/workflows/ci.yml` (`signals check`); `config/schedule.yaml` |
| Journal | **Built through J3; 0 publications.** | [README §Journal](../README.md#journal--research-experiment-publish-in-progress); `data/journal.db` |
| Console chat | **Built; 0 sessions.** `chat_enabled: false`. | `python/jester/chat/`; `config/thresholds.yaml` |

## Registers, and which one to read

| Register | Covers | Authoritative section |
|---|---|---|
| **S-01 … S-24** | 2026-10-31 strategy items (reliability, evaluation, security, ops) | [2026-10-31 report §9](reports/2026-10-31-next-development-strategy.md), status in [§15](reports/2026-10-31-next-development-strategy.md) |
| **ND-01 … ND-14** | 2026-11-01 follow-up items | [2026-11-01 report §2.2](reports/2026-11-01-next-development-strategy-v2.md); status below |
| **F-01 … F-17** | 2026-10-29 codebase findings | [Master roadmap §1.5](plans/2026-10-29-project-assessment-and-master-roadmap.md); F-04/F-07/F-12/F-13/F-15 remain known issues |
| **DD-M4/M5/M6, J0–J3** | Dedup and journal slices | [2026-10-29 final report](reports/2026-10-29-final-implementation-report.md) |
| **V3-01 … V3-26** | 2026-11-01 V3 register, on branch `v3-hardening` | [V3 hardening cycle](#v3-hardening-cycle-branch) above; the register report itself is not in this tree |

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
