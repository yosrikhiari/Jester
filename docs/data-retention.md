# Data retention

What `jester retention` removes, what it deliberately leaves behind, and why
the boundary is where it is. The code that enforces all of this is
`retention_sweep` in `python/jester/store.py` plus `prune_exports` in
`python/jester/export.py`; this page is the readable version of the same
facts, so a policy question has somewhere to land that is not a function
signature.

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
- **Backups.** `jester backup` snapshots are ordinary files under
  `data/backups/` (beside the database unless `--out` says otherwise). The
  sweep does not delete them; a backup that quietly ages out with the data it
  protects would be worse than no backup.
- **The problem-signal archive.** `data/signals-*.db` and its ClickHouse table
  are a different pipeline with a different rule: a signal record stores only
  a **600-character excerpt** by construction (`EXCERPT_CHARS`), never the
  full text, so there is nothing for this sweep to minimize. Counts stay
  reconcilable because the row is not deleted.
- **The journal.** `data/journal.db`, the article files and the site worktree
  are outside this sweep; they have their own versioning and approval gates.

## Backups are a separate command, not a schedule

`jester backup` uses SQLite's online backup API (a consistent snapshot while
the archive is being written — a filesystem copy taken mid-write opens fine
and is corrupt), then **opens the snapshot** and runs `PRAGMA
integrity_check` plus row counts before reporting success. Only after that
does it prune old backups, keeping the newest **14** (`--keep`).

Nothing ships a backup schedule. `scripts/nightly.sh` runs the pipeline; it
does not call `jester backup`. If you want nightly backups, schedule this
yourself:

```bash
python -m jester.cli backup
python -m jester.cli backup --verify data/backups/jester-20261030T030000Z.db.gz
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
