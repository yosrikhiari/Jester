# Wave 2026-11-03 — Run-outcome telemetry plane ("drift ledger") — Phase A

Authorization: owner chose **"Phase A: Drift ledger"** via question tool on 2026-11-03, following the
research-only deliverable `../2026-11-03-scrapling-informed-enhancement/report.md` (P0 opportunity #1).

Tree: worktree `D:\project in this pc\Jester\jester-v3-hardening`, branch `feat/run-outcome-plane`
cut off `v4-strategy-batches` = `origin/main` = `15032e4`. `.superpowers/` is untracked but NOT
gitignored → wave ledger and reports are committable material (B3 commit).

## Goal

Every scheduled worker run leaves one outcome row per visited source — pages ok/err, rows kept,
anchor/tile/block counters, per-field coverage, bytes, latency, fetch mode, failure class — persisted
additively; the console renders per-source trend cards with drift alarms. **No parse-behavior change;
no adaptive substitution, ever** (`siteprofile.go:748-754` "wrong data that looks right").

## Design decisions (each with its reason)

1. Counter-only telemetry, borrowed from the *scoring axis* of Scrapling's relocate() — never its
   substitution behavior. All counters are integer/JSON, additive schema, quiet-by-construction.
2. `siteprofile.Parse` becomes `Parse(body) ([]store.Listing, ParseStats, error)` — the honest
   signature; internal modes (ldjson/nextdata/anchored) each return their counters.
3. Per-field coverage is counted over **returned rows only**; denominator = kept rows ×-profile:
   `listing_id`/`url`/`price`/`currency`/`status` read off the Listing struct so semantics are the
   archive's (price=0 counts as NOT covered — "zero is not a price" law, siteprofile.go:886-897);
   other profile fields read off the row's payload JSON, which retains exactly what `assign` put in.
   Detail-page fills are NOT in the histogram (Parse returns before detail) — documented, deferred.
4. `run_outcome` is **Go-owned**: `CREATE TABLE IF NOT EXISTS` inside Go's schema SQL; **no
   schema_version bump** (meta guard stays 1 — strict cross-language equality, `contractcheck`);
   Python console opens `migrate=False` and only READS the table; a pre-wave database gets the table
   on the next Go open; a console reading an old file without the table says so honestly.
5. One row per (run_id, source), `INSERT OR REPLACE` (last visit wins), written for clean visits AND
   failures (`first_err` carries why). Disabled sources write no row (they are not part of the run's
   truth); a CDP-session failure DOES (the operator must see who yielded nothing and why).
6. `fetch_outcome` per-page table DEFERRED: per-source rows satisfy this wave's acceptance criteria;
   per-page drill-down is a follow-up only if trend cards demand it. Same for detail-page counters
   (`FetchDetails` returns a summary string, not numbers today).
7. `fetchSource` now returns an `outcome` struct (items, fresh, dup, extended stats JSON) instead of
   bare int — per-branch yield truth for every community adapter; realestate branch splits
   fresh/dup/enqueued and embeds the realestate stats JSON. Mock mode never walks sources → no rows
   (outcome rows are a live fact; tests exercise the layers directly).
8. Latency measured around the fetch call only (pacing sleeps excluded); bytes = len(body).
   Community adapters have no per-page notion — their counters stay 0/0, which Python renders as "-".

## Batches & commits

- **B1 (Go)** — four commits, each compiling and suite-green:
  1. `feat: siteprofile parse counts what a page held` — ParseStats + modes + dispatcher + `recordFields`
     + all call sites (realestate.go:59, listingharvest:217, siteprofile tests) compile-fixed.
  2. `feat: realestate reports fetch and parse stats per run` — `realestate.Stats` threaded out of
     FetchListingList (pages/bytes/latency/fetch mode/merged ParseStats).
  3. `feat: store keeps a run outcome ledger` — run_outcome DDL+index (no version bump), RunOutcome
     struct + RecordRunOutcome (INSERT OR REPLACE), migration re-add test.
  4. `feat: worker writes the run outcome ledger` — outcome struct through fetchSource's branches,
     runLive row write incl. cdp-failure rows, realestate fresh/dup/extended facts.
- **B2 (Python/console)** — `python/jester/runout.py` (series + drift rules: anchor/tile silence,
  coverage drop vs rolling baseline, all-pages-failed), console api + sources trend card, tests with
  a seeded temp db; pytest + doc-tests.
- **B3 (docs/PR)** — STATUS.md truth row, "what it does not do" honesty notes, ledger close, then
  push + PR + merge **only with fresh owner authorization**.

## Acceptance (from report §3.1)

- Every scheduled run yields one row per source (verified by tests at each layer).
- A fixture regression where the anchor fails flips the signal: siteprofile test with a broken anchor
  (Anchors=0, Kept=0, Blocks/Tiles layout honest) + console drift rule at B2.
- `go test ./...` green; `pytest` green under `PYTHONPATH=python`, `JESTER_MOCK=1`,
  `TMP`/`TEMP=C:\Users\mano9\AppData\Local\Temp\opencode`.
- doc-contracts/doc-links green at docs time.

## Scope cut register (kept out, with why)

- Per-page `fetch_outcome` table (B2 drill-down if demanded).
- Detail-page fill counters (FetchDetails lacks numerics; string summary unchanged).
- Scrapling-style similarity/diff views, robots watcher, uTLS, listingeval wiring → later phases (B/C).
- Community-platform extended parse stats (their adapters are JSON walkers, not page parsers).

## B2 record (2026-11-04)

- Drift rule set (final): `all-pages-failed` (pages_err>0, pages_ok==0); `shape-gone`
  (no first_err AND yield==0 AND history yielded rows — the Anchors=0-alone limit enforced in
  the rule itself); `coverage-drop` (pooled baseline over history rows with stats, frac>=0.5
  floor, drop>=0.25); `repeat-failure` (failure streak >= 3, broken by any success).
- Trend card on Sources page: latest run's kept/items count, `err` line with first_err on hover,
  one `tone-failed` pill per alarm kind with its note on hover. `drifting` filter segment added
  to the existing status segments; page summary names the drifting sources (the alarms say what
  changed, not what to do).
- `/api/outcomes` = full series + drift per source; `sources()` composes compact cards and
  `drifting_sources` (listed sources only).
- Community rows (NULL stats) render via items alone; page counters show as the HTTP-mode story.
- Test pins: anchor-silence-no-judgement, never-yielded-is-unmeasured, flappy-streak-not-
  lifetime, born-thin-field-quiet, corrupt stats_json degrades, absent table = {} (14 tests).

## Progress log

- [x] Phase A authorized (owner, question tool 2026-11-03)
- [x] Integration points verified: Parse sites (2 prod + ~26 test), fetchSource returns, schema SQL
      + addColumns + isDuplicateColumn semantics, schema_version=1 strict guard both languages,
      runLive wiring point, realestate fresh/dup dedup plumbing
- [x] B1 commit 1 — siteprofile ParseStats (`683eb70`)
- [x] B1 commit 2 — realestate.Stats (`c1ce3ac`)
- [x] B1 commit 3 — store ledger (`d2813a1`)
- [x] B1 commit 4 — worker wiring (`d3d53d7`)
- [x] B2 — console read side (`178162e`): `python/jester/runout.py` (series +
      drift rules: all-pages-failed, shape-gone w/ clean-pages + prior-yield
      corroboration, coverage-drop vs pooled baseline w/ COVERAGE_FLOOR guard,
      repeat-failure as a 3-streak not a lifetime); `/api/outcomes` route;
      Sources page trend column + drifting filter + summary note; 14 tests
      (`test_console_runout.py`) incl. the honest-limit pins (anchor silence,
      born-thin field, flappy streak). Full python suite 2325 passed; doc
      tests 9 passed; JS `node --check` clean.
- [x] B3 — docs: STATUS.md gained "As of 2026-11-04" (branch complete, not
      merged, archive untouched) + the wave section with the five-commit
      table, drift-rule limits, honesty notes and verification evidence;
      ledger + research report become committed artifacts in this commit.
- [x] Coverage-gate repair (`75687a2`, pushed cycle 2026-11-04): PR #88's
      first CI run passed every required context but SonarCloud's quality
      gate — new_code coverage 68.2% < 80. Diagnosis from SonarCloud's own
      API (per-file new_lines/new_uncovered_lines/toCover): worker/main.go
      58 to-cover / 58 uncovered dominated the loss math
      (1 - 89/280 = 68.2% exactly). Repair: RunOutcomes reader + worker
      failure-mode rows (CDP-refused, no-adapter), httptest realestate walk
      (fresh then dup), siteprofile drop-counter fixtures — which exposed a
      real bug: ldjson's NoURL counter skipped its increment; fixed and
      pinned. Local proxy for the gate at the repair commit: worker 26
      uncovered-to-cover left (the one-per-adapter return lines only a live
      network can reach), siteprofile/store/realestate new lines 0/0/4
      (browser fork) → estimated gate ≈ 88%.
- [x] Merge PR #88 when green — authorized and done (2026-11-04): the
      branch's second CI run `41799904853` passed every required context
      (Python 6m02s, Go 43s, Frontend 12s, Windows 2m15s, Compose 1m14s,
      Sonar — CI scanner 1m23s, SonarCloud Code Analysis pass — the gate
      now reads clean after `75687a2`), and the PR landed as merge commit
      `55222a7ceb7c36ea9e4801c0b6e6d4c13806fcef` at 2026-11-04T08:37:56Z,
      preserving every wave commit above. Dependabot PRs #80/#81 stay
      held regardless.
