# Changelog

What each version contains, in the shape of the three products this repository
holds. The authoritative per-item status is [docs/STATUS.md](docs/STATUS.md);
this file is the history. Dates are UTC+01:00 (the operator's zone).

## The three products

| Product | State | Where it lives |
|---|---|---|
| **Idea pipeline** — scraped comments → insights → clusters → scored ideas | **Paused since 2026-10-02.** Code and tests complete; the three scheduled tasks are registered and disabled. Resume after the B1 gate in the 2026-10-31 strategy report. | `python/jester/` (agents, archivist, console), `data/jester.db` |
| **Problem signals** — a deterministic collector that archives buyer-intent records | **Live**, five scheduled tasks, fixture gate green on 2026-10-31. | `go/`, `python/jester/signals/`, `data/signals-*.db`, ClickHouse |
| **Journal** — plan → run → check → publish with evidence | **Built through slice J3; nothing published yet** (0 articles). | `python/jester/journal/`, `data/journal.db`, site worktree |

## [Unreleased] — 2026-11-02

Hardening on branch `v3-hardening` (base `5a4bd36`). Not tagged, not pushed,
not merged; `main` is still `d2da8de` (`v0.1.0`). User-visible changes only;
per-item status is in [docs/STATUS.md](docs/STATUS.md).

### Idea pipeline
- `GET /api/config` no longer returns the scraper licence key or proxy. Any key
  matching `license|proxy|secret|token|key|password` is blanked server-side,
  and a `secrets_set` map tells the console whether a field is set — a literal
  `${VAR}` counts as not set, the same rule the Go side applies.
- An idea's evidence is claimed and the idea inserted in one `BEGIN IMMEDIATE`
  unit, with a deterministic support key and an index over it. A crash between
  the claim and the insert leaves neither; two writers on the same evidence
  produce one idea.
- A corrupt `blocked_until` fails closed: the quota reports a named block
  window instead of reading as "no block". A quota charge is one conditional
  statement whose rowcount is the verdict, so two processes cannot both spend
  the last unit; the rescore queue takes a per-idea claim before asking.
- A reply that could not be parsed, or that the role's own check refused, is
  filed `ok=0` with an error instead of `ok=1`. Rescores now file `llm_calls`
  rows, and nuggets archived without a vector are counted and logged once per
  run.
- One retry policy: socket timeout, DNS failure, reset connection and TLS error
  are retried like a 503, and every attempt shares one per-call deadline rather
  than restarting the timeout.
- Crash recording is scoped to the run the command started. A failing
  subcommand no longer marks an unrelated live run as errored.
- Read paths execute no DDL: `GET /api/jobs`, `get_job` and `recent_jobs` ask
  `sqlite_master` first, and a missing table returns the documented default.
- Unknown top-level `thresholds.yaml` keys are refused by name, and a `roles:`
  block must name a provider and model for each role it lists instead of
  inheriting a built-in silently.
- The console refuses a body over 1 MiB with 413, answers a foreign `Host` with
  403, times out an idle connection at 30 s, and still answers `localhost` for
  the compose healthcheck.
- `scripts/check_lock_parity.py` runs in both Python CI jobs: an unpinned
  requirement, one missing from `pylock.toml`, or a floor above the locked
  version fails the build.
- `jester eval` gates the extraction report on κ >= 0.6 when κ is defined, and
  prints the double-labelled denominator the floor was measured over.
- `jester eval agreement` reports per-axis MAE between human and model scores
  with its denominator, never imputes a missing value, and says when fewer than
  `--min 30` rows are comparable.

### Problem signals
- Each source has a streak row (`source_state`): consecutive zero-result and
  error runs, the last read, the last error. `jester health --days 7` prints it
  per source, exits 1 on a barren (3 zero runs), stale (36 h unread) or
  erroring source, and prints JSON with `--json`.
- A scheduled run that fails now reaches the operator: the Windows launcher and
  the POSIX cron line call `jester notify` on a non-zero exit and still exit
  with the run's own code.
- A correction keeps the row it replaced (`problem_signal_revision`, with
  `signals revisions --restore`), every mutable field is refreshed, and a
  sweep judges only the ids a *completed* fetch actually saw, so a Hacker News
  deletion is observed rather than inferred. Those reads are kept as
  `problem_signal_read` rows.
- The CSV export is complete or absent: written to a temp file, fsynced and
  renamed, reconciled by the row-id set and a content hash, and both files
  removed if either step fails.
- ClickHouse schema v2: missing columns are added with `ALTER TABLE ... ADD
  COLUMN IF NOT EXISTS`, and `signals load` prints the schema version, the
  columns described and what it added. A field added to the map no longer needs
  a manual `DROP`.
- `jester signals retention --days 365` deletes records whose last sighting is
  older than the window, and ages out `problem_signal_read` audit rows by read
  time. Declared as `JesterSignalsRetention` (weekly SUN 04:00) and **paused**:
  the excerpt is the only copy, so the TTL is an owner decision.
- The ClickHouse load is declared, not implied: `JesterClickHouseLoad` (daily
  10:30, after the brief) is in `config/schedule.yaml` and **paused**, because
  SQLite is the source of truth and a load needs a running ClickHouse.

### Journal
- An approval binds what it approved: a manifest over the revision, every
  figure and every saved copy, hashed at approval time. Export and the move to
  `published` recompute it and refuse on a mismatch, naming the changed figure
  or the saved copy.
- An approval lapses after 30 days (`journal_approval_ttl_days`), and both
  gates ask for it again rather than accepting an old one.
- `obs:` targets need a reason: `obs:<label>|<reason>` with at least 8
  characters after the `|`. Headings and inline code are prose now — a number
  in an `<h2>` or between backticks counts like any other figure.
- `published`, `shared` and `measured` articles can be retracted
  (`journal retract SLUG --reason`): the state, the reason and the transition
  are recorded, and the article leaves the index, the feed and export.
- `journal snapshots SLUG --verify` re-checks the stored bytes on disk by
  default and re-fetches only with `--network`; a changed page is marked stale
  and blocks the export that would quote it.

### Operations
- `jester backup` covers `journal.db`, `chat.db` and `.env` alongside the
  archive, writes one manifest per set, verifies the snapshot it wrote, names
  what was absent, and can copy the set to a second volume (`--also-to`).
- `jester backup restore <file> <new path>` restores into a file that does not
  exist yet, decompressing on the way, and runs `integrity_check` plus the
  manifest's counts before it reports success. `jester backup drill` does the
  same into a temp directory; declared monthly and **paused**.
- The console container runs as uid 10001 with cron removed from the image, so
  the scheduler has one owner: the host's.
- Windows tasks register `/rl LIMITED` and take `/run-as` explicitly, never
  SYSTEM; run logs rotate at 5 MB with three kept; a launcher format marker
  (v2) makes an argv-identical older launcher visibly stale to `apply` and to
  the console.
- `docs/data-retention.md` now states that the signal archive has its own
  365-day window rather than claiming nothing is ever deleted.

### Repository
- `python/tests/test_doc_links.py` checks every relative link in every tracked
  Markdown file, skipping code blocks, inline code, anchors and URI schemes.
  It found one real break, now repaired.
- `docs/plans/archive/README.md` defines when a plan is archived and records
  that, as of 2026-11-02, no plan's own header says it is superseded. Nothing
  was moved and no report was touched.
- README: SQLite is named the source of truth, ClickHouse an optional manually
  loaded mirror, and the tests paragraph carries counts measured on 2026-11-02.

## [0.1.0] — 2026-11-01

First release. The anchor was the 2026-10-31 state at commit `36d357b`, with
all five CI jobs green at `41199031390` (Linux, Windows, Go, frontend, Sonar
scanner); this entry adds the cycle that followed and is what `v0.1.0` tags.

### Idea pipeline
- Seven model jobs resolved in one place (`roles.py`); every role has a
  provider, model and generation settings that a run records as a digest.
- Extraction, synthesis, scoring, labelling, dedup and archiving, with an
  archivist that refuses to mix embedding models.
- Batch extraction capped at five comments; a reply whose item indexes do not
  echo its positions is rejected, so insights are never attributed to the
  wrong comment.
- `quota_stopped` is a terminal run status, not a success; one tick owns a
  day; a rescore pass has its own budget and drains the backlog.
- `llm_calls` provenance: role, model, prompt/response hashes, latency;
  full text only with `JESTER_LLM_CALL_TEXT=1`; pruned after 30 days.
- `jester evidence --run ID` writes a self-contained evidence pack.
- `jester eval` gates the scoring formula on committed known-good/bad pairs.
- `jester schedule status` prints a disabled task as `PAUSED` / `state :
  DISABLED`, and an enabled one as `RUNNING`/`idle`. It previously printed a
  disabled task as `INSTALLED just now`.
- `jester schedule apply` refuses to change the cadence of an **enabled** task
  without `--force`; a paused task moves freely. The refusal is reported in
  dry runs too, and exits non-zero.
- `config/schedule.yaml` declares the signals sweep weekly (SUN 08:00), which
  is what the code's own `SWEEP_CADENCE` says and what the host runs. It had
  said daily; applying it as-is would have run the live sweep seven times
  more often.
- `config/schedule.yaml` gains `JesterNightlyBackup` (daily 02:30, enabled)
  and `JesterNightlyRetention` (weekly SUN 03:30, **disabled** until the
  extraction sample is labelled).
- `jester doctor --no-migrate` diagnoses the archive without writing to it:
  the file stays byte-identical, absent tables print as notes, and a file
  with no Jester schema exits 1. The default still migrates, and Doctor now
  says which mode it used.
- Every `migrate=False` open (Doctor, the console, `eval sample`) no longer
  sets the journal mode. `PRAGMA journal_mode` writes when it changes the
  mode, so a viewing open used to take a WAL archive back to DELETE and
  rewrite its header — a write from the path that promises none.
- `eval sample` draws the deterministic extraction sample (seed 20261031);
  `eval extract` runs the quality report; CI now runs `eval extract` after
  `eval sample`'s file is committed. The draw exits 1 on a missing archive or
  an empty pool rather than creating an empty file or reporting success, and
  its design file records the archive's basename, not a filesystem path.
- The extraction sample is ranked by `sha256(seed, unique_key)` instead of
  `random.Random(seed)`. Same seed and pool still give the same ids, but the
  draw no longer depends on a PRNG implementation or on the order rows
  arrive in, and it no longer trips SonarCloud's rule about PRNGs in a
  security context.
- `pytest` is no longer a runtime dependency; it comes with `.[dev]` and is
  still pinned in `pylock.toml` for CI.
- CI's coverage gate is a floor again: `--cov-fail-under=86`, two points below
  the 88% baseline measured with the full suite on 2026-11-01.

### Repository
- The nightly live lane's first scheduled run (2026-11-01, `41300973142`,
  `acd424e`) found one test that had never executed.
  `test_cmd_run_writes_points_to_shared_server` ran the pipeline on the
  repository config, whose extractor is `qwen3:8b`, while the lane
  deliberately pulls only `qwen3:0.6b`: extraction fell back to the stand-in,
  the refusal in `_file_produced` archived nothing, and the failure had
  nothing to do with Qdrant. Behind it sat a second defect the first one hid —
  the test probed the bare `nuggets` collection while the pipeline writes
  `nuggets__<db stem>`. The test now pins the five model roles to the
  deterministic stand-ins (the set `jester smoke` pins) and asks Doctor about
  this database's collection. The dispatch on `0697e1d` is green
  (`41302659108`: 11 passed, 0 skipped), and the README and the workflow now
  say eleven live tests, which is what the lane collects.
