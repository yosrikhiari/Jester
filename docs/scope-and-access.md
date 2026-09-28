# Problem-signal collector — scope and access status

> **Generated 2026-09-28** from `config/signal_rules.yaml`, the collector's field map and the live archive. Not typed by hand: if the rules change, this changes with them.
> **Scope status: PROVISIONAL — awaiting sign-off**

## 0. What is collecting today

Sections 1 and 2 are the PROPOSED scope and need a decision. This section is what the collectors actually read right now, and it needs no decision from anyone — every source below runs on a public keyless API, so none of it waits on the access question in section 6.

| collector | platform | authority it runs on |
|---|---|---|
| `hackernews` | hackernews | public, keyless search API published by Algolia for Hacker News; no account, no commercial restriction stated |
| `hackernews-hiring` | hackernews | public, keyless search API published by Algolia for Hacker News; the adverts are posted publicly by the companies themselves |
| `himalayas` | himalayas | free public jobs API, no key; Himalayas asks for a link back to the listing and a credit, and forbids resubmitting its jobs to other job sites |
| `jobicy` | jobicy | free public remote-jobs API, no key; Jobicy permits commercial use for tools and AI products, asks that the listing URL and a credit are kept, and automated checks at most hourly |
| `remotive` | remotive | free public remote-jobs API, no key; Remotive asks for a link back and a credit, at most 4 calls a day, and forbids resubmitting its jobs to other job sites |

They ask different questions and carry different rule sets. One reads forum discussion for people describing a problem; the others read hiring adverts, where a company states what it will pay for.

### What each source has produced

Read off the live archive on 2026-09-28. *Buyers* are records the rules scored as wanting contract engineering work; *recent* means posted on or after 2026-07-30.

| source | records | buyers | recent buyers |
|---|---:|---:|---:|
| HN Who is hiring | 3,000 | 149 | 27 |
| Himalayas (contract listings) | 1,471 | 1,122 | 1,122 |
| Jobicy | 22 | 15 | 15 |
| Remotive | 1 | 1 | 1 |
| HN discussion (Ask HN, comments, Show, stories) | 756 | 3 | 3 |
| Reddit hiring rooms (worker archive, set apart) | 71 | 10 | 10 |

**Proposed source: HN "Who is hiring" plus the three job boards.** In the last 60 days they produced **1,165 buyer signals from 554 named companies**, on free public APIs that need no access decision. Approving them as the source settles section 6 without a purchase; the Reddit rooms in sections 1 and 6 stay available as a measured alternative.

## 1. Communities

The scope owner owns this list: it defines the buyer test. Every line says whether it was **measured** against posts already in the archive or is **proposed** and still unverified, so nothing here is an assumption wearing a recommendation's clothes.

### Buyer sources — what outreach can act on

| community | why | evidence |
|---|---|---|
| `r/Entrepreneur` | founders describing operational work before they hire for it | measured — 13 posts + 177 comments: 3 buyer, 9 practitioner. The highest buyer count of the eleven measured. |
| `r/sweatystartup` | owners of physical/service businesses naming back-office toil | measured — 8 posts + 107 comments: 2 buyer, 3 practitioner. Service-business owners, the closest match to the offer. |
| `r/ecommerce` | store owners hitting the ceiling of their apps and integrations | measured — 11 posts + 59 comments: 1 buyer, 8 practitioner. Strongest practitioner yield of any buyer-side room. |
| `r/msp` | managed service providers buy automation continuously | measured — 15 posts + 207 comments: 1 buyer, 1 practitioner. Low rate, but the posters hold budgets. |
| `r/agency` | agency owners describing delivery work they cannot absorb | measured — 9 posts + 144 comments: 1 buyer, 7 practitioner. Mixed: many posters sell services rather than buy them. |

### Content sources — content material, explicitly not leads

