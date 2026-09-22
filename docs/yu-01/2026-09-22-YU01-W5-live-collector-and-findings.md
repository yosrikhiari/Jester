# YU-01 — a live collector that runs today, and what 461 real records say

> **2026-09-22.** The collector now runs **live against an approved source**, end to end: search → classify → dedupe → SQLite → ClickHouse → CSV → digest → console. 461 records collected, 0 failures, a second identical run added 0 rows.
>
> The source is **Hacker News**, not Reddit. That is the 9 Oct fork, built before it is due. Reddit still needs approved commercial access, and the Reddit collector still does not exist — on purpose.

---

## 1. Why Hacker News, and why now

The delivery plan's 9 Oct fork says: if commercial Reddit access is not approved, the hours move to *a named approved source*, and it named Hacker News first. Its search API is public, keyless and documented, so it can be built and proven **before** the decision rather than after it.

That changes the 9 Oct conversation from a week of drift into a five-minute confirmation, and it costs nothing if access *is* approved: the field map, filters, fixtures, ClickHouse load, digest and console are all source-agnostic. Only `signals/sources/` ever changes.

**Reddit is deliberately absent as code.** `jester signals sources` says so out loud rather than leaving a gap someone helpfully fills:

> reddit — NOT BUILT ON PURPOSE. The free Data API is non-commercial and this work is commercial… A path that cannot legally run must not exist as code that can be called by accident.

---

## 2. Search, not listing walks — now measured on a second platform

W3 concluded that walking `/new/` is the wrong collection mode and searching for the sentences a buyer writes is the right one. The collector implements that: **ten literal phrases**, not three community names.

```
"we do it manually"        "zapier broke"              "worth paying someone"
"by hand every week"       "outgrown our spreadsheet"  "hire a developer or"
"copy paste between"       "does not scale anymore"    "build it in house or buy"
"hours a week on spreadsheets"
```

Seif's decision is now **which phrases**, not which communities. Phrases are cheaper to test, faster to swap, and double as Omar's opening lines. The digest names every phrase that returned nothing, so the dead ones are visible rather than quietly carried.

---

## 3. What the first live run found — and the finding that matters

461 records, 10 phrases, 0 errors. **6 scored buyer. Four of those six were vendors.**

| what it actually was | example |
|---|---|
| a YC launch post | *Launch HN: Rudus (YC P26) — AI for concrete contractors* |
| a YC founder's Show HN | *Show HN: Praxos — Multiplayer AI* |
| a developer showing off a side project | *I run a fully automated local-LLM news station on a single RTX 3090…* |
| a developer's essay about their own i18n system | *Vibe coding did not kill making money out of engineering* |

### Why phrase matching cannot see this on its own

**A vendor's pitch contains the buyer's words, on purpose.** Rudus's launch post says concrete contractors spend hours a week doing manual takeoffs on spreadsheets — a perfect buyer sentence, quoted by the person selling the cure. The vocabulary really is there.

What separates them is the **frame**: a launch post addresses an audience about a product; a buyer addresses a room about a problem. So the marker has to be structural, not vocabulary — and on Hacker News the structure is explicit and conventional. `Show HN` and `Launch HN` are *defined by the site* as product announcements, which makes this the one place a frame can be read off the text with certainty.

Hence a new `vendor_pitch` family, weighted heavier than `supply_side` (−0.60 vs −0.50) because it is more certain: a freelancer might also be a buyer of something else; a launch post never is.

### The before/after, and a mistake worth recording

The first comparison I ran looked like a triumph — buyer 6 → 1, practitioner 35 → 10 — and it was **wrong**. It compared new verdicts against the *stored* ones, and the stored verdicts were computed from the whole post while the archive keeps only a 600-character excerpt (retention §14). So the difference mixed two unrelated causes: the rules changed, and the evidence got shorter. Reading that as "my rule change did this" credits the rules with work truncation did.

Scored properly — both rule sets over the same stored text:

