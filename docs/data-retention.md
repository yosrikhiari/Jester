# Data retention

What `jester retention` removes, what it deliberately leaves behind, and why
the boundary is where it is. The code that enforces all of this is
`retention_sweep` in `python/jester/store.py` plus `prune_exports` in
`python/jester/export.py`; this page is the readable version of the same
facts, so a policy question has somewhere to land that is not a function
signature. The problem-signal archive is a separate database with a separate
window, enforced by `signals.retention` — see
[its own section](#the-signal-archive-has-its-own-window).

## The clock

One TTL: **30 days**, measured from each row's own timestamp — `created_at`
for nuggets and ingest batches, `observed_at` for listing observations, and
the manifest's `exported_at` for an export directory. `retention_sweep` takes
`ttl_days` and `now` for tests, but the CLI exposes neither: the shipped
sweep is 30 days, period.

```bash
python -m jester.cli retention                  # data/jester.db, exports/ beside it
scripts/retention.sh                            # the same command with PYTHONPATH set
python -m jester.cli retention --exports DIR    # prune a different export directory
```

The console's **retention sweep** button calls the same code path.

## What it removes

| Target | Action | Why |
|---|---|---|
| `nuggets.raw_text` | set to `NULL` past TTL | the raw comment is what the pipeline needed; the insight, category, URL and timestamps stay |
| `ingest_batch` | rows with `status='done'` past TTL are deleted | the queue's history is not evidence once processed |
| `listing_observation.payload` | `description` and `seller` keys stripped past TTL | free text and a person's name age out; price, rooms, surface and the rest stay |
| export directory | whole directory removed once its manifest is past TTL | a CSV export is a copy of the same raw text; a retention policy with a hole in it is not a retention policy |

A row that carries none of the listing keys is left untouched rather than
rewritten to an identical value, and the sweep reports the number of rows it
actually changed.

## What it leaves behind

- **Nugget rows.** The row — unique key, category, extracted insight, platform,
  source URL, thread, timestamps — survives. Reading an old idea still works;
  only the comment text behind the nugget is gone.
- **`ingested_comments`.** The fingerprint skip-list is never swept. This
  matters: deleting a done `ingest_batch` row does **not** make its comments
  look new again, because `enqueue_new_only` skips on `ingested_comments`.
  A repeat scrape stays a zero-new-comments run after the sweep.
- **Ideas, clusters, runs, quota state.** No sweep touches them. The archive's
  output is the product; the sweep minimizes input text, not results.
- **Qdrant vectors.** The vector store holds vectors and a `category` payload,
  not the comment text (`archivist.py` upserts `{"category": ...}`). Vectors
  are derived and can be rebuilt with `jester reembed --all`; they are not a
  second copy of raw text, so the sweep has nothing to remove there. In
  per-database local mode the stores sit beside the database as
  `<name>.qdrant/` (nuggets) and `<name>.ideas.qdrant/` (idea dedup): deleting
  a database without them leaves phantom vectors behind, which is why a
  throwaway database needs all three removed.
- **The extraction-evaluation sample.** `eval/extraction/sample.jsonl` holds
  150 comments drawn deterministically from the archive, and it is the one
  place raw text is meant to outlive the TTL. It is the substrate for the
  extraction-quality report (κ, precision, recall, fabrication), so wiping it
  would destroy the measurement too. It is bounded (150 rows, about 100 KB),
  versioned in git with the code that grades it, and re-drawn only by
  re-running `eval sample` against the same archive contents — the design
  file records the pool (115,199 comments, 89 strata) and the seed, because a
  new comment changes the pool and can change the ids. The sweep does not
  touch it; deleting it is a deliberate act.
- **Backups.** `jester backup` snapshots are ordinary files under
  `data/backups/` (beside the database unless `--out` says otherwise). The
  sweep does not delete them; a backup that quietly ages out with the data it
  protects would be worse than no backup.
- **The problem-signal archive.** `data/signals-*.db` and its ClickHouse table
  are a different pipeline with a different clock, so this sweep does not touch
  them: a signal record stores only a **600-character excerpt** by construction
  (`EXCERPT_CHARS`), never the full text, and there is nothing here for the
  nugget sweep to minimize. Its records are not immortal, though — they age out
  on their own window, described in the next section.
- **The journal.** `data/journal.db`, the article files and the site worktree
  are outside this sweep; they have their own versioning and approval gates.

## The signal archive has its own window

The problem-signal archive is a separate database (`data/signals-*.db`) and it
has its own sweep, `jester signals retention`, which is a different command
from the 30-day sweep above. What it does:

- **One window: 365 days**, set in `schedule.COMMAND_SPEC` as
  `signals retention --days 365`; the command's own `--days` default is the
  same 365, so a hand run and a scheduled run agree. It is
  measured from each record's **last sighting**, not its first: a posting still
  being returned every morning is current, and the window is "nobody has seen
  this for a year".
- **It deletes whole records.** A signal record is a 600-character excerpt and
  the row is the only copy of it, so there is nothing to minimize field by
  field: the row goes, and with it the evidence behind every buyer count taken
  from it. A window below one day is refused (`--days 0` would mean
  "everything").
- **The read audit ages out by its own clock.** `problem_signal_read` rows
  (which query read which record, and whether the read completed) are removed
  by `read_utc` once they are older than the window, even in a run that deletes
  no record. That table is bookkeeping for the removal sweep, not an archive.
- **It is PAUSED.** `JesterSignalsRetention` is declared weekly on SUN 04:00
  with `enabled: false`. Nothing deletes a signal record until the owner picks
  a TTL and flips the declaration; a load or a run by hand is the only way it
  happens before that.
- **It is observable.** `jester health --days 7` reports each source's streak
  and exits 1 on a barren, stale or erroring one, and `jester signals evidence`
  reconciles SQLite against ClickHouse against the CSV export. A sweep that
  removed more than intended shows up as a change in the first and a
  disagreement in the second.

The command is safe to run by hand:

```bash
python -m jester.cli signals retention --db data/signals-live.db --days 365
```

## Backups and retention are declared, not remembered

`jester backup` uses SQLite's online backup API (a consistent snapshot while
the archive is being written — a filesystem copy taken mid-write opens fine
and is corrupt), then **opens the snapshot** and runs `PRAGMA
integrity_check` plus row counts before reporting success. Only after that
does it prune old backups, keeping the newest **14** (`--keep`).

