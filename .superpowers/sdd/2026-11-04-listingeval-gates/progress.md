# Phase B register — listingeval gates wired into the scheduled run ("gates in the loop")

Date: 2026-11-04 · Branch: `feat/listingeval-gates` (cut from `main` `49f080b`) ·
Source: [the 2026-11-03 Scrapling research report](../2026-11-03-scrapling-informed-enhancement/report.md),
§3.2 + roadmap "Phase B". Owner go-ahead for the phase: 2026-11-04 ("continue then",
given immediately after the Phase-A close-out report; push + PR + merge still need
fresh authorization when green).

## Goal

`listingeval` exists but only runs when a human remembers to run it by hand; drifted
portals stay green in the console between manual harvests. This phase makes the gate
live in the loop the way the ledger did:

1. Extract the grading core of `go/cmd/listingeval` into `jester/internal/grade` —
   same JSONL record shape, same floors, same `knownAbsent` register, same verdict
   math — so the worker (and anything else) can call it without a subprocess.
2. After each live realestate walk, the worker writes the run's parsed listings as a
   JSONL harvest artifact under the run's evidence dir, grades that sample against
   the profiles' floors, and records the verdict in the source's `run_outcome` row
   (inside `stats_json` under `grade` — no schema change: the grade is part of what
   the run saw, the same carrier the walk stats already use).
3. A failing floor reaches the operator through the same surfaces the drift ledger
   built: the worker log, the row's trend data (`/api/outcomes` → an alarm chip on
   the Sources page), and the signals/health digest.
4. Grading is deterministic in CI: fixture JSONL → grade table as unit tests; the
   worker path is pinned offline with the Phase-A httptest portal.

## Design decisions (Go side, settled)

- **Package `jester/internal/grade`** (flat under `internal/`, like every other
  internal package; the report's "internal/siteprofile/grade" alternative rejected
  for consistency with the tree). Imports `siteprofile` + `store`, same direction
  siteprofile already uses; nothing imports grade except the worker and the CLI.
- **`grade.Record` is byte-shape-identical to the CLI's `record` struct**
  (portal, market, listing_id, url, price *int64, currency, media_count, payload),
  so the JSONL `listingharvest` emits grades identically to what the CLI graded
  yesterday. The CLI loses its private struct, gains nothing else observable:
  flags, output lines, and exit codes stay exactly as they are.
- **Verdict per gate: `ok` | `fail` | `none` | `thin`.**
  - `ok` / `fail`: the floor math, unchanged.
  - `none`: named in `knownAbsent` (houni price, redfin/tunisieannonce media,
    tunisieannonce deal_type) — the register stays the truth-setter, byte-for-byte.
  - `thin`: the sample is below a quorum (default: 10 records per portal for run
    grades, so a 3-record walk cannot paint a red trend card out of noise; the
    bulk CLI keeps quorum 1 so its behavior today is unchanged).
- **`grade.Options`** carries the three floors (0.95 / 0.80 / 0.90 — the CLI flags'
  defaults), `MinUnique` (1000 for the bulk CLI; 0 = off for run grades — a routine
  walk visits a couple of pages, and "fewer than 1000" would fail every run),
  the quorum, `PortalFloors` (mubawab's two declared floors) and `KnownAbsent`
  (houni/redfin/tunisieannonce) moved **verbatim** out of the CLI.
- **Run-grade posture** (`grade.RunOptions()`): `MinUnique 0`, `Quorum 10`, same
  floors, same registers. Rationale comment: a gate that fails on every routine
  run teaches everyone to ignore the eval — the CLI's own warning, honored for
  small samples.
- **Zero rows ≠ a grade.** A walk that parsed nothing is already a failing ledger
  row (firstErr / walk stats); grading it would embed a JSON object with NaN
  shares and add a second, noisier alarm to the same fact. Grade only when the
  walk yielded ≥ 1 listing. Grading uses the walk's RAW `listings` slice — not the
  post-budget/post-dedup counts — so the grade measures the parser's own output.
- **Verdict carrier: `run_outcome.stats_json`.** The realestate row's stats JSON
  gains one additive key: `"grade": {total, unique, dupes, portals:[{portal,
  records, gates:[{name, verdict, got, floor, n, total, note?}], failures?[…],
  low_price?, deals?}]}`. No new columns, no schema_version bump, no backup/export
  contract touch — the console already parses `stats_json` for the trend cards.
- **Harvest artifact.** Decided and landed: `data/traces/<run_id>/<portal>.listings.jsonl`
  beside the archive (the db's own dir, threaded explicitly through `runLive` and
  `fetchSource` as `dbDir`). The records are `grade.Records(listings)` — the same
  shape the `listingharvest` command grades, so the CLI can regrade an archived
  sample verbatim. Written ONLY on a failing grade, best-effort, never fatal;
  a clean or thin walk writes nothing.
- **Worker print stays human, as landed:** `[grade] <portal>: N gate(s) failed`
  when gate verdicts failed, then one line per failing gate
  (`<portal>: FAIL <name>: n/total at floor NN%`) and the structural findings
  as-is (they already carry their portal prefix, exactly as the CLI prints
  them). A sample whose only findings are structural skips the count header.
  Every line prints beside the existing `[live] …` lines.

## Tasks

- [x] B1 — `internal/grade` package: Record/Options/Report, Grade(), RunOptions,
      registers moved verbatim from the CLI; 12 test functions covering every
      verdict path (ok/fail/none/thin), the per-portal checks (on-host, currency,
      empty payload, broken price, deal_type), dupes/min-unique, zero-sample
      safety, and Report JSON marshaling. Both floor registers pinned at their
      boundaries (mubawab 7/10 at floor 0.70; 19/20 at floor 0.95).
- [x] B2 — CLI refactor: `listingeval` main.go becomes flags + JSONL scan +
      `grade.DefaultOptions()` (flags override the three floors + min-total) +
      the same printed table; exit codes unchanged. Parity PROVEN with both
      binaries over four fixtures (mixed findings / 1000-record pass / empty /
      corrupt line): stdout, stderr and exit codes byte-identical. The old
      binary was built from a copy of the tree with only
      `HEAD:go/cmd/listingeval/main.go` swapped back in.
- [x] B3 — worker wiring (`16b76ce`): after each live realestate walk the worker
      grades the walk's RAW listing slice with `RunOptions()`, embeds the
      verdict in the row's `stats_json` under `grade` (walkStats embeds
      realestate.Stats + Grade, omitempty), prints failing-gate lines, writes
      the artifact on a failing grade only; `dbDir` threaded through
      `runLive`/`fetchSource`. Tests: failing-walk httptest drive (row + log +
      artifact regradeable in the CLI's shape), thin-walk silence (no verdict,
      no artifact), a walk with no output carries stats but no grade, a
      profile-load failure stands the grade down loudly. Two design fixes the
      test run itself surfaced: the `[grade]` header carries the portal name,
      and the fixture profile needs `deal_type` (the real profiles classify it,
      so structural findings stay quiet below quorum too).