| community | why | evidence |
|---|---|---|
| `r/zapier` | the richest 'the no-code fix stopped scaling' material, and zero leads | measured — 14 posts + 68 comments: 0 buyer, 8 practitioner. People who automate and hit the tool's ceiling. |
| `r/ExperiencedDevs` | delivery-org commentary for content; NOT an outreach source | measured — 14 archived posts, almost all career/resume/manager questions; one usable content signal |
| `r/devops` | practitioner pain for content | measured — 14 archived posts: CI questions, tool choices, CV reviews |

**Buyer-source count: 5** (the task asks for 3-5).

### Measured and NOT recommended

Listed so the decision is auditable rather than a silent omission.

| community | why not |
|---|---|
| `r/selfhosted` | measured — 23 archived posts; hobbyists who build it themselves. Anti-buyer by definition. Kept for: content only — one strong cost-comparison post ('Migrated 42 workflows from Zapier to self-hosted n8n, $248/mo down to $7.70/mo') |
| `r/productivity` | measured — 15 archived posts, all personal productivity (study habits, ADHD, screen time). No business signal. |
| `r/homelab` | measured — 8 archived posts; hobby hardware. |
| `r/NoStupidQuestions` | measured — 23 archived posts; general-purpose, no delivery signal. |
| `r/personalfinance` | measured — 16 archived posts; personal money, wrong audience entirely. |
| `r/sysadmin` | measured — 14 posts + 196 comments: 0 buyer, 0 practitioner. The largest sample of any candidate and the emptiest. Previously proposed as a buyer source on the strength of tool-selection posts; collecting it disproved that. |
| `r/Accounting` | measured — 12 posts + 25 comments: 0 buyer, 0 practitioner. Professionals discussing their profession, not firms buying software. |
| `r/smallbusiness` | measured — 8 posts + 23 comments: 0 buyer, 1 practitioner. Was the original lead candidate; the live sample did not support it. Kept for: re-test if the search phrases change — the room is right, the sample was thin |
| `r/nocode` | measured — 11 posts + 59 comments: 0 buyer, 4 practitioner. The 'hit the tool ceiling' thesis held for content and not for buying. Kept for: content only |
| `r/shopify` | measured — 8 posts + 48 comments: 0 buyer, 5 practitioner. Kept for: content only |
| `r/devopsjobs` | measured — 12 scored posts, 0 buyers: full-time roles only (worker archive, 23-27 Sep) |
| `r/jobbit` | measured — 2 scored posts, both months old, 0 buyers (worker archive) |
| `r/remotejs` | proposed, never measured — no posts in the worker's archive |
| `r/hiring` | proposed, never measured — listed in the worker's sources, no scored posts yet |

## 2. Queries

- `we do it manually`
- `by hand every week`
- `copy paste between`
- `hours a week on spreadsheets`
- `zapier broke`
- `outgrown our spreadsheet`
- `does not scale anymore`
- `worth paying someone`
- `hire a developer or`
- `build it in house or buy`

Queries are applied per community. A run records the query that matched each record, so a change of targeting is visible in the data rather than only in a config diff.

## 3. Fields

30 fields, one field map driving the SQLite table, the ClickHouse table and the CSV header.