`config/schedule.yaml` declares all four operational jobs:

| Task | Command | Cadence | Enabled |
|---|---|---|---|
| `JesterNightlyBackup` | `backup` | daily 02:30 | **yes** |
| `JesterNightlyRetention` | `retention` | weekly SUN 03:30 | **no** |
| `JesterSignalsRetention` | `signals-retention` | weekly SUN 04:00 | **no** |
| `JesterNightlyBackupDrill` | `backup-drill` | monthly day 1 at 03:00 | **no** |

Backup is enabled because it only adds a copy. The three disabled jobs are each
waiting on an owner decision: the nugget retention sweep NULLs `raw_text`, and
the extraction sample in `eval/extraction/` depends on that text; the signals
sweep deletes the only copy of a record's excerpt, so it waits for a chosen
TTL; and the restore drill is a monthly report whose machine the owner picks —
a backup nobody has restored is a backup nobody knows works.

`jester schedule apply` installs what the file says; `jester schedule status`
prints the disabled task as PAUSED. It refuses, and exits non-zero, when the
file moves the cadence of a task that is enabled right now — that is a change
to live request volume, so it takes `--force` to say so deliberately. Paused
tasks and scope changes move freely.

Either command still runs by hand:

```bash
python -m jester.cli backup
python -m jester.cli backup --verify data/backups/jester-20261030T030000Z.db.gz
python -m jester.cli retention            # the sweep has no dry run
```

The sweep removes data that only exists in the database; the backup is the
only other copy of the raw text it wipes, so the two are meant to be used
together.

## Concurrency: it stands down, it does not fight

If an active run holds the write lock, `jester retention` prints
`retention skipped: write lock held (...)` and **exits 0** — a sweep that
could not run is not a failed night. `open_db` is inside that guard on
purpose: opening the database is itself a write (schema reconcile), and an
earlier version that opened first died on a traceback instead of standing
down.

## Irreversibility

Wiping `raw_text`, deleting done batches and removing an export directory are
not undoable from the sweep itself. The safe order around anything you might
need later is: `jester backup`, then `jester retention`. Restore by stopping
the writers and opening the chosen snapshot as the database.
