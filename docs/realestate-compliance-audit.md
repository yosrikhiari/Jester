# Real-Estate Compliance Audit Report

**Report Date:** 2026-09-03  
**Scope:** 7 regions, 12 portal profiles (Tunisia foundation + France + Spain + UK + Germany + Italy + USA)  
**Status:** ✅ Tunisia Phase 1 compliance verified. France Phase 2 audit in progress.

---

## Compliance Checklist (per site)

Before ANY scraper code for a site, audit the following:

| # | Checklist Item | Status | Notes |
|---|---|---|---|
| (a) | **Official API / feed availability** | — | Does the site provide an official API or data feed? If yes, document endpoint, auth method, rate limits, data coverage. If no, note that scraping is the only integration method. |
| (b) | **ToS restriction on automated access** | — | Does the Terms of Service prohibit or restrict automated access, scraping, or bot usage? Document specific clause references, any required attribution, rate limits implicitly allowed, and whether written permission has been obtained. |
| (c) | **Phone / contact-data consent (PII)** | — | Does the scraping process collect phone numbers or other personally identifiable information? Document consent framework, opt-in/opt-out mechanism, and whether phone data is stored, logged, or transmitted. GDPR/DPA consent-gate requirements apply. |
| (d) | **GDPR / DPA posture** | — | Classify the data handling: (i) scrape-only existing public data, (ii) new data generation, (iii) personal data inclusion. Document data retention policy, anonymization steps, and lawful basis for processing. |

---

## Per-Site Audit Records

### Region 1 — Tunisia (Foundation) ✅ COMPLETED

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Tayara.tn | None (scraping only) | ToS permits public content viewing; no explicit bot restriction found | Phone numbers not collected in current schema | Scrape-existing public data; no personal data | 1 |
| 2 | Mubawab Tunisia | None (scraping only) | ToS permits public content viewing | Phone numbers not collected | Scrape-existing public data | 2 |
| 3 | Tunisie Annonce | None (scraping only) | ToS permits public content viewing | Phone numbers not collected | Scrape-existing public data | 3 |
| 4 | Houni.tn | None (scraping only) | ToS permits public content viewing | Phone numbers not collected | Scrape-existing public data | 4 |

**Tunisia Build Order Matrix:** Ranked by `(compliance posture, data quality, effort)` = {1, 2, 3, 4}. All four sites passed compliance gate; Phase 1 (Tunisia) foundation verified.

---

### Region 2 — France (In Progress)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | **Leboncoin** | None (no public API). Multiple Apify actors confirm: "LeBonCoin doesn't offer a public API for property data extraction." Scraping only, proxy-dependent. | **EXPLICIT RESTRICTION.** robots.txt: "It's forbidden to use search robots or other automatic methods to access Leboncoin.fr. Access is only permitted with special permission from Leboncoin.fr." CGU also prohibits automated access without permission. ToS clause reference required. | Phone numbers may appear on listings; requires consent-gate for display/storage. Current schema does not collect phone (PII consent-gated field per normalized schema). | GDPR/DPA: Scrape-existing public data, but ToS restriction raises consent questions for phone numbers. Classify as "scrape-existing with PII caveat" pending clause review. | **2** (restrictive ToS lowers rank) |
| 2 | **SeLoger** | None (no public API). Apify actors exist but all note scraping-only integration; no official API published. | **RESTRICTED but less explicit.** robots.txt: "Disallow: /recherche?*" (search pages disallowed). Core CSS/JS and profiles allowed. No explicit "no robots" banner like Leboncoin. ToS CGU reviewed 2026-02-26 — no explicit bot prohibition found yet, but must verify. | Phone numbers may appear on listings; requires consent-gate for display/storage. Current schema has PII consent-gated field (seller.phone). | GDPR/DPA: Scrape-existing public data. robots.txt disallows search pages only; other pages appear accessible. Lower restrictiveness than Leboncoin. | **1** (less restrictive ToS, robots.txt only disallows search) |
| 3 | Bien'ici | None published. | robots.txt PERMITS the search path (`/recherche/achat/...`); disallows are query-shape only (`/recherche/*&*`, `/*tri=*`, `/*?mode=*`), and `/recherche/*?neuf=oui&page=*` is explicitly Allowed. | Not reached. | **Not buildable — consent-gated.** See below. | **n/a (closed)** |
| 4 | Logic-Immo | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 5 | ParuVendu | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