- [x] B4 — console surface (`0423dd6`): `runout.drift_alarms` gains
      `grade-failed` via `_grade_failed` (newest GRADED row decides; a passing
      grade clears; an ungraded newest row does not; thin alarms nothing), the
      chip renders the alarm's own label (`grade: N gate(s)` /
      `grade: N finding(s)`), filters/notes/drifting_sources ride unchanged.
      Seven new tests in `test_console_runout.py`. The signals/health digest
      was scoped OUT — see the honesty notes.
- [x] B5 — docs: STATUS wave section, this register carried into the branch,
      counts row; doc tests 9 green before any push.
- [ ] Push + PR + merge — **requires fresh owner authorization**.

## Python-side map (was pending the explorer run — filled)

- Where scheduled runs get their run id / command line: the schedule runs
  `jester cycle` (`cli.py`); `cycle` already resolved its run id per invocation
  (`cycle-<UTC stamp>`, parser default None resolved in `cmd_cycle`), and this
  phase fixed `ingest` the same way (`ingest-<UTC stamp>` via
  `_default_ingest_run_id`, `e84d6a3`) — the static `cli-ingest` was
  overwriting the row's ledger history under the PK `(run_id, source)`.
- The run evidence dir convention: the db lives at `data/jester.db`; trace
  artifacts land under `data/traces/<run_id>/` beside it — inside the db's own
  dir by construction, so nothing else needs to know the path. The side
  contracts were checked rather than assumed: `backup.companion_paths` copies
  explicitly derived files (journal, chat, dotenv) and never sweeps the db's
  dir, `prune_backup_sets` iterates the backup OUTPUT dir, and no code path
  enumerates `data/` itself — the trace dirs are invisible to all of them.
- Signals/health alert channel: inspected and deliberately NOT used —
  `health.py` reads only its own signals-live.db archive (never run_outcome),
  so any grade wiring there is a NEW integration with its own data path, not
  a piggyback. Deferred; the console alarm + worker log are the operator
  surface this phase.
- Doc-contract counts mechanics: measured after B3 on the branch via
  `python scripts/counts.py --no-archive` → `python tests: 2364` /
  `go test functions: 338` (was 2343/306 at the Phase A close).

## Honesty notes

- Gates measure the run's own sample, never the archive's cumulative rows.
- `thin` is NOT `fail`: below-quorum samples report shares without verdicts, so
  the alarm channel stays readable (the eval's own warning, honored at run size).
  Structural findings are the deliberate exception — they stay loud at ANY
  sample size, because every shipped profile classifies `deal_type` from the
  URL (default sale) and the parser cannot miss a field it does not read
  conditionally; the quorum floors only the gate verdicts.
- `knownAbsent` moves untouched; a declared gap renders `none` with its reason,
  never a red chip.
- Mock mode keeps writing no ledger rows: the grade is a live fact like the
  ledger row it rides on.
- **Superseded design point:** the plan's earlier idea of a separate `grade`
  key on the console's trend card did not land. The grade surfaces ONLY as the
  `grade-failed` drift alarm — one fact, one surface — with the failing-gate
  detail (`grade: N gate(s)`, the gates and run id) inside the alarm itself.
- **Signals/health deferral (scope decision, recorded):** `health.py` reads only
  its own signals-live.db archive, never `run_outcome`, so riding the digest
  would be a new integration with its own data plumbing, not a piggyback. The
  operator surface this phase is the console alarm plus the worker's `[grade]`
  log lines; wiring the digest is left as an explicit follow-up, not claimed
  as done.
- The grade-failed rule keys on the ledger's newest GRADED row: a later
  ungraded row (a walk that went dark) does NOT clear the alarm, and a later
  passing grade does. Clearing the alarm requires a grade, not silence.
- Artifacts are evidence, not state: written once per failing grade besides
  the archive, best-effort, never a job stopper, and invisible to the backup,
  export and prune contracts (companion copies are explicit files; prune
  sweeps the backup out dir; nothing enumerates `data/`).
- The CLI keeps its bulk posture exactly as it was (quorum 1, min-total
  flagable): the `thin` verdict is unreachable in the CLI today and prints as
  what it is, not as an ok, should a sample ever hit it.