| field | sqlite | clickhouse | meaning |
|---|---|---|---|
| `record_id` | TEXT | String | our key: <platform>:<source_id> |
| `mode` | TEXT | LowCardinality(String) | live \| synthetic — never inferred, always stored |
| `platform` | TEXT | LowCardinality(String) | hackernews \| himalayas \| remotive \| jobicy — the public APIs the collectors read; reddit only for rooms scored from the worker's archive (no Reddit requests) |
| `source_id` | TEXT | String | the platform's own id (HN item number, board listing id, Reddit post id) |
| `source_url` | TEXT | String | permalink; the evidence link that travels with the signal |
| `community` | TEXT | LowCardinality(String) | hn/comment \| hn/ask \| hn/show \| hn/story \| hn/hiring \| himalayas/contract \| remotive/jobs \| jobicy/jobs \| a Reddit hiring room from the archive, set apart pending access |
| `kind` | TEXT | LowCardinality(String) | post \| comment |
| `author` | TEXT | String | handle as published; never enriched, never contacted |
| `title` | TEXT | String | post title, empty for a comment |
| `excerpt` | TEXT | String | permitted text, truncated to EXCERPT_CHARS |
| `text_hash` | TEXT | String | sha1 of the full permitted text — how an edit is detected |
| `query` | TEXT | String | the community/search term that matched this record |
| `match_reason` | TEXT | String | why it was kept, in words a human can check |
| `match_confidence` | REAL | Float32 | 0–1; the classifier's own confidence, not a score |
| `audience` | TEXT | LowCardinality(String) | buyer (outreach can act on it) \| practitioner (content material) \| none |
| `relevant` | INTEGER | UInt8 | 1 kept as a problem signal, 0 collected and rejected |
| `company` | TEXT | LowCardinality(String) | 'unknown' unless the source states it (a job advert names its company) |
| `buyer_intent` | TEXT | LowCardinality(String) | 'unknown' for a post; a hiring advert's own engagement words |
| `created_utc` | TEXT | Nullable(DateTime64(3)) | when the author posted it |
| `edited_utc` | TEXT | Nullable(DateTime64(3)) | when we first saw the text change |
| `removed_utc` | TEXT | Nullable(DateTime64(3)) | when the source stopped returning it |
| `first_seen_utc` | TEXT | DateTime64(3) | our first sighting — never overwritten |
| `last_seen_utc` | TEXT | DateTime64(3) | our latest sighting |
| `revisions` | INTEGER | UInt16 | how many times the text changed under us |
| `run_id` | TEXT | String | the run that last touched this row |
| `run_status` | TEXT | LowCardinality(String) | ok \| error |
| `error` | TEXT | String | what went wrong on this record, empty when ok |
| `outcome` | TEXT | LowCardinality(String) | what came of it: '' untouched \| contacted \| replied \| meeting \| won \| no \| unfit |
| `outcome_at` | TEXT | Nullable(DateTime64(3)) | when the outcome was recorded |
| `outcome_note` | TEXT | String | one line from whoever worked it; empty is fine |

## 4. What is kept, and for how long

- **Excerpt only**: 600 characters of permitted text per record. Full text is hashed for edit detection and not stored.
- **Identities**: handles as published; never enriched, never contacted.
- **Removal**: recorded, not deleted; text pruned by the archive's retention sweep. Counts stay reconcilable; the archive's retention sweep prunes the text on its own schedule.
- **Never inferred**: company and buyer intent. Both stay `unknown` unless the source states them — a job advert names its company and its engagement (contract, part-time); a forum post does not, and no code path guesses. A post is a signal, never evidence of a budget.

## 5. Classification

Deterministic rules in 15 families (`owner_voice`, `capacity`, `cost`, `work_pain`, `needs_built`, `work_named`, `asking`, `career`, `personal`, `hobby`, `supply_side`, `opinion_essay`, `advice_giving`, `showcase`, `vendor_pitch`). A family counts once however many of its phrases matched, so a long post cannot out-score a precise one.

Every record is labeled for **who it is for**, because this task feeds two different people and they do not want the same posts:

- **buyer** — a business voice *and* work that software could remove. At or above **0.6**; within **0.15** below it the record is kept and flagged ambiguous. A buyer signal requires ownership language: an employee says "our company" as readily as an owner does.
- **practitioner** — delivery pain from someone who builds. At or above **0.45**. Content material; **never** handed to outreach as a lead.
- **none** — collected and rejected, with the reason stored so the rejection can be audited against the post.

No model sits in the acceptance path: the fixture gate is deterministic checks, and a gate that depends on a model's mood is not a gate.

## 6. Access — the decision this document exists for

**Required:** approved commercial Reddit API access, in writing, covering both data storage and the intended AI processing.

**Why it is not free:** Reddit's free Data API tier is for non-commercial use. This work is commercial — the signals feed a buyer test and published content — so it needs a paid agreement. Confirm the current terms and pricing with Reddit when applying; they have changed more than once since 2023.

**Requested so far:** none on record. This project holds no application to Reddit and no quote; whether to apply, and for what budget, is part of the decision below.