| | buyer | practitioner | none |
|---|---|---|---|
| before (v2) | 1 | 11 | 444 |
| after (v3) | 1 | 10 | 445 |
| **changed** | | | **1 record** |

`jester signals reclassify` now refuses to pretend: without `--baseline` it prints `comparable: false` and says why.

### The defect that comparison exposed

**The archive could not reproduce its own verdicts.** A record said *"buyer signal: owner_voice + work_pain"* while the excerpt beside it contained neither phrase, because both were at character 900. A reviewer checking that claim finds nothing and concludes the classifier is broken.

Fixed by storing the **words**, not just the family names:

> `buyer signal: owner_voice + work_named, asking [matched: i run a, reporting, how do you, anyone else]`

Now a verdict is checkable against the source link, which is the evidence the card actually asks for. A test asserts every phrase named is a phrase the record contains.

---

## 4. The conclusion Seif needs

**Neither Reddit's business communities nor Hacker News contain buyers at a useful density**, and they fail for opposite reasons:

| | what the room is | buyer rate |
|---|---|---|
| Reddit business subs | people asking how to *start* or *market* a business | 0.65% (8/1 236), most false positives |
| Hacker News | practitioners who build it themselves, and vendors launching | 0.66% (3/455), 2 of 3 arguable |

The practitioner column is the other half of the story and it is real: **32 practitioner signals** with source links, which is content material for Rayen from exactly the rooms that produce no buyers.

**What this says about the plan:** the collection machinery works — it searches, classifies deterministically, dedupes, recovers, reconciles and reports. The open question is no longer *can we collect* but *where do buyers actually write*. Developer and startup forums are the wrong population on both platforms, and that is now measured rather than assumed on 1 691 real records.

---

## 5. What was built

| Piece | Where | Gate |
|---|---|---|
| Source layer + paperwork (`access`, `terms_url`, `rate`) | `signals/sources/__init__.py` | "do not bypass limits" |
| Hacker News search collector, paced, backing off on 429/5xx | `signals/sources/hackernews.py` | W5 branch B |
| Run loop + `signal_run` ledger | `signals/run.py` | "counts saved", "zero-result run is valid" |
| Failed query stored as a counted record, recoverable | `signals/run.py` | "silent failures" on the Fail list |
| Recovery pass | `signals/run.py` | "3 runs + 1 recovery pass" |
| `reclassify` with a real baseline | `signals/run.py` | W8 "before/after compared" |
| Weekly digest: repeated needs, links, counts, unknowns | `signals/digest.py` | W7 (3–9 Nov) |
| Console page: tiles, run ledger, facets, per-record reasons | `console/` | it has to be *readable*, not just queryable |

**A bug the tests caught:** recovery never marked anything recovered. It asked the table *"is this query still failing?"* — and the old failure row is still there, still saying `error`, because removal is recorded and never deleted. Recovery now decides from the pass it just ran.

---

## 6. State of the task

| Gate | Due | State |
|---|---|---|
| W1 scaffold | 28 Sep | ✅ 22 Sep |
| W2 scope/access + filters | 2 Oct | ✅ 22 Sep |
| W3 20 fixture cases | 9 Oct | ✅ 22 Sep |
| W4 ClickHouse + saved SQL | 16 Oct | ✅ 22 Sep |
| **W5 live runs** | **23 Oct** | ✅ **on the approved replacement source** — 2 runs, 0 errors, repeat adds 0 rows. Reddit still blocked. |
| W6 first real batch | 2 Nov | ✅ done in substance — 455 records with links |
| W7 weekly digest | 9 Nov | ✅ built and generated |
| W8 one evidenced fix | 16 Nov | ✅ `vendor_pitch`, with a clean A/B and the truncation defect fixed |
| W9 handover | 20 Nov | not started |

**Unchanged blocker:** commercial Reddit API access and the phrase list — **owner Seif**, status **2 Oct**, access-or-replacement **9 Oct**. My recommendation for 9 Oct is now backed by a working collector rather than a plan: **confirm Hacker News as the named replacement**, and spend the decision on *phrases and populations* instead of on access.