- `docs/model-providers.md`: what leaves the machine, to which provider,
  under which variable, and what Jester keeps.
- `docs/STATUS.md`: one index across the S-, F- and ND- registers.
- `sonar-project.properties` ignores `pythonsecurity:S8706` for
  `python/jester/wal_bench.py` only, with the reason written next to it: the
  rule models `sqlite3.connect` as a connection-string sink, and that call
  takes a file name (`uri=False`, said explicitly at both sites). This was
  the last thing holding the new-code Security Rating below A.

### Data
- A verified archive snapshot was taken (2026-11-01, 225,694,167 bytes,
  `integrity_check` OK). The live `data/jester.db` was not modified.
- `eval/extraction/sample.jsonl` — 150 of 115,199 extractable nuggets, 89
  strata, proportional allocation, drawn **before** any retention sweep;
  re-drawn when the ranking changed to `sha256(seed, unique_key)`, with two
  runs producing identical bytes (`sha256 adbb909d…`).

### Problem signals
- Go collector and ClickHouse store with `FINAL` counting; Python digest,
  export (CSV + reconciling manifest) and fixture gate (21 cases).
- 600-character excerpts by construction, so retention has nothing to strip.
- Scope and access document, with the sign-off still pending.

### Journal
- Plan/run/table/figure/draft/check/snapshot/export/variant/posted/measure,
  each step gated: a number must point at a run, a quote at a saved copy, and
  publishing needs an approved, unchanged version.
- Fixture checkers: 31 citation cases, 12 planted unbacked numbers, 30 lint
  paragraphs (10/15 AI paragraphs flagged on held-out prose, 0/15 human).

### Console
- Read auth when a token is set, security headers on every response,
  same-origin POST check, and a refusal that drains the body so Windows
  delivers the 403 instead of a reset.

### Operations
- `config/schedule.yaml` + `jester schedule apply` (10 tasks at this version,
  8 at the 2026-10-31 anchor), with launcher-drift detection and a
  fake-`schtasks` test harness.
- `jester doctor` reports disabled tasks, unread config keys, missing tables
  and schedule drift.
- `jester backup --verify` (SQLite online backup, integrity check, keep 14)
  and `jester retention` (30-day TTL on raw text, batches, listings, exports).
- WAL measured against the rollback journal; rollback stays the default.
- CI: Linux suite with coverage, Windows platform subset, Go build/test/
  contract, frontend lint/build, Sonar scanner; `live.yml` authored for a
  nightly Ollama/Qdrant lane.
