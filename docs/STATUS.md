# Status

The one place to answer "is it done?". Every register in `docs/` maps an item
ID to its evidence; this page says which of them is current and where the
remaining work sits. **If this page and another document disagree about a
status, this page wins** — fix the other document in the same change.

> **`main` at `1f774a2`.** Everything implementable without the owner's
> credentials is merged: PR #85 landed the lock-parity fix (`b01349e`, in the
> merge `baafcb7`), PR #86 merged the 2026-11-02 v4 work — batches B1–B5 of
> [the report](reports/2026-11-02-next-development-strategy.md) — as a merge
> commit (`a2c6104`) that preserves every wave commit the register cites,
> and PR #87 parameterized the signals schema probe's pragma read (fix
> `324bbb4`, merge `1f774a2`), closing the one finding that SonarCloud's
> quality-gate check held red. The live archive was **not** modified by any
> of this work: no command here opened `data/jester.db` for writing.
> [The R4 register](#r4-register-2026-11-02-report) below maps every item to
> its wave and its remaining owner half.

**As of 2026-11-03.** PR #85's merge run (`41623777580`), PR #86's merge
run (`41625000419`) and PR #87's merge run (`41640812411`) are green on
`main`; PR #86 (`41623876275`) and PR #87 (`41639855372`) passed all of
their checks on the first CI pass — Python, Go, Frontend, Sonar, and the
Windows and Compose jobs that branch protection does not require yet.
PR #87 also returned SonarCloud's quality-gate check to green: main's
analysis now reads `new_vulnerabilities` 0 and new-code Security Rating
A, where the failing condition had been exactly one medium-impact
`go:S2077` finding. `main` is green and holds everything except the owner
lane: the protection decisions, the extraction/agreement labels, the
Windows task account + `schedule apply`, WAL and the second backup volume.

**As of 2026-11-04.** The drift-ledger wave (Phase A) is code-complete on
`feat/run-outcome-plane` — all suites green, and SonarCloud's new-code
coverage gate repaired at `75687a2` (68.2% on the first CI run, raised on
the same branch; [PR #88](https://github.com/yosrikhiari/Jester/pull/88)'s
latest checks are the authority) — and is **not merged yet**: the owner
authorized push + PR + merge when green, and the merge lands only with
every required context green (the wave section below carries the evidence
and the honesty notes). The live archive was again not touched: no
command here opened `data/jester.db` for writing.

**Extraction quality is NOT MEASURED (stated 2026-11-02).** No
`eval/extraction/labels.jsonl` is committed, so `jester eval extract` exits 0
with `measured: false` and a NOT MEASURED block — the CI step called
"extraction eval gate" is a state report, not a gate, and no extraction number
(precision, recall, F1, fabrication rate, kappa) may be quoted from it. The
same holds for `jester eval agreement` and `eval/scoring/agreement.jsonl`.
The owner action is the drawn 150-row sample
(`eval/extraction/sample.jsonl`, 50 rows double-labelled): when labels are
committed, the published floors (F1 0.70, fabrication 0.05, kappa 0.6) become
a real gate and the first measured number can be printed with its denominator.

Status words:

- **Merged** — the V3 cycle's code, tests and docs are on `main` via PR #79
  (squash `7005078`, 2026-11-02); the evidence is that cycle's green CI run
  (`41508062030` at `94ba3f2`), and no merged row was re-proven by a
  dedicated post-merge run.
- **Shipped** — code, tests and docs are on `main` and the named suite passes.
- **Owner-gated** — the code side is ready; the next step needs your machine,
  your data, your credentials, your labels, or your judgement.
- **Not started** — no work done.
- **Not this cycle** — deliberately deferred.

## V3 hardening cycle (merged on main)

The register is the 2026-11-01 V3 strategy report (`V3-01` … `V3-26`), which is
still not in this tree; the per-row evidence is the task briefs and reports
under `.superpowers/sdd/v3-hardening/`, and the code is on `main` via PR #79
(squash `7005078`). The word is **Merged**, defined in the list above; row
extras say what each merged row still owes:

- **Merged, owner half named** — the mechanism is merged; the remaining half
  needs the owner's machine, data, labels or judgement.
- **Ruling** — decided that cycle, with no code change.
- **Open** — not done (or, for V3-03, fixed only on the newer branch).

| ID | Task | What is merged | Status |
|---|---|---|---|
| V3-01 | T1 | Idea claim and insert in one `BEGIN IMMEDIATE`; a deterministic `ideas.support_key` plus index stops a second idea on the same evidence; fault-injection tests | Merged |
| V3-02 | T1 | A corrupt `blocked_until` fails closed with a named window; quota spend is one conditional statement whose rowcount is the verdict; the rescore queue takes a per-idea claim | Merged |
| V3-03 | — | On `main`, nothing: history still returns the oldest 24 messages. | **Open**; fixed in code on `v4-strategy-batches` (R4-11: newest-24) |
| V3-04 | T3 | `signals_source_state` streak table; `jester health --days 7` exits 1 on a barren, stale or erroring source; the launcher turns a non-zero exit into a notify | Merged |
| V3-05 | T5 | An approval records a manifest over revision, figures and saved copies, and export and publish recompute it; `obs:<label>\|<reason>`; headings and inline code are prose | Merged |
| V3-06 | T4 | CSV export is atomic and reconciled by id and content hash; ClickHouse schema v2 with additive `ALTER`; `signals retention --days 365`, declared paused | Merged |
| V3-07 | T2 | A malformed or refused reply is filed `ok=0`; the rescore path files `llm_calls`; archivist failures are counted and logged once per run | Merged |
| V3-08 | T6 | No DDL on read paths; `jester doctor` adds an advisory WAL note and still never changes the mode | Merged, owner half named: enable WAL on the live archive after the benchmark-backed decision |
| V3-09 | T2 | One retry policy (socket, DNS, TLS and 5xx alike) with one deadline per call | Merged |
| V3-10 | T6 | Unknown top-level config keys are refused; a `roles:` block must name a model for each role it lists | Merged |
| V3-11 | T6 | Dependabot gains `gomod`; `govulncheck` and `npm audit --audit-level=high` are blocking CI steps | Merged (the cycle's PR run `41508062030` is green) |
| V3-12 | T6 | 1 MiB body cap answered 413, Host allow-list answered 403, 30 s per-connection timeout | Merged |
| V3-13 | T8 | κ is a gate at 0.6 with its denominator printed; `jester eval agreement` reports per-axis MAE with denominators and `--min 30` | Merged, owner half named: label the 150 extraction rows and the 30 scorer-agreement ideas |
| V3-14 | T7 | Every mutable signal field is refreshed; a correction keeps its replaced row; `problem_signal_read` makes an HN removal visible instead of guessed | Merged |
| V3-15 | T5 | Approvals expire after 30 days; `journal snapshots --verify` re-checks stored copies and only re-fetches with `--network`; a retracted article leaves the index, feed and export | Merged |
| V3-16 | — | Nothing extracted. | **Ruling** (below) |
| V3-17 | T2 | Crash recording is scoped to the run the process started; a failing subcommand no longer brands a live run as errored | Merged; the register's other half (moving the decoy databases) is not done |
| V3-18 | T3 | The Windows launcher and the POSIX cron line run `jester notify` only on a non-zero exit and keep the run's code | Merged |
| V3-19 | T9 | README names SQLite the source of truth and ClickHouse an optional manual mirror; `JesterClickHouseLoad` is declared after the brief, paused | Merged |
| V3-20 | — | No frontend change. | **Ruling** (below) |
| V3-21 | T9 | `docs/plans/archive/README.md` states the archive rule and records that no plan qualifies; `python/tests/test_doc_links.py` checks every relative path in the tracked `.md` files; one broken link repaired | Merged |
| V3-22 | T1 | `GET /api/config` blanks any secret-named value and returns a `secrets_set` map; the UI reads that map | Merged |
| V3-23 | T7 | Backup covers `journal.db`, `chat.db` and `.env`, verifies the snapshot, restores into a new file (`backup restore`), supports `--also-to`, and a monthly drill | Merged, owner half named: choose and provide the second volume |
| V3-24 | T6 | `scripts/check_lock_parity.py` runs in both Python CI jobs and fails on unpinned, missing or older-than-floor requirements | Merged, partial: Windows still installs `requirements.txt`, and `pyproject.toml` is not read |
| V3-25 | T7 | The console image runs as uid 10001 with cron removed; the built image reports `uid=10001(jester)`, no `cron`, and a writable `/app/data` | Merged; the compose stack was not started by that cycle (no second writer against a live archive) |
| V3-26 | T7 | `/rl LIMITED` on every task, `--run-as` for an account, 5 MB x 3 log rotation, launcher format marker v2 | Merged, owner half named: choose the Windows task account before `schedule apply` |

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

In the order the R4 register (and then the merged V3 leftovers) requires:

- Make the Windows job a required context and set `strict=true` (deciding
  `enforce_admins`); the lock-parity fix (PR #85) and the 2026-11-02 batches
  (PR #86, merge `a2c6104`) are already on `main` (R4-01).
- Label the 150 extraction rows (50 doubled) and the 30 scorer-agreement
  ideas; the gates are wired and print NOT MEASURED until the labels exist
  (R4-06).
- `schedule apply --dry-run` and then apply, choosing the Windows task
  account before `apply` (R4-02's host half and R4-12; closes V3-26's owner
  half).
- Enable WAL on the live archive after the benchmark-backed decision, on
  local disk only (V3-08 / R4-12).
- Choose and provide a second backup volume for `backup --also-to`, then
  take the restore drill on a copy (R4-07; closes V3-23's owner half).
- Re-approve any live journal approval once after merge: approvals recorded
  before the manifest binding are refused until taken again.
- Flip `JesterNightlyRetention`, `JesterSignalsRetention`,
  `JesterNightlyBackupDrill` and `JesterClickHouseLoad` only by owner
  decision; each is declared and disabled for a stated reason.
- Then the R4-12 lane: migrate and drain the rescore queue in bounded
  windows, and read one week of scheduled runs (`jester health`,
  `jester evidence`, zero `SQLITE_BUSY`, one restore drill).

## R4 register (2026-11-02 report)

The register is §9 of [the 2026-11-02 strategy report](reports/2026-11-02-next-development-strategy.md):
items `R4-01` … `R4-12`, batched B1–B5 and executed as waves W1–W5 (W1 = B1,
…, W5 = B5) on `v4-strategy-batches`, merged to `main` as PR #86 (merge
commit `a2c6104`, wave commits preserved). The first two columns come from
the tree's commit history; the last column is the owner lane.

| ID | Wave (commits) | What exists on `v4-strategy-batches` | What remains (owner unless stated) |
|---|---|---|---|
| R4-01 | W1 (`9f89e95`, `0e60328`, `a853a64`) | The parity failure prints the exact regeneration command (`9f89e95`); `docs/` states that a pip bump must pair with a regenerated lock (`0e60328`); both survived the W1 review fixes (`a853a64`); PR #85 (`b01349e`) is merged | Windows required context; `strict=true` with the `enforce_admins` decision; choose the lock-refresh shape (a `lock-refresh` workflow, or a paired Dependabot policy) and prove a seeded pip PR is refused or auto-fixed |
| R4-02 | W1 (`1fb7afc`, `10f5263`, `2af9574`, `a853a64`) | Archive roles and a per-task DB override: every scheduled task names the archive it writes; CLI defaults `signals`/`health` to the signals archive; `signals_source_state` rename with a re-runnable additive migration; `contractcheck -signals` | `schedule apply --dry-run`, then apply on the host (with R4-12) |
| R4-03 | W2 (`8524c94`, `b4a8dea`, `d6b4e69`, `378f87f`) | POST requires a token by default; non-loopback bindings refused without one; export `out_dir` contained to the exports root; `JESTER_QDRANT_URL` redacted to set/not-set | Provide the generated token to the compose stack's environment (a boot without one stays read-only by design) |
| R4-04 | W2 (`c54d553`) | `config-live/` is a CI-checked overlay: identical non-model key sets asserted, an explicit intentional-defaults allowlist, and the promotion doc corrected; `update_thresholds` keeps writing only the patched keys (ruling) | — |
| R4-05 | W3 (`cd46208`, `b56378e`) | Both shipped images run uid 10001, `user:` pins it in compose, `restart: unless-stopped` on console and worker; CI builds the images, starts the stack and asserts the worker row is readable | The one-time chown for a volume created by root-era images (documented in the quickstart) |
| R4-06 | W4 (`c987052`) | `eval extract` and `eval agreement` print the NOT MEASURED block with `measured: false` and exit 0; STATUS carries the standing paragraph below | Commit the labels, then publish the first measured numbers |
| R4-07 | W4 (`7c2b578`, `b473d8b`) | A failed second copy is reported as a partial set (`failed:` listed) instead of losing the first; backup args are declared; `.env` is kept by default with the ACL requirement documented and `--exclude-secrets` as the opt-out | Provide the second volume path, add it to the scheduled backup, `schedule apply`, and take the restore drill on a copy |
| R4-08 | W5 (`faa344f`, `5236fab`, and this docs commit) | STATUS describes the tree again (this page); quickstart is OS-split with `jester doctor` as step 0 and per-OS paths; one Python floor (3.11) and one Go floor (1.26) — `pyproject.toml`, README, `go.mod`, `ci.yml` agree; the report itself is in the tree | — |
| R4-09 | W5 (`f64ec08`) | The export tree is built in jester-owned staging, the manifest lands last through temp-file + replace, and the swap retires the previous export (restoring it if the replace fails); a kill leaves no visible export; `prune_exports` removes jester-named leftovers and never an unmanifested dir it did not write | — |
| R4-10 | W3 (`b56378e`) | `permissions:` on both workflows (least-privilege); the frontend base image is digest-pinned | `pip-audit` stays advisory; making it blocking needs the triage decision |
| R4-11 | W5 (`4b1e830`) | `ChatStore.messages` returns the newest N chronologically; the window applies at the store; a 30-turn test asserts seqs 7..30 plus the current turn (V3-03 closed in code) | Enabling chat (`chat_enabled`) is an owner decision |
| R4-12 | — | The ordered owner lane is defined in the report's own row; no host-side step ran here (hard constraint of this work) | Fix R4-02's apply dry-run; account; `schedule apply`; WAL; migrate and drain bounded windows; one backup; journal re-approval; one week of scheduled runs; `jester health` and `jester evidence` |

### Verification on `v4-strategy-batches`

| Command | Where | Result |
|---|---|---|
| `python -m pytest python/tests -q` (with `PYTHONPATH=python`, `JESTER_MOCK=1`, `TMP`/`TEMP` set) | B1–B4 complete, tree at `b473d8b` | `2303 passed, 28 skipped, 13 deselected, 41 warnings in 561.06s` |
| the same command | B5 complete, tree at `5236fab` | `2315 passed, 28 skipped, 13 deselected, 41 warnings in 535.22s (0:08:55)` |
| `python scripts/counts.py --no-archive` | this commit | `python tests: 2343` / `go test functions: 306` |
| focused suites re-run at B5 | chat files (57), export (49), doc contracts + doc links (9) | all green |

## Drift-ledger wave (Phase A of the Scrapling-informed roadmap) — code complete on branch

Phase A was chosen from [the 2026-11-03 Scrapling research
report](../.superpowers/sdd/2026-11-03-scrapling-informed-enhancement/report.md)
by the owner (question tool, 2026-11-03) and executed on
`feat/run-outcome-plane`, cut off `main` tip `15032e4`. Its design register
lives at
[the wave ledger](../.superpowers/sdd/2026-11-03-drift-ledger/progress.md);
the wave is **NOT merged** — pushing, the PR and the merge (Dependabot PRs
#80/#81 stay held) require fresh owner authorization, at which time this
section is updated with the merge evidence.

What exists on the branch, with its commit:

| Commit | Layer | What it does |
|---|---|---|
| `683eb70` | `go/internal/siteprofile` | `Parse(body) ([]Listing, ParseStats, error)` — anchors, tiles, blocks, bad_json, candidates, kept, filtered, no_id, no_url, and a per-field coverage histogram counted over the KEPT rows (canonical fields read off the struct, so `price=0` stays a non-price) |
| `c1ce3ac` | `go/internal/realestate` | `Stats` (pages ok/err, bytes, fetch-clock latency, fetch mode) threaded out of `FetchListingList`; offline httptest test drives clean-page and 403-portal walks |
| `d2813a1` | `go/internal/store` | `run_outcome` table + source index via `CREATE TABLE IF NOT EXISTS` — **no schema_version bump** (the guard stays 1, a strict cross-language contract); one row per (run_id, source), `INSERT OR REPLACE`, kept for failing visits |
| `d3d53d7` | `go/cmd/worker` | `runLive` writes one row per visited source BEFORE the failure short-circuit; a CDP-session failure writes its row; realestate rows carry fresh/dup splits and the walk stats JSON; disabled sources write none |
| `178162e` | Python console | `jester/runout.py` (series + drift rules), `/api/outcomes`, Sources-page trend column with alarm chips and a `drifting` filter |
| `75687a2` | Go, gate repair | SonarCloud's new-code coverage gate read 68.2% (floor 80): the worker's failure-mode wiring had no tests. `store.RunOutcomes(runID)` reader; runLive failure-mode rows pinned offline (CDP refused on loopback, unknown adapter); an httptest realestate walk drives the fresh/dup split across two runs; siteprofile's per-mode drop counters got one fixture per exit — which found and fixed a real bug (ldjson mode skipped its NoURL increment); `ParseStats.Add` merge invariants and the `recordFields` nil-start pinned |

The console's drift rules carry their limits where they live, and the tests
pin them: `shape-gone` needs clean pages AND a prior yield (anchor silence
ALONE never judges an anchored profile — the anchor is the only way that mode
sees tiles at all); `coverage-drop` compares against a pooled rolling baseline
with a floor (a field born thin stays quiet); `repeat-failure` is a streak of
three broken by any success, not a lifetime count; a never-yielded source is
unmeasured, not drifted.

What it does NOT do (the honesty notes):

- **No parse-behavior change and no adaptive substitution** — ever. The
  counters observe; they never relocate, re-save or "fix" a selector. The
  Scrapling relocate() idea is adopted strictly as its scoring axis.
- Coverage is counted over **returned rows only**; detail-page fills are not
  in the histogram (Parse returns before detail fetches).
- Community adapters carry `items` (and their failure class); page counters
  for them are zero and the console renders the parse story only where a
  realestate walk produced it.
- Latency wraps the fetch call (a bounded retry's backoff lands inside its
  own clock); pacing sleeps are excluded.
- Per-page `fetch_outcome` rows and detail counters are deferred, by scope
  decision, until the trend cards demand them.
- Mock mode writes no rows: the ledger is a live fact.
- `listingeval` remains manual — wiring it into scheduled runs is Phase B.

Verification run on the branch (each row is the tree at its named commit):

| Command | Where | Result |
|---|---|---|
| `go test ./... -count=1` | `go/`, after each B1 commit (`683eb70`, `c1ce3ac`, `d2813a1`, `d3d53d7`) | every package `ok` |
| `go vet ./...` | `go/`, tree at `75687a2` | no output (clean) |
| `go test ./... -count=1 -coverprofile` | `go/`, tree at `75687a2` | every package `ok` |
| `go test ./cmd/worker ./internal/siteprofile ./internal/store ./internal/realestate -cover` | `go/`, tree at `75687a2` | `28.2%` / `86.7%` / `78.0%` / `71.7%` of statements |
| `python -m pytest python/tests -q` (env above) | tree at `178162e` | `2325 passed, 28 skipped, 13 deselected, 41 warnings in 560.13s (0:09:20)` |
| `python -m pytest test_doc_links.py test_doc_contracts.py -q` | tree at this docs commit | `9 passed` |
| `python scripts/counts.py --no-archive` | this commit | `python tests: 2357` / `go test functions: 310` |

The coverage-gate repair (`75687a2`) changed no measured behavior except
one fix it produced along the way: ldjson's NoURL drop counter now
increments. The lines its tests still do not reach are the one-per-adapter
`return` statements (a live network each — hermetic tests stay out) and the
four browser-render lines in realestate's HTTP/browser fork.

## The products

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
| **V3-01 … V3-26** | 2026-11-01 V3 register, merged as `7005078` | [V3 hardening cycle](#v3-hardening-cycle-merged-on-main) above; the register report itself is still not in this tree |
| **R4-01 … R4-12** | 2026-11-02 strategy review, executed as batches B1–B5 | [the 2026-11-02 report §9](reports/2026-11-02-next-development-strategy.md); status in [the R4 register](#r4-register-2026-11-02-report) below |

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

The 2026-11-02 implementations cycle (R4 waves W1–W5) is verified per commit
and per wave: [the R4 register](#r4-register-2026-11-02-report) above carries
its full-suite and counts evidence, and the work landed on `main` via
PR #86 (`a2c6104`) after six green PR checks and the green merge-commit runs
the banner cites — the work itself is what
[the 2026-11-02 report](reports/2026-11-02-next-development-strategy.md)
specified.
