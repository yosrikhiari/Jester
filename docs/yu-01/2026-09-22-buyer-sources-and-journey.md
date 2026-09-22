# Where the buyers are, and the journey that finds them

> **2026-09-22.** Three things: (1) which Reddit communities are worth targeting for Seif's problem, tested by scraping them rather than reasoning about them; (2) the end-to-end journey — UI, site, data — from a scrape to something Seif can use; (3) the Nuggets filter rail, now collapsible.
> All scraping for this test ran into a **separate database** (`data/scope-test.db`) with its own config, so the production archive and the hourly cycle were untouched. It is calibration, not a YU-01 deliverable.

---

## 1. What was added and scraped

**33 Reddit sources now** (was 18). Three waves, each with a written reason, all marked *proposed, not yet measured* in `config/sources.yaml` until their own data comes back.

| Wave | Communities | The thesis being tested |
|---|---|---|
| **1** | r/msp · r/nocode · r/Entrepreneur *(+ measured r/smallbusiness, r/sysadmin)* | decision-makers who buy automation |
| **2** | r/automate · r/zapier · r/ecommerce · r/shopify · r/sweatystartup · r/Accounting · r/agency · r/SaaS | people who **already pay** for automation and hit its ceiling, plus owner-dense verticals |
| **3** | r/excel · r/googlesheets · r/Airtable · r/EntrepreneurRideAlong | the offer's literal trigger — "this spreadsheet eats my week" |

**Wave 3 is the one the evidence pointed at.** Waves 1–2 were chosen by *who* posts; wave 3 by *what they post about*. r/excel is the largest manual-process community on the site: every thread is someone doing by hand what software should do, and they arrive already knowing the work is wrong.

---

## 2. Wave 1 result — measured, 723 records

| community | posts | buyer | practitioner | comments | buyer | practitioner |
|---|---|---|---|---|---|---|
| r/Entrepreneur | 13 | **2** | 2 | 177 | 1 | 1 |
| r/smallbusiness | 8 | **1** | 1 | 23 | 0 | 0 |
| r/msp | 15 | 0 | 0 | 207 | 1 | 0 |
| r/sysadmin | 14 | 0 | 0 | 196 | 0 | 0 |
| r/nocode | 11 | 0 | 1 | 59 | 0 | 0 |

**5 buyer signals from 61 posts + 662 comments (0.7 %)** — and reading all five, most are false positives:

- *"Is EOS a scam?"* (r/msp) — a book discussion; "our business" + "integration" fired.
- *"I run a solo dev agency… looking to gain hands-on experience"* (r/Entrepreneur) — **another agency selling its services.** Supply side, wearing owner language.
- *"What's one part of running your small business you wish you could automate?"* — a survey post, not a buyer.

**r/msp and r/sysadmin produced zero in 400 records.** They hold budgets, but they are practitioners: they buy *tools*, and they build the glue themselves. Drop them from the buyer list; keep r/sysadmin for content.

---

## 3. Two findings that matter more than the community list

### 3.1 The best buyer signals are in comments — and the prefilter deletes them

The single most promising thread found was *"What's one part of running your small business you wish you could automate?"*. Its replies are business owners naming, in their own words, work they would pay to remove. That is the raw material Seif's outreach needs.

**Three replies survived out of a whole thread.** `prefilter_min_chars: 40` and `prefilter_min_words: 6` drop anything shorter — and the answer to "what would you automate?" is *"chasing late invoices"*: 21 characters, three words. The pipeline throws away the highest-value evidence in the corpus before anything sees it.

That prefilter is correctly tuned for the **nugget** pipeline, which feeds an LLM extractor that needs prose. It is wrong for a **buyer** pipeline, where terseness is a feature. YU-01's collector must not inherit it.

### 3.2 Terse answers need their thread for context

A reply saying "invoicing" is meaningless alone and damning under *"what would you automate in your small business?"*. The classifier scores each record in isolation, so it cannot see that. The fix is **thread context**: when a parent post establishes a buyer frame, its replies inherit the owner voice.

This is a design change for the collector, not a phrase-list tweak, and it is the highest-value change on the list.

---

## 4. The end-to-end journey, as it stands today

### Data path