**France Compliance Audit — Detailed Findings:**

**Leboncoin (Priority 1):**
- [x] **(a) API/Feed:** None. No public API exists. All integrations are scraping-only. Apify actors confirm: "LeBonCoin doesn't offer a public API for property data extraction." Use French residential proxies for reliable scraping.
- [x] **(b) ToS Restriction:** **EXPLICIT.** robots.txt directly states: "It's forbidden to use search robots or other automatic methods to access Leboncoin.fr. Access is only permitted with special permission from Leboncoin.fr." CGU also prohibits automated access without permission. This is a **strong ToS restriction** that may require written permission before scraping.
- [x] **(c) PII Consent:** Phone numbers may appear on listings. Current Go schema has `seller.phone` as a PII consent-gated field (per normalized schema). Decision needed: scrape and consent-gate, or avoid phone numbers entirely.
- [x] **(d) GDPR/DPA Posture:** Scrape-existing public data, but ToS restriction raises consent questions for phone numbers. Classify as "scrape-existing with PII caveat." Lawful basis assessment needed before proceeding.

**SeLoger (Priority 2):**
- [x] **(a) API/Feed:** None. No official public API published. Apify actors provide scraping-only integration. Same situation as Leboncoin — scraping only, no API.
- [x] **(b) ToS Restriction:** **RESTRICTED but less explicit than Leboncoin.** robots.txt: "Disallow: /recherche?*" (search pages disallowed). Core CSS/JS and profiles/* are allowed. No explicit "forbidden robots" banner like Leboncoin's direct prohibition. ToS CGU (reviewed 2026-02-26) — no explicit bot prohibition found yet, but must verify full CGU text.
- [x] **(c) PII Consent:** Phone numbers may appear on listings. Same as Leboncoin — Go schema has `seller.phone` as PII consent-gated field per normalized schema. Decision needed.
- [x] **(d) GDPR/DPA Posture:** Scrape-existing public data. robots.txt only disallows search pages; other pages appear accessible. **Lower restrictiveness than Leboncoin.** GDPR assessment: publicly accessible listings, but phone numbers require consent-gate if collected.

**France Build Order Matrix (preliminary):** Ranked by `(compliance posture, data quality, effort)` = {SeLoger, Leboncoin}. SeLoger ranks higher due to less explicit ToS restriction (robots.txt only disallows search pages, not all automated access). However, both sites have no official API — scraping-only for both.

---

### Action Items (from plan Task FR-0.1)
- [ ] **FR-0.1** — Completed: For Leboncoin and SeLoger, each checklist item (a)–(d) documented above.
- [ ] **FR-0.2 — Verification** — **Pending:** Reviewer confirms the audit records, for each France site: (a)–(d) above, and the build-order matrix is ranked by `(compliance posture, data quality, effort)`. Matrix may re-order the priority pair based on findings.

**Expected outcome of FR-0.2:** The matrix may keep SeLoger as rank 1 (less restrictive) or elevate Leboncoin if data quality/effort factors outweigh the ToS restrictiveness. Either ranking is valid per the plan's `(compliance posture, data quality, effort)` formula.

---

### Region 3 — Spain (Not Started)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Idealista | — | — | — | — | — |
| 2 | Fotocasa | None published. | robots.txt allows page 1 of a result list but **forbids all pagination** (`/*/l/2*` … `/*/l/39*`), the three largest cities (`/madrid/`, `/barcelona/`, `/valencia/`) and every price/room filter. | Not reached. | **Not buildable — list never populates.** See below. | **n/a (closed)** |
| 3 | Habitaclia | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

### Region 4 — UK (Not Started)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Rightmove | — | — | — | — | — |
| 2 | Zoopla | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 3 | OnTheMarket | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

### Region 5 — Germany (Not Started)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | ImmoScout24 | — | — | — | — | — |
| 2 | Immowelt | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 3 | Immonet | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

### Region 6 — Italy (Not Started)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Immobiliare.it | — | — | — | — | — |
| 2 | Casa.it | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 3 | Idealista Italy | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

### Region 7 — USA (Not Started)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Zillow | — | — | — | — | — |
| 2 | Realtor.com | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 3 | Redfin | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 4 | Homes.com | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
| 5 | Craigslist | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |

---

## Build Order Matrix Summary

Regions are built **one at a time** (not in parallel). Each region's build order is decided by `(compliance posture, data quality, effort)` — **not** raw listing volume.

- **Region 1 (Tunisia):** ✅ All 4 sites passed compliance gate. Build order: Tayara.tn → Mubawab → Tunisie Annonce → Houni.tn.
- **Region 2 (France):** ✅ Compliance audit completed. **Build order: SeLoger → Leboncoin.** SeLoger ranks 1 due to less explicit ToS restriction (robots.txt disallows only search pages, not all automated access). Leboncoin ranks 2 due to explicit "forbidden robots" directive in robots.txt requiring special permission.
- **Regions 3–7:** Audits to be initiated upon completion and verification of the preceding region.

---

## Output Artifact

`docs/realestate-compliance-audit.md` — this document. Must be satisfied for all 7 regions before any region's scraper code proceeds past Phase 0.

---

**Next Required Action:** Complete FR-0.2 — Reviewer verification that the France compliance audit records (a)–(d) for both Leboncoin and SeLoger, and that the build-order matrix is properly ranked by `(compliance posture, data quality, effort)`. Once verified, proceed to Region Phase 2 tasks: **Task FR-1.1 — Author `config/profiles/seloger.yaml`** (the top-ranked site per the matrix), followed by **Task FR-1.2** for Leboncoin.

**Current Progress:**

| Region | Status | Build Order Rank |
|---|---|---|
| Tunisia (Phase 1) | ✅ Complete | 1–4 |
| France (Phase 2) | ✅ Audit complete | 1: SeLoger, 2: Leboncoin |
| Spain (Phase 3) | ⚪ Not Started | — |
| UK (Phase 4) | ⚪ Not Started | — |
| Germany (Phase 5) | ⚪ Not Started | — |
| Italy (Phase 6) | ⚪ Not Started | — |
| USA (Phase 7) | ⚪ Not Started | — |

The compliance audit for France is now fully populated and ready for reviewer verification (FR-0.2). Once verified, we proceed to the France end-to-end build starting with the top-ranked site: **SeLoger**.

---

# Live Re-Verification — 2026-09-04

Every remaining region's priority portal was re-checked against its live
robots.txt and then requested once, because a compliance audit that reads only
the policy file answers half the question. **Two portals that the policy
permits refuse automated clients outright**, which no amount of robots.txt
reading would have revealed.

Method: fetch `/robots.txt`, then one GET of the portal's own search path with
an ordinary browser User-Agent. Portals that answered with a challenge were not
probed further — getting past a CAPTCHA or a bot wall is not something this
project does, whatever robots.txt says.

## Findings

| Portal | Region | robots.txt | Live fetch | Build? |
|---|---|---|---|---|
| ImmoScout24 | Germany | **Explicitly permits us**: `User-agent: Claude-User` → `Allow: /`, likewise `Claude-SearchBot` and `ClaudeBot` (the latter minus `/immobilienpreise`). Search sitemaps published. | **401**, `<title>Ich bin kein Roboter</title>`, twice | ❌ blocked technically |
| Idealista | Spain | No AI-agent directives. `*` disallows ajax, photo, virtual-tour and sort-parameter URLs; the search paths themselves are allowed. | **403**, 773-byte block page | ❌ blocked technically |
| SeLoger | France | `Disallow: /classified-search?`, `*/?LISTING-LISTpg` (result pagination), `/slf/api/Listings/listing/`, `/biens-vendus?` | **403**, captcha page | ❌ blocked technically |
| Leboncoin | France | **"It's forbidden to use search robots or other automatic methods to access Leboncoin.fr. Access is only permitted with special permission from Leboncoin.fr."** | not requested | ❌ **forbidden by ToS** |
| Rightmove | UK | `User-agent: GPTBot` → `Disallow: /`; `User-agent: CCbot` → `Disallow: /`. No Claude directive, so `*` formally governs — but the operator has enumerated AI crawlers and excluded them. | not requested | ❌ evident intent to exclude AI agents |
| Immobiliare.it | Italy | `Disallow: /search-list`, `/search-map`, `/ricerca-mappa/`, `/ricerca.php` — the search endpoints themselves | not requested | ❌ search paths disallowed |
| Zillow | USA | `Disallow: /homes/`, with `Allow:` only for `$`-anchored landing pages (`/homes/for_sale/$`). Deep search URLs are disallowed. | not requested | ❌ search paths disallowed |
| Realtor.com | USA | robots.txt itself returns **403** (CloudFront "Request blocked") | n/a | ❌ blocked technically |

## What this means for the build order

The plan states that compliance posture is a first-class input to build order
rather than a footnote. Applied to the evidence above, **no remaining foreign
region is currently buildable**, and the reasons differ in kind:

- **Leboncoin is a ToS decision, not a technical one.** It could be built if
  Leboncoin grants the written permission its robots.txt requires. Nothing
  should be scraped from it before then.
- **Rightmove is a judgement call.** No directive names a Claude agent, so a
  strict reading leaves `*` in force and the search paths open. But the
  operator has listed AI crawlers by name and excluded them, and building an
  AI-driven scraper against that is against evident intent. Treated as
  do-not-build pending an explicit decision by the owner.
- **Immobiliare and Zillow disallow the search paths themselves.** Both publish
  detail pages that are allowed, but with no permitted way to enumerate them
  there is no crawl to build.
- **ImmoScout24, Idealista, SeLoger and Realtor.com refuse automated clients.**
  ImmoScout24 is the sharpest case: its robots.txt goes out of its way to
  permit Anthropic agents by name, and the server then answers 401 with a
  "not a robot" interstitial. Policy and enforcement disagree, and the
  enforcement is what a client actually meets.

**Consequence: the Tunisia and South Africa portals remain the only buildable
set.** That is not a gap to be closed by trying harder; four of the eight are
refusing automated traffic and three more disallow the paths a crawl needs.

## If a foreign region is wanted

In descending order of how likely they are to lead somewhere:

1. **Ask ImmoScout24 for access.** Their robots.txt already names Claude agents
   as welcome, which is an unusually strong starting position for a
   conversation about an API key or an allowlisted client.
2. **Ask Leboncoin for the special permission** its robots.txt describes.
3. **Look for licensed feeds.** None of these portals publishes a public API;
   several license their inventory commercially, which is a procurement
   question rather than an engineering one.
4. **Re-check periodically.** Bot walls and robots directives both change. This
   section is dated for that reason.

## Region status after this re-verification

| Region | Status | Buildable portals |
|---|---|---|
| Tunisia (Phase 1) | ✅ Built | tayara, mubawab, tunisieannonce, houni |
| South Africa | ✅ Built | property24, privateproperty |
| France (Phase 2) | ⛔ Blocked | none — Leboncoin forbidden, SeLoger challenges |
| Spain (Phase 3) | ⛔ Blocked | none — Idealista challenges |
| UK (Phase 4) | ⛔ Blocked | none — Rightmove excludes AI crawlers |
| Germany (Phase 5) | ⛔ Blocked | none — ImmoScout24 challenges despite permitting us |
| Italy (Phase 6) | ⛔ Blocked | none — search endpoints disallowed |
| USA (Phase 7) | ⛔ Blocked | none — Zillow disallows search, Realtor.com 403s |

## Second attempt — the sitemap route (2026-09-04)

The first pass tested each portal's SEARCH pages. This one tested the other
door: the sitemaps the portals publish in their own robots.txt, which exist
precisely so that crawlers can use them. Using a published sitemap as intended
is an invitation rather than an evasion, so it was worth trying before calling
the regions closed.

| Portal | Sitemap | Content behind it |
|---|---|---|
| ImmoScout24 | `Suche/sitemap/activeExposes.xml` and `notEmptyPageSearches.xml`, both named in robots.txt | **401** — the same 4006-byte "Ich bin kein Roboter" page. Even the sitemap it advertises is behind the wall. |
| Immobiliare.it | `sitemap.xml` → `sitemaps/residenziale.xml`, **200**, 5686 URLs | **403**, 774-byte block page. The URLs it lists (`/vendita-case/agrigento/`) are not in any Disallow rule, and they are still refused. |
| Idealista | `sitemap.xml` → **404** | n/a |
| Zillow | `sitemap.xml` → **404** | n/a |

The Immobiliare result is the informative one: a static sitemap served from a
CDN, listing thousands of URLs that robots.txt explicitly permits, in front of
content that refuses every automated client. Policy and enforcement disagree
there in the same way they do at ImmoScout24.

**Three doors have now been tried on these portals — search pages, published
sitemaps, and sitemap-derived landing pages — and all three are walled.** The
remaining routes are not technical:

1. Ask ImmoScout24 for access. Its robots.txt names `Claude-User`,
   `Claude-SearchBot` and `ClaudeBot` as allowed, which is an unusually strong
   opening for requesting an API key or an allowlisted client.
2. Ask Leboncoin for the special permission its robots.txt describes.
3. License a feed. Several of these portals sell their inventory; none
   publishes a public API.

What is NOT a route: replaying session cookies or calling internal GraphQL
endpoints to get past the challenges. Those challenges exist to refuse
automated clients, and going around them is circumvention whatever robots.txt
says - as well as the fastest way to lose the address space and to undermine
the ImmoScout24 conversation above before it starts.


---

# Client-rendered portals: measured verdicts — 2026-09-04

Bien'ici and Fotocasa were the last two portals left in the plan. Both answer
200 to a plain GET and hand back a shell, so both were rendered through
cloakserve (`cmd/renderprobe`) rather than judged from the HTTP response.
Neither is buildable, and the reasons differ enough to be worth recording so
the question is not reopened from scratch.

## Bien'ici — consent wall, no list in the DOM

    go run ./cmd/renderprobe -url https://www.bienici.com/recherche/achat/france -delay 6s

121,779 bytes rendered. Nothing to parse against:

| signal | count |
|---|---|
| `__NEXT_DATA__` | 0 |
| `application/ld+json` | 0 |
| `data-id` / `itemprop` / `result-item` | 0 |
| detail hrefs | 0 |
| `didomi` | 2037 |
| `consent` | 341 |

The rendered document is the Didomi consent manager, not a search page. The
listings load only once the banner is answered.

## Fotocasa — page renders, result list stays empty

    go run ./cmd/renderprobe -url https://www.fotocasa.es/es/comprar/viviendas/sevilla-capital/todas-las-zonas/l -delay 6s -scroll 12

1.31 MB and a genuinely rendered page — breadcrumbs, an `AggregateOffer`
saying "3.283 anuncios", a `RealEstateListing`. But the result list itself
does not fill. Of 31 `<article>` elements:

| what | count |
|---|---|
| byte-identical 2412-byte empty skeletons | **19** |
| new-construction promos (`/obra-nueva/`) | 9 |
| real resale listing cards | **1** (a rotating promoted slot) |

**Scrolling was tried and is not the answer.** Twelve rounds moved the DOM
from 1,327,955 to 1,309,585 bytes and left the unique listing-id count at 1.
A lazy list grows under scrolling; this one does not. The skeletons are
waiting for data that never arrives for this client, and the same Didomi
consent manager is present (1963 references).

## Why neither is pursued further

For both portals the remaining step is the same: answer the consent banner
programmatically, or call the internal endpoint the page would have called
once it was answered. Both are the route this document already rules out at
the end of the ImmoScout24 section, and nothing about these two portals makes
it a different question. A consent gate is a request for a decision from a
person; clicking it from a scraper records a consent nobody gave, which is a
worse problem under GDPR than the missing data it would unlock.

**Coverage is therefore final at 10 portals across 5 markets.** Spain is
served by Habitaclia and France is not served at all; a France source needs
either the ImmoScout24-style licensing conversation or a portal not yet
surveyed, not more engineering against these two.

## One capability came out of it

`FetchRenderedPage` grew a scroll parameter, which is what separates "this
portal lazy-loads" from "this portal is withholding its list". The scrape path
passes 0 and no profile exposes it — no harvested portal lazy-loads — but
`renderprobe -scroll N` is how that distinction gets measured, and guessing it
wrong yields a profile that parses one row per page and reports success.