**Who decides:** Seif (scope owner), **due 2026-10-02**. The choice is between two things: approve the free sources already running (section 0), or buy Reddit access for the rooms below.

**If access is bought, these are the rooms to point it at — measured, and thin.**

Every room in section 1 is a problem-DISCUSSION room, and those yielded almost no buyers. The same split holds on Hacker News: discussion rarely carries a buyer, hiring adverts often do — same classifier, same archive. So the rooms worth paying for are Reddit's hiring rooms, and these are the ones that produced buyers when measured.

| room | why | status |
|---|---|---|
| `r/techjobs` | [Hiring] posts with stated hourly rates, remote and worldwide | measured — 6 scored posts, 2 buyers (21-27 Sep, worker archive) |
| `r/DevsForHire` | [Hiring] posts for full-stack and QA work, rates stated | measured — 5 scored posts, 2 buyers (23-24 Sep, worker archive) |
| `r/forhire` | [HIRING] and [FOR HIRE] posts; the closest Reddit analogue to a Who-is-hiring thread | measured — 1 scored post, 1 buyer (26 Sep, worker archive); thin |
| `r/MachineLearningJobs` | ML and data roles, some on contract | measured — 12 scored posts, 1 buyer (23-28 Sep, worker archive); mostly full-time roles |
| `r/hiredev` | developer hiring posts | measured — 10 scored posts, 1 buyer (23-27 Sep, worker archive); mostly full-time roles |

How they were measured: Jester's worker had already stored these rooms' posts by browser, and `jester signals from-archive` scored that archive without sending Reddit a single request. Those records are **set apart and not used** until this decision is made. **The benchmark they have to beat is already running and costs nothing** — HN "Who is hiring" and the job boards, on keyless public APIs (section 0). If paid access cannot beat free, the answer is not to buy it.

**Open decisions**, none of which this repository can make for you:

- Approve HN "Who is hiring" plus the job boards as the replacement source (section 0) **or** choose 3-5 Reddit communities and approve the commercial access spend.
- If Reddit: choose the queries (section 2), apply for access and name the budget.
- Provide a ClickHouse instance and credentials scoped to this collector, or confirm the self-hosted one.

Done: the data-design review (Rassil, 25 Sep 2026) — ClickHouse holds insert and query only; no defects raised.

**Until access exists, the collector sends Reddit no requests.** Reddit is not in the collector's source registry, so `jester signals run --source reddit` fails with "no source named 'reddit'" rather than quietly collecting something it should not. The only Reddit records are the hiring rooms above, scored from what the worker had already archived, and they are set apart until this decision. `jester signals sources` prints every collector that does exist and the authority each one runs on.

Live collection against **approved** sources is already built and running, and every record carries a `mode` column saying `live` or `synthetic` — so a synthetic row can never be counted as a live one, whichever source it came from.

## 7. If access is refused or late

Everything above the fetch layer is source-agnostic: the field map, the classifier, the fixtures, the ClickHouse load, the digest and the handover do not care where a record came from. Only the collector changes. Pre-named alternatives, each with a working adapter in this repo:

| source | access | why it substitutes |
|---|---|---|
| Hacker News (Algolia Search API) | public, keyless, documented | Ask HN is the densest software-delivery pain feed in the source list; adapter already ingesting |
| Discourse instances (latest.json / t/<id>.json) | public per-instance JSON | one adapter reaches many forums; operator and practitioner pain |
| Stack Exchange (official API) | public, documented, quota'd | explicit problem statements by construction |
| Lemmy (public API) | public | Reddit-shaped discussion without the terms question |

**Recommendation:** name the fallback *with* the access decision rather than after it. Then the switch is a confirmation, and every date downstream holds either way.

## 8. Cost

| line | estimate | note |
|---|---|---|
| Reddit commercial API | unknown — the scope owner to obtain | the single unpriced item; a quote is required before committing |
| ClickHouse | 0 | self-hosted in Docker beside the existing services unless an instance is provided |
| Build hours | the bulk of the estimate remains | the scaffold is already built |
| Operation after release | ≤ 4 h/week | the task's own cap; surplus hours are surfaced, not absorbed |

