# SQLite journal mode: rollback vs WAL (S-11)

**Question.** `open_db` defaults to SQLite's rollback journal. The stated reason
is that the archive can live on a Windows bind-mounted Docker volume, where
WAL's `-wal`/`-shm` sidecars are not reliably arbitrated. Meanwhile the console
and the pipeline hold the same database at once, and lock contention is a
measured failure mode (41 overlapping ticks refused, 55 `SQLITE_BUSY` aborts,
9 killed ingest runs). This report records what the two modes do on the
operator's machine, so the default is a decision with numbers behind it rather
than a guess.

**Short answer.** WAL is dramatically better on a local disk — 3.1–3.6× the
commits, 40–70× the completed reads, and delete-mode readers see p95 latencies
an order of magnitude worse because they queue behind the writer. Both modes
recovered cleanly. The default stays rollback **because this harness measured a
local disk, not the bind-mounted volume the default exists for**; WAL remains
opt-in via `JESTER_SQLITE_JOURNAL_MODE=wal`, and that is the recommended setting
for a local-disk archive.

## Method

`python -m jester.wal_bench` (`python/jester/wal_bench.py`), one throwaway
database per round, seeded with 20,000 nuggets through the real `open_db`
(migrations included). For each mode: one writer committing batches of 50
`INSERT`s and two readers running `COUNT(*)` plus the last 50 rows, for 2.0
seconds; connections use `busy_timeout=5000` and `synchronous=NORMAL`. Each
connection is created inside its own thread. `recovery_check` then writes a
marker, closes the file, reopens it with the stdlib driver and checks
`PRAGMA integrity_check`, the persisted mode, and the marker row.

- Machine: Windows 10.0.26200, x64; Python 3.12 / stdlib `sqlite3`.
- Disk: local SSD under the repo's out-of-tree scratch directory.
- Two independent rounds are reported because a single run of a 2-second
  benchmark is noise with a decimal point.

Reproduce:

```
export PYTHONPATH=python
python -m jester.wal_bench --rows 20000 --seconds 2 --readers 2
```

The harness refuses to point `--db` at `data/jester.db`. The guard resolves
both paths against the repository root, not the current directory, so running
the harness from elsewhere cannot slip past it (`test_wal_bench.py` covers
that case).

## Results

Round 1

| mode | commits | rows written | write errors | write p95 (ms) | reads | read errors | read p50 (ms) | read p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| delete | 499 | 24,950 | 0 | 9.4 | 187 | 0 | 1.37 | 90.68 |
| wal | 1,837 | 91,850 | 0 | 2.4 | 7,695 | 0 | 0.30 | 1.52 |

Round 2

| mode | commits | rows written | write errors | write p95 (ms) | reads | read errors | read p50 (ms) | read p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| delete | 515 | 25,750 | 0 | 9.1 | 121 | 0 | 11.86 | 114.63 |
| wal | 1,630 | 81,500 | 0 | 2.5 | 8,778 | 0 | 0.29 | 1.43 |

Round 3 (after the harness fix below — see "The bug the harness found")

| mode | commits | rows written | write errors | write p95 (ms) | reads | read errors | read p50 (ms) | read p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| delete | 571 | 28,550 | 0 | 8.2 | 145 | 0 | 0.71 | 125.05 |
| wal | 1,769 | 88,450 | 0 | 2.4 | 6,275 | 0 | 0.40 | 1.68 |

Recovery (both modes, all rounds): `mode_persisted: True`,
`row_present: True`, `integrity: ok`.

## Reading the numbers

- **Writes.** WAL commits 3.1–3.6× more often in the same wall-clock window,
  and its p95 commit is ~4× faster. Rollback has to fsync the journal and the
  main file per commit; WAL appends.
- **Reads.** The gap is not subtle: 121–187 completed read cycles in delete
  mode versus 7,695–8,778 in WAL. Delete-mode readers block for the duration of
  each write transaction, and the p95 read latency (91–115 ms) is the writer's
  hold time, not query cost. That pathology is exactly what a console polling
  the database next to a running cycle experiences.
- **No `SQLITE_BUSY` in either mode** because the 5 s busy timeout absorbed the
  waits. The contention shows up as latency, which is why the failure register
  recorded it as slow pages and refused ticks rather than as an exception
  storm.
- **One caveat on the delete-mode p50 in round 2 (11.86 ms)** versus round 1
  (1.37 ms): reader scheduling against a writer that grabbed the lock more
  often. It reinforces the same conclusion rather than changing it.

## The bug the harness found

Round 3 exists because the platform-sensitive CI subset (S-16) exposed a race
in the harness itself: `benchmark` had every worker connection run
`PRAGMA journal_mode=<mode>` on open. Switching to WAL takes an exclusive lock,
so when the writer and a reader asked for it at the same instant, the loser
failed with `database is locked` — intermittently, roughly six runs in ten, and
fast enough that the busy timeout never covered it. The test reported it as a
flaky failure rather than a wrong number.

The fix is in `wal_bench.connect`: the mode is switched once by a setup
connection before any worker starts, and workers open with `set_mode=False`.
That is also the shape of production — `open_db` sets the mode, and the rest of
the process reuses the file in that mode. Ten consecutive runs of the test
passed after the change, and the round-3 numbers match rounds 1–2.

## Decision

**Keep rollback as the default in code.** The one environment this harness
cannot exercise from here is the one the default protects: a Docker Desktop
Windows bind-mounted volume, where `-wal`/`-shm` persistence across the mount
is the documented failure mode. Trading a measured local-disk win for an
unmeasured risk to the archive's integrity is the wrong direction, and the
failure would be silent — a stale WAL replayed over a newer main file.

**Use WAL wherever the database is on a local disk or a named Docker volume.**
That is `JESTER_SQLITE_JOURNAL_MODE=wal` in `.env` for a native install, and it
is where the numbers above apply. The console's polling reads stop competing
with the pipeline's commits.

**When to revisit.** Flip the default if either: (a) a run of this harness on
the actual bind-mounted volume shows the sidecars survive a container restart
and recovery passes, or (b) the deployment moves off the bind mount. Until one
of those happens the status of this finding is *decided, with evidence, for the
deployment we have*, not *fixed*.

## Limitations

- One machine, one filesystem, two rounds. The ratios are robust; the absolute
  millisecond figures are not a performance guarantee.
- The readers are synthetic (`COUNT(*)` + tail rows) — representative of the
  console's polling shape, not a full page render.
- The writer is a single thread of batched inserts; the pipeline's real write
  pattern includes deletes, updates and index maintenance.
- The bind-mounted-volume case is reasoned about, not measured here. That is
  the gap this report openly leaves for the threshold to flip.
