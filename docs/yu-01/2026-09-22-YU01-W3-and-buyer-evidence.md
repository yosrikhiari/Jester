# YU-01 — W3 gate delivered, and what 1 236 real records say about Reddit

> **2026-09-22.** Two things: the **9 Oct gate (20 labeled fixture cases) passes**, seventeen days early; and the buyer-source question now has an answer measured across **123 posts and 1 113 comments from 11 communities**, scraped live.
> All scraping ran into a throwaway database (`data/scope-test.db`) with its own config. Calibration, not a YU-01 artifact.

---

## 1. W3 gate — 20/20, seventeen days early

`jester signals check` → **20 cases, 5 categories, all pass, exit 0.**

| category | 4 cases each | what they prove |
|---|---|---|
| **relevant** | toil with a number · software that doesn't exist · tool ceiling · terse answer in a thread | the four shapes a buyer signal actually takes |
| **irrelevant** | hobbyist · supply-side · industry essay · recruiting | every one is a **real false positive** the classifier produced against archived posts, pinned so it cannot come back |
| **ambiguous** | owner with no cost · thin practitioner · inherited voice · cost without a voice | the records where evidence is real but thin — kept, flagged, never silently promoted |
| **edits/deletions** | unchanged ≠ duplicate · edit dated once · removal recorded not deleted · removal that comes back | facts about a row **across runs**, checked against a real table |
| **failures** | 503 · rate-limited · malformed · blocked page | "silent failures" is on the card's Fail list; each leaves a counted `run_status=error` row |

**The cases are data, not code** (`tests/fixtures/signals/*.json`), each carrying a `why`. A claim is easier to argue with when it isn't buried in a test function.

**The harness grades its own coverage.** A set that quietly lost its failure cases, or shrank to three, still reads 100 % — so `check` fails on a missing category or a short set, and 12 tests feed it deliberately broken sets to prove it complains.

The four **replay** cases each get their own temporary database, because they assert on total row count and one leaking into another would make the whole category pass or fail together.

---

## 2. Thread context — built, measured, then reined in twice

**The finding that prompted it:** the densest buyer evidence in the archive sits under posts like *"What's one part of running your small business you wish you could automate?"*. Each reply is an owner naming work they'd pay to remove — and each reply, read alone, is three words with no ownership language at all. The thread is what says who is speaking.

So `classify()` now takes the parent post. When the parent is a **buyer frame** (owner voice **and** asking the room), a reply inherits the voice it never stated — at a lower weight, flagged `inherited`, because borrowed evidence is weaker evidence.

Then the measurement pushed back, twice:

| Round | Buyer signals found | What reading them showed |
|---|---|---|
| context on | **16** | every one an advisor, an investor, a vendor's AMA reply or a joke. In a survey thread most replies *advise the asker*; only a minority answer about themselves. |
| + `advice_giving` negative and a length cap (an answer is short, advice is long) | **9** | better, but a "solo dev agency… looking to gain hands-on experience" survived — supply side wearing owner language |
| + `dev agency` / `experienced developer` / `open to work` as supply-side | **8** | current state |

Also added from the evidence: **`work_named`**, the vocabulary of real answers (invoicing, quoting, scheduling, payroll, reconciliation…), because `work_pain`'s words (manual, by hand, spreadsheet) fire on none of them; and **`frame_phrases`**, second-person address used *only* to detect a frame and never as a record's own voice — "your business" is what a survey post says and also what an advert says.

---

## 3. The answer on Reddit, measured

11 communities scraped live, 123 posts + 1 113 comments:

| community | posts | buyer | practitioner | comments | buyer | practitioner |
|---|---|---|---|---|---|---|
| r/Entrepreneur | 13 | 3 | 3 | 177 | 0 | 6 |
| r/sweatystartup | 8 | 1 | 0 | 107 | 1 | 3 |
| r/ecommerce | 11 | 1 | 6 | 59 | 0 | 2 |
| r/msp | 15 | 0 | 0 | 207 | 1 | 1 |
| r/agency | 9 | 0 | 2 | 144 | 1 | 5 |
| r/zapier | 14 | 0 | 5 | 68 | 0 | 3 |
| r/nocode | 11 | 0 | 2 | 59 | 0 | 2 |
| r/shopify | 8 | 0 | 2 | 48 | 0 | 3 |
| r/smallbusiness | 8 | 0 | 1 | 23 | 0 | 0 |
| r/sysadmin | 14 | 0 | 0 | 196 | 0 | 0 |
| r/Accounting | 12 | 0 | 0 | 25 | 0 | 0 |
| **total** | **123** | **5** | **23** | **1 113** | **3** | **25** |

**0.65 % buyer rate — and reading all eight, most are still not buyers of engineering.** They are owners with *business* problems (a founder in trouble, a construction owner weighing his first hire, someone asking about sales tax) rather than process problems software removes.

### Why, and it is not the classifier

Reddit's business communities are dominated by people asking how to **start** or **market** a business. Established operators describing process pain they would pay to remove are rare, because that is not what those rooms are for. r/sysadmin and r/Accounting produced **zero buyer signals in 447 records** — they are practitioners who build the glue themselves.

The practitioner column tells the other half of the story: **48 practitioner signals**, real content material for Rayen, from exactly the communities that produce no buyers (r/Entrepreneur 6, r/agency 5, r/zapier 5, r/ecommerce 6).

---

## 4. The conclusion that changes the plan

**Walking `/new/` is the wrong collection mode for buyer signals.** It samples whatever a community posted today, and what a community posts today is mostly not a buyer. Every tightening I made raised precision; none of them can raise a base rate that is near zero.

**The right mode is search.** Instead of "read r/smallbusiness", ask Reddit for the sentences a buyer writes:

- `"we do it manually" subreddit:smallbusiness`
- `"worth paying someone"`, `"hire a developer or"`, `"tried zapier" broke`
- `"spreadsheet" "hours a week"`

That is a targeted query against the whole site rather than a depth-first walk of one room — the same number of requests, a far higher hit rate. YU-01's card already lists **search terms** as an input alongside communities; the collector should treat search as the primary mode and listing walks as the fallback.

**Two consequences:**

1. **Reddit's search endpoint needs the API.** The browser collector cannot page search results reliably. This makes the 2 Oct access decision more load-bearing, not less.
2. **Seif's decision changes shape.** The question is no longer only "which 3–5 communities" but "which 5–10 *phrases*" — and phrases are cheaper to test, easier to iterate, and directly reusable as Omar's opening lines.

---

## 5. State of the task

| Gate | Due | State |
|---|---|---|
| W1 scaffold | 28 Sep | ✅ delivered 22 Sep |
| W2 scope/access + filters | 2 Oct | ✅ delivered 22 Sep |
| **W3 20 fixture cases** | **9 Oct** | **✅ delivered 22 Sep — 20/20, 5 categories, coverage self-checked** |
| W4 ClickHouse + saved SQL | 16 Oct | next; needs an instance (§6 OPEN-4) |
| W5 live runs | 23 Oct | blocked on access |

**Suite: 53 signals tests pass** (12 of them on the fixture harness itself), plus the console suite.

**Unchanged blocker:** commercial Reddit API access and the community/phrase list — owner Seif, status 2 Oct, access-or-replacement 9 Oct. Everything above was collected through the browser collector into a throwaway database, for calibration only.
