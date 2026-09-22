# YU-01 delivery plan — what changes in Jester, and what ships when

> **STATUS: ACTIVE (2026-09-22). W1 + W2 delivered same day; next gate is 9 Oct (20 fixture cases).** Owner: Yosri. Task: YU-01, daily Reddit problem-signal scraper, 22 Sep–20 Nov 2026.
> Companion to `2026-09-22-YU01-status.md` (today's result + end-of-day post). This file is the forward plan: the nine weekly outputs, the code that has to change for each, the hours, and the two decisions that gate everything.
> Milestone wording is quoted from the team plan; nothing here reinterprets a gate.

---

## §0 One sentence

Build the Reddit problem-signal collector as a separate, API-only path inside Jester — schema and export first (done), then filters, then twenty fixture cases, then ClickHouse, and only then live runs — so every milestone through 16 Oct lands without waiting on Reddit access, and the 9 Oct fork has its replacement already built.

---

## §1 Definition of done — the nine weekly gates

The plan states these; they are not mine to soften. A milestone is done when its **gate** passes, not when work happened.

| Week | Date | Deliverable | Acceptance gate (from the card) |
|---|---|---|---|
| W1 | **28 Sep** | Local scaffold | Field map + 5 synthetic records exported to CSV. Labeled synthetic. One command reproduces the export. **✅ delivered 22 Sep** |
| W2 | **2 Oct** | Scope/access status | 3–5 communities, queries, fields, cost and permissions documented. Filtering/deduplication built on synthetic inputs. |
| W3 | **9 Oct** | Fixture pipeline | 20 labeled cases pass: relevant, irrelevant, ambiguous, edits/deletions, failures. All deterministic checks pass. **Fork:** access unresolved → Seif names access date/budget *or* replacement collector. |
| W4 | **16 Oct** | Queryable store | ClickHouse + CSV. Repeat import creates no duplicates. Source dates, queries, deletion handling and export reconcile. Saved SQL/count reconciliation owned by me; **Rassil reviews once, max 1 h**. |
| W5 | **23 Oct** | Live runs *(gated)* | With approved access: 3 scheduled live daily runs + 1 recovery pass; actual unique/relevant/exported/error counts saved; zero-result run valid. Without: the approved replacement milestone, mode labeled. |
| W6 | 27 Oct–2 Nov | First real batch | Source-linked problem batch from the approved source; actual counts. Zero is valid. Routine runs/digest ≤ 4 h/week. |
| W7 | 3–9 Nov | Weekly digest | Repeated needs, source links, actual counts and unknowns. Seif checks ICP usefulness. No automatic outreach. |
| W8 | 10–16 Nov | One evidenced fix | Fix one relevance or recovery defect; before/after compared. Scheduled batch keeps running. No defect → record the checks. |
| W9 | **20 Nov** | Handover | Collector, schema, saved queries, CSV, schedule and recovery steps handed over. Final counts reconciled. Source/access mode and remaining limits stated. |

**Fail conditions** (also the card's): fixture rows called live · silent failures · duplicates · missing source evidence · blocked time hidden · unrelated tasks opened.

---

## §2 What exists now (22 Sep, end of day)

**Delivered today — W1 gate passed six days early.**

| Piece | Where | State |
|---|---|---|
| Field map (26 fields) → SQLite DDL, ClickHouse DDL, CSV header, all generated from one definition | `python/jester/signals.py` `FIELDS` | done |
| `problem_signal` table: upsert keyed on `record_id`, first/last seen, edit detection via `text_hash`, revision count, removal recorded not deleted | same | done |
| 5 labeled synthetic records covering clear / ambiguous / rejected / edited / failure | same, `SYNTHETIC` | done |
| One repeatable command: `python -m jester.cli signals --db … --out …` | `cli.py` `cmd_signals` | done |
| Export + `manifest.json` proving CSV↔table reconciliation, counts grouped by `mode` | `signals.py` `export_csv` | done |
| 12 tests on the gates that come later (no duplicates, first-seen kept, edit dated once, removal recorded, reconciliation, excerpt vs hash) | `python/tests/test_signals.py` | done |

**What Jester already has that this task can reuse:** a scheduler (`schtasks`, 30-min cycle, retries, requeue), a CSV exporter with retention pruning, a run ledger with per-run counts, and working public-API adapters for Hacker News, Discourse, Lemmy, Stack Exchange and GitHub — the last of which is the 9 Oct fork's insurance (§5).

**What Jester has that this task must NOT use:** the CloakBrowser Reddit scraper (`go/internal/reddit/`). It bypasses the API, which the card forbids. It stays for the nugget pipeline; no YU-01 artifact may come from it.

**What does not exist yet:** a classifier that produces `match_reason`/`match_confidence`, 20 fixture cases, a ClickHouse instance, a Reddit API client, and approved access.

---

## §3 Locked decisions

1. **API-only path for YU-01.** Separate module (`signals.py` + friends) from the browser collector. They share the DB file, the CSV/retention machinery and the scheduler — nothing else.
2. **No live code path until access exists.** `--mode` accepts `fixture` only. A live collector that cannot run cannot misfire, and a fixture row cannot be mislabeled by accident.
3. **Mode is a column, not a claim.** Counts are produced by `GROUP BY mode`. Every report quotes those numbers.
4. **Unknown stays unknown.** `company` and `buyer_intent` are columns fixed at `"unknown"`; no inference path exists. No Apollo enrichment, no DMs, no identity resolution — the card lists these under Avoid and Fail.
5. **One field map.** Schema changes happen in `FIELDS` or not at all; the three schemas regenerate.
6. **Removal is recorded, never deleted.** Retention (§14) removes text on its own schedule; the row survives so counts reconcile.
7. **Zero is a valid result.** A run that finds nothing relevant reports zero; no volume quota, no padding.
8. **Deterministic checks only** for the fixture pipeline (W3). No model in the acceptance path — a gate that depends on an LLM's mood is not a gate.

---

## §4 Build order — what changes in Jester, week by week

Hours are against the card's own estimate (**30–40 h total**, 20 internal h/week from 1 Oct). ~4 h spent on W1.

### W2 · 29 Sep–5 Oct — scope/access + filters (gate 2 Oct) · ~6 h — **DELIVERED 22 Sep**

**Deliver:** the scope/access document Seif signs, and filtering/dedup working on synthetic inputs.

**Done (22 Sep, ten days early):**
- `config/signal_rules.yaml` — 31 problem phrases, 14 negative, 3 hard-reject, thresholds, and the provisional scope (5 communities + 3 queries) with `status: provisional` until Seif signs.
- `python/jester/signals/filters.py` — deterministic `classify()` returning relevant / confidence / reason / ambiguous plus the phrases for and against; `apply_to()`; `crossposts()` (reported, never merged).
- `python/jester/signals/scope.py` + `jester signals scope` — the 2 Oct document generated from the rules that actually run, including the commercial-access section and the pre-named 9 Oct fallbacks.
- Fixtures now declare a **category** and the classifier produces the verdict: `jester signals check` grades all 5 cases (relevant / ambiguous / irrelevant / edited / failure), non-zero exit on a miss. This is the harness the 9 Oct 20-case gate extends.
- 18 new tests (`test_signals_filters.py`). Suite: 756 passed, 1 pre-existing unrelated failure.
- **Defect found and fixed while testing:** word-bounded phrases fired inside URL slugs (`/manually-curated-list` matched `manually`). Reddit posts are mostly links, so this was a live false-positive source. URLs are stripped before matching.

| Change | File | Why |
|---|---|---|
| `classify(text, rules) → (relevant, reason, confidence)` — deterministic keyword/phrase rules, no model | `python/jester/signals/filters.py` (new; `signals.py` becomes a package) | W3's gate needs *deterministic* checks; the reason column must be human-checkable |
| Rule set as data, not code: problem phrasings, negative markers (showcase posts, hiring ads, memes), ambiguity band | `config/signal_rules.yaml` | Seif can change targeting without a code change; the rules are reviewable |
| `jester signals scope --out …` — prints/writes the scope document from the config (communities, queries, fields, retention, estimated volume) | `cli.py` | The 2 Oct deliverable is generated from the thing that actually runs, so it cannot drift |
| Dedup already exists (upsert); add **cross-post detection** (same text hash, different `record_id`) reported, not merged | `signals/__init__.py` | A crosspost is two real records; collapsing them silently would break counts |

**Scope document contents** (what Seif decides, I draft): 3–5 communities with the reason each is a buyer-signal source · the search terms per community · the field list (link `field-map.md`) · retention and permitted-storage terms · estimated records/day · the access options and their cost · the permissions that must be in writing.

### W3 · 6–12 Oct — 20 fixture cases (gate 9 Oct) · ~6 h

| Change | File | Why |
|---|---|---|
| 20 labeled cases, 4 per category: relevant · irrelevant · ambiguous · edited/deleted · failure | `python/tests/fixtures/signals/*.json` + loader | The gate names these five categories exactly |
| `jester signals fixtures --check` → per-case pass/fail table, exit non-zero on any failure | `cli.py` | The gate is "all deterministic checks pass"; it must be one command with an exit code, not a reading exercise |
| Edit/deletion replay: run 1 sees the post, run 2 sees it edited, run 3 does not see it → assert `revisions=1`, `edited_utc` set, `removed_utc` set | `tests/test_signals_fixtures.py` | Three of the five categories are only meaningful across runs |
| Failure cases: 503, malformed payload, missing field, rate-limit response | same | "Silent failures" is a Fail condition; each must produce a counted `run_status=error` row |

**9 Oct fork is executed here, not discussed** — see §5.

### W4 · 13–19 Oct — ClickHouse + CSV (gate 16 Oct) · ~8 h

| Change | File | Why |
|---|---|---|
| ClickHouse service, local, profile-gated | `docker-compose.yml` (`clickhouse` under `--profile signals`) | No instance exists today; naming it now avoids a 16 Oct surprise |
| Loader: SQLite → ClickHouse, `ReplacingMergeTree(last_seen_utc)`, batched insert, idempotent | `python/jester/signals/clickhouse.py` | "Repeat import creates no duplicates" is the gate; the engine does it by design and the test proves it |
| Saved SQL: `queries/*.sql` — unique collected, relevant share, per-community counts, edits, removals, errors, DB↔CSV reconciliation | `python/jester/signals/queries/` | The gate says I own "saved SQL/count reconciliation"; saved means files, not history |
| `jester signals load` + `jester signals check` (runs every saved query, prints the table, non-zero exit on a mismatch) | `cli.py` | One command produces the evidence Rassil reviews |
| Evidence pack for the review: schema, 3 saved queries with output, the reconciliation manifest, the fixture report | `data/exports/signals/evidence/` | Rassil gets **one hour**; he should read, not hunt |

**Book Rassil's hour for Mon 13 or Tue 14 Oct.** He releases App 1 that same week (13–19 Oct); leaving it to the 16th risks colliding with his own gate.

### W5 · 20–26 Oct — live runs (gate 23 Oct, access-gated) · ~8 h

*Only if access is approved by 9 Oct.* Otherwise this slot delivers the named replacement milestone (§5).

| Change | File | Why |
|---|---|---|
| `RedditClient`: OAuth2, token refresh, `User-Agent: windows:jester-signals:v1 (by /u/<handle>)`, listing + search + comments, reads `X-Ratelimit-Remaining`/`-Reset` and sleeps | `python/jester/signals/reddit_api.py` | The only permitted collection path |
| Jittered pacing, well under the granted limit; one token shared across queries | same | "Do not bypass limits" |
| `jester signals run --mode live` — one daily pass per community/query, writes rows, stamps `run_status`, records the run in the ledger | `cli.py` | The daily unit of work |
| Recovery pass: re-runs the failed records of the last run, clears or keeps the error | `signals/recovery.py` | The gate asks for 3 runs **+ 1 recovery pass** |
| Schedule: reuse `jester.schedule` with a `signals` task, daily | `python/jester/schedule.py` | The scheduler already survives reboots |

### W6–W9 · 27 Oct–20 Nov — operate, digest, fix, hand over · ~6 h total

| Week | Change | File |
|---|---|---|
| W6 | First real batch; the counts are whatever they are. Nothing new is built unless Seif funds it. | — |
| W7 | `jester signals digest --week …` → markdown: repeated needs (clustered by rule hits), source links, counts, and an explicit **unknowns** section | `signals/digest.py` |
| W8 | One evidenced fix: take a real false positive/negative or a recovery failure, fix it, re-run the fixture set, publish before/after | wherever the defect is |
| W9 | `HANDOVER.md`: run it, schedule it, recover it, query it, what the limits are, what mode the data is in | `docs/signals/HANDOVER.md` |

**After release the card caps routine operation + digest at 4 h/week.** That cap is in the plan; the remaining hours are Seif's to assign (§6).

---

## §5 The 9 Oct fork — both branches, planned now

The card: *"On 9 Oct Seif decides access budget/date or transfers remaining hours to a named approved-source collector; no indefinite waiting."*

**Branch A — access approved.** W5–W9 as written above.

**Branch B — access not approved.** The replacement must be a *named approved source*. Jester already has working, documented-public-API adapters, so the fork costs days, not weeks:

| Candidate | Access | Why it substitutes | Cost to adapt |
|---|---|---|---|
| **Hacker News** (Algolia Search API) | public, keyless, documented | Ask HN is the densest software-delivery pain feed in the source list; already ingesting | ~4 h — map its JSON to `FIELDS` |
| **Discourse instances** (`latest.json` / `t/<id>.json`) | public, per-instance | One adapter reaches many forums; operator/practitioner pain | ~4 h |
| **Stack Exchange** (official API, key optional) | public, documented, quota'd | Explicit problem statements by construction | ~5 h |
| **Lemmy** (public API) | public | Reddit-shaped discussion without the ToS question | ~4 h |

**My recommendation to Seif, to be decided *with* the access decision, not after it:** name **Hacker News (Ask HN) + one Discourse instance** as the fallback now. Then 9 Oct is a five-minute confirmation instead of a week of drift, and the 16 Oct and 23 Oct gates stay on their dates either way. The field map, filters, fixtures, ClickHouse load, digest and handover are all source-agnostic — only the fetch layer changes.

---

## §6 Decided / Open

### Decided (mine to decide, decided)
- API-only module, separate from the browser collector.
- Deterministic classifier for the acceptance path; no model in a gate.
- Rules live in `config/signal_rules.yaml`, editable without code.
- ClickHouse runs locally in Docker for W4 unless credentials arrive first.
- Evidence pack prepared *before* Rassil's hour.

### Open — Seif owns, dates from the card
| # | Question | Needed by | If unanswered |
|---|---|---|---|
| **1** | **Commercial Reddit API access.** Reddit's free Data API tier is for non-commercial use; this task is commercial (it feeds the buyer test), so it needs a paid agreement and **written** approval covering storage *and* AI processing. Who applies, what budget, what date? | **2 Oct** status, **9 Oct** decision | 9 Oct fork fires → Branch B |
| **2** | **3–5 communities + search terms.** This defines the buyer test, so it is his call. Starting proposal from Jester's existing list: r/ExperiencedDevs, r/devops, r/sysadmin, r/selfhosted, r/smallbusiness. | **2 Oct** | I build filters against the proposal and mark it provisional |
| **3** | **Named replacement, pre-agreed** (§5). | with #1 | 9 Oct becomes a stall instead of a decision |
| **4** | **ClickHouse instance + credentials** scoped to this collector, or approval to self-host locally. | **13 Oct** | I self-host locally and state it in the handover |
| **5** | **Rassil's hour**, booked 13–14 Oct. | **8 Oct** | Slips into his App 1 release week |
| **6** | **The surplus hours.** YU-01 needs ~6–8 h/week; the allocation from 1 Oct is 20 h/week. That is **~12 h/week unassigned**, which the plan says must be surfaced, not absorbed ("Show unassigned hours; never hide them under polishing or meetings"; "Do not spend 20h/week watching an automated collector"). What gets the rest? | **29 Sep** (first full week) | I report the surplus weekly until it is assigned |

---

## §7 Risks

| Risk | Impact | What I do about it |
|---|---|---|
| Reddit commercial access is slow or refused | Live milestones (23 Oct onward) never start | Everything through 16 Oct is offline by design; Branch B is pre-built and pre-named |
| Rules over-fit the synthetic fixtures | Live precision collapses on first contact | Fixtures are written from *plausible* phrasings, not tuned against real posts; W6's first real batch is treated as the calibration, and W8 exists to fix exactly this |
| ClickHouse is a new dependency in a project that has none | 16 Oct slips on infrastructure, not on the work | Profile-gated container this week, not on 16 Oct; the loader is testable against a local instance |
| 20 h/week allocated, ~7 h needed | Looks like idle time or invites busywork | §6 #6 — surfaced weekly as a number, with a request for assignment |
| A live row and a fixture row get confused in a report | Straight into the card's Fail list | `mode` is a column; every count is `GROUP BY mode`; there is no live code path yet at all |

---

## §8 Backlog (not now, and not without funding)

- A read-only "Signals" page in the Jester console for Seif's usefulness check — only if he asks for it; the CSV and the digest come first.
- Embedding-based near-duplicate detection across communities (Jester has Qdrant already).
- Sentiment/urgency scoring — explicitly *not* a gate, and it risks reading intent into a post.
- Multi-platform signal merge (Reddit + HN + Discourse in one table) once one source is proven.

---

## §9 What to post now (kickoff, Tue 22 Sep)

**1. Weekly outcome:** YU-01 W1 — local Reddit-collector scaffold: field map + 5 labeled synthetic records exported to CSV by one repeatable command.
**2. Today's outcome:** the same, delivered today.
**3. Done-when:** `python -m jester.cli signals` writes `signals-fixture.csv` with 5 rows, all `mode=fixture`; a second run adds no rows; the manifest reconciles DB and CSV; tests pass.
**4. Proof I will show:** command output, `signals-fixture.csv`, `field-map.md`, `schema.clickhouse.sql`, `manifest.json`, `pytest tests/test_signals.py` (12 passed).
**5. Blocker, owner, deadline:** commercial Reddit API access + written approval, and the 3–5 communities — **owner Seif**, status due **2 Oct**, access-or-replacement decision **9 Oct**. Not blocking today; everything to 16 Oct is offline by design.

End-of-day post: `2026-09-22-YU01-status.md` §1.
