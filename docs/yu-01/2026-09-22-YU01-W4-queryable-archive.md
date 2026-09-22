# YU-01 — W4 delivered: queryable archive, no duplicates, saved SQL

> **2026-09-22.** The **16 Oct gate passes**, twenty-four days early. ClickHouse holds the archive, a repeat import creates no duplicates, seven saved `.sql` files answer the questions the task asks, and one command writes the pack Rassil reviews in his hour.
>
> Everything below ran against fixtures. Live rows need approved commercial Reddit API access, which Seif owns and which is still open.

---

## 1. The gate, in the words of the card

> *"Queryable ClickHouse + CSV by 16 Oct. Repeat import creates no duplicates. Source dates, queries, deletion handling and export reconcile. Saved SQL/count reconciliation owned by me; Rassil reviews once, max 1 h."*

| the card asks | where it is | verdict |
|---|---|---|
| Queryable ClickHouse | `clickhouse` service, `--profile signals`, HTTP 8123 | running, 24.8 |
| + CSV | `signals.csv` beside the pack, same rows | reconciles |
| Repeat import creates no duplicates | three loads of the same 5 rows → 5 rows, 5 distinct ids | 0 duplicates |
| Source dates | `created_utc` nullable, `first_seen_utc`/`last_seen_utc` always set | queryable |
| Queries | `query` column on every record, plus 7 saved `.sql` | in the pack |
| Deletion handling | `removed_utc` stamped, row kept | query 06 |
| Export reconciles | `manifest.json`: rows in table vs rows in file | `reconciles: true` |
| Rassil reviews once, max 1 h | `data/exports/.../evidence/README.md` | generated, one page |

Three commands, each exiting non-zero when it fails:

```bash
docker compose --profile signals up -d clickhouse
```

```bash
python -m jester.cli signals load --db data/signals-m2.db
```

```bash
python -m jester.cli signals evidence --db data/signals-m2.db --out data/exports/signals-m2
```

---

## 2. Why a repeat import cannot duplicate

Not a promise, a table definition. `problem_signal` is a `ReplacingMergeTree(last_seen_utc)` **ordered on `record_id` alone**, so the same record arriving twice collapses to one row and the newer sighting wins.

Two decisions inside that sentence were not obvious, and both were changed after thinking about what actually breaks:

**The sort key excludes community and platform.** The natural key looks like `(platform, community, record_id)`. It is wrong: the day a post is crossposted or a community is renamed, the same record lands under two sort keys and the archive quietly holds it twice — which is precisely the duplicate this milestone forbids.

**The partition key is `first_seen_utc`, not `created_utc`.** `created_utc` is nullable, because a fetch that failed has no post date. A null partition key rejects the row — so partitioning on it would silently drop exactly the failure records the run is supposed to count. Partitioning on when *we* collected it is also the question the archive gets asked.

**The consequence everyone forgets:** a `ReplacingMergeTree` collapses at merge time, not at insert. A `count()` without `FINAL` counts parts, not records, and drifts upward after every import. That is the one way this design can lie, so every saved query says `FINAL`, a test asserts it, and the reconciliation reports both numbers — `rows_final` versus `rows_raw` — so an un-merged table (normal) can never be mistaken for a duplicated one (a failure).

---

## 3. What reconciliation actually checks

Four numbers, because three of them can agree while the archive is still wrong:

| number | what it means | failure it catches |
|---|---|---|
| `sqlite_rows` | what the collector wrote | — |
| `clickhouse_rows_final` | what a query sees | rows lost in transit |
| `clickhouse_unique_ids` | distinct `record_id` | **broken dedup key** |
| `rows_in_csv` | what the export carries | an export that silently truncates |

`reconciles` is true only when the first three are equal and the CSV matches. The evidence pack exits non-zero otherwise, so the document and the exit code are decided by one set of numbers rather than two.

---

## 4. The seven saved queries

Files, not string literals — the point of *"own saved SQL"* is that someone else can open one and run it without this codebase.

| file | answers |
|---|---|
| `01-reconciliation` | one row per record? (the 16 Oct check, both counts) |
| `02-counts-by-mode` | what we collected, live vs fixture |
| `03-daily-collection` | which days the collector ran — the 23 Oct check in one query |
| `04-audience-by-community` | which communities produce buyers; `buyer_rate` decides scope |
| `05-buyer-signals` | the deliverable: link, excerpt, matched reason, confidence |
| `06-edits-and-removals` | records that changed or disappeared under us |
| `07-failures` | every failed fetch, grouped by what went wrong |

They live in `python/jester/signals/queries/`, and every run copies them into the pack beside their results as JSON.

---

## 5. The evidence pack is built for an hour, not for completeness

Rassil gets one hour, once. A reviewer with an hour cannot read a codebase, so `README.md` answers, in order: do the two stores agree, did a repeat import duplicate anything, what is in the archive, what failed, and where can I check a claim myself. Every number on the page is a query result written by the run that produced the folder — nothing is typed in, so the pack cannot claim a figure the data does not hold.

It also carries the **twenty labeled cases** from the 9 Oct gate. An archive that happens to contain no edits, removals or failed fetches proves nothing about how they are handled; the fixture set does, against a real table, and a reviewer should not have to take that on trust from a second document.

And it states what it does **not** claim: `company` and `buyer_intent` are always `unknown`, no handle is enriched or contacted, and fixture rows have never been near Reddit.

---

## 6. State of the task

| Gate | Due | State |
|---|---|---|
| W1 scaffold | 28 Sep | ✅ delivered 22 Sep |
| W2 scope/access + filters | 2 Oct | ✅ delivered 22 Sep |
| W3 20 fixture cases | 9 Oct | ✅ delivered 22 Sep — 20/20, coverage self-checked |
| **W4 ClickHouse + saved SQL** | **16 Oct** | **✅ delivered 22 Sep — 0 duplicates over 3 imports, 7 queries, pack generated** |
| W5 3 scheduled live runs | 23 Oct | blocked on access |
| W6–W9 batch, digest, fix, handover | 20 Nov | after W5 |

**Suite: 796 tests pass** (17 new on the loader; one pre-existing failure fixed on the way — `config/scraper.yaml` declared `version:` that Python's `ScraperConfig` did not, so the console's Config page was rendering a subset of the file).

**Unchanged blocker:** commercial Reddit API access, the community/phrase list, and ClickHouse credentials for wherever this runs that is not my laptop — **owner Seif**, status **2 Oct**, access-or-replacement **9 Oct**. Every milestone through 16 Oct was offline by design, and all four are now done.