```
config/sources.yaml          33 reddit sources, each with a reason and a measured/proposed flag
      │  jester ingest --only <names> --db <db> --config <dir>
      ▼
Go worker (cloakserve)       walks r/<sub>/new/ with ?after= cursors, ±30% jitter,
                             block-page detection, thread_state skip
      │  per thread: <shreddit-post> meta + <shreddit-comment> bodies
      ▼
prefilter                    min/max chars, min words, emoji/mention caps   ← §3.1 loses buyer signals here
      │
      ▼
ingest_batch                 kind='comment', comments JSON, thread_meta JSON (title, body, score, community…)
      │
      ├─► nugget pipeline    extractor → archivist → synthesizer → critic   (the existing product)
      │
      └─► YU-01 classifier   signals.filters.classify() → buyer | practitioner | none
                             + reason + confidence, deterministic, no model
      │
      ▼
problem_signal               one row per post: source id/url, community, query, excerpt,
                             first/last seen, revisions, audience, run status   ← fixture-only today
      │  jester signals export
      ▼
CSV + manifest.json          counts split buyer/practitioner, DB↔CSV reconciliation
```

### Site path (what a person does)

1. **Overview** — headline says what changed today; the infrastructure card says whether cloakserve is up, which decides whether Reddit can be fetched at all.
2. **Sources** — the 33 communities, each with its fetch path (`api` / `browser`) and a **blocked** flag when cloakserve is down. This is where a community gets added or parked.
3. **Run** — `▶ run pipeline` (or `⇣ ingest`) with a source picker; the run strip in the top bar shows it working from any page.
4. **Runs** — the ledger: platform mix, warnings, and *why* a run kept nothing.
5. **Queue** — what was fetched and is waiting, comments and listings counted apart; open a batch to read its comments in the drawer.
6. **Nuggets** — the archive, narrowed by the facet rail (§5).
7. **Search / Clusters / Ideas** — meaning-based retrieval, themes, and the scored ideas.

### Verified in the browser today

| Step | Result |
|---|---|
| Nuggets loads | 1–100 of 79,856 · rail shows platform / community / category with counts |
| Click `platform: reddit` | 2,600 match, chip appears, community list narrows to reddit communities, **2.5 s** |
| Collapse the rail | rail hidden, chips persist, table widens 898 px, choice remembered |
| Impossible filter combo | *"No nugget matches these filters — 80,032 in the archive"* + **clear the filters** button |
| `/api/nuggets/page` | **3.9 s → 1.8 s**, payload **205 KB → 47 KB** |

---

## 5. The filter rail is collapsible now

The rail was permanent furniture taking 230 px of a page whose job is reading long insights. Now:

- **`hide filters` / `filters` toggle** with a state remembered per browser, defaulting to open on wide screens and shut below 900 px.
- **Active filters live outside the rail**, as removable chips (`platform reddit ✕`) plus `clear all`. This is the part that makes collapsing safe: a hidden filter can never silently shape what you are reading.
- The list gets the width back when the rail is shut.

**Two defects found while building it:**

1. **The empty state lied.** With filters matching nothing it said *"No nuggets archived yet — run pipeline from Overview"* on an archive of 80,032, because it decided from the *filtered* count. It now distinguishes "nothing here" from "too narrow" and offers the actual fix.
2. **The page was slow because of what it sent.** 100 rows × 44 columns = 205 KB, and the table renders 9 of those columns; `raw_text` alone was two thirds of the payload. Trimmed to what is rendered, plus five facet indexes created by the app itself (630 ms of SQL per click → 150 ms).

---

## 6. What I would tell Seif

1. **The community list matters more than the classifier, and wave 3 is the bet.** r/excel, r/googlesheets and r/Airtable are where people describe manual work as their own problem, in their own words, at volume. r/msp and r/sysadmin scored zero in 400 records — they are practitioners with budgets, not buyers.
2. **The gold is in comment threads, not posts** (§3.1), and the current prefilter deletes it. That is a pipeline change, and it is worth more than adding ten more subreddits.
3. **Nothing here is a lead.** The output is buyer *language* — the words an owner uses about work that is costing him — which is what makes a first message sound like it was written by someone who has met the problem.
4. **Still the blocker:** commercial Reddit API access, owner Seif, status due 2 Oct, access-or-replacement 9 Oct. Everything above was collected through the browser collector for calibration only, into a throwaway database, and is explicitly not a YU-01 artifact.
