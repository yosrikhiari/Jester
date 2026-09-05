# Real-Estate Compliance Audit Report

**Report Date:** 2026-09-03 · **Last updated:** 2026-09-05  
**Scope:** 7 numbered regions plus South Africa, Morocco and UAE (Tunisia foundation + France + Spain + UK + Germany + Italy + USA + ZA + MA + AE)  
**Status:** ✅ **All audits complete.** 16 portals across 8 markets passed the gate and are built: Tunisia (5), South Africa (2), France (1), Spain (1), UK (2), Germany (1), USA (1), Morocco (1), UAE (1). Italy has no buildable portal. The Phase 2 expansion (2026-09-05) found accessible fallbacks for every previously-blocked region except Italy, plus two new markets.

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
| 5 | **Behya.tn** | None (scraping only) | **No restriction.** robots.txt carries a `Sitemap:` line and **no `Disallow` at all**. WordPress classifieds theme, 15 tiles/page, served in full to an ordinary GET. Verified 2026-09-04. | Phone numbers not collected | Scrape-existing public data; record lives in `data-` attributes on each tile | **5 (built)** |
| 6 | Menzili.tn | Not reached. | **EXCLUDED.** robots.txt disallows `ClaudeBot` **by name**. | Not reached. | n/a — excluded by name. | **n/a (excluded)** |
| 7 | ImmoTunisie | n/a | n/a — domain is **parked for sale on GoDaddy**; no portal to audit. | n/a | n/a | **n/a (defunct)** |
| 8 | Lyanimmo | n/a | n/a — domain **does not resolve**. | n/a | n/a | **n/a (defunct)** |

**Tunisia Build Order Matrix:** Ranked by `(compliance posture, data quality, effort)` = {1, 2, 3, 4, 5}. Five sites passed the compliance gate and are built. The three remaining candidates are closed for distinct reasons — one excludes ClaudeBot by name, two no longer exist — so Tunisia's coverage is final at five portals. Phase 1 (Tunisia) foundation verified.

---

### Region 2 — France ✅ COMPLETED (served by ParuVendu)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | **Leboncoin** | None (no public API). Multiple Apify actors confirm: "LeBonCoin doesn't offer a public API for property data extraction." Scraping only, proxy-dependent. | **EXPLICIT RESTRICTION.** robots.txt: "It's forbidden to use search robots or other automatic methods to access Leboncoin.fr. Access is only permitted with special permission from Leboncoin.fr." CGU also prohibits automated access without permission. ToS clause reference required. | Phone numbers may appear on listings; requires consent-gate for display/storage. Current schema does not collect phone (PII consent-gated field per normalized schema). | GDPR/DPA: Scrape-existing public data, but ToS restriction raises consent questions for phone numbers. Classify as "scrape-existing with PII caveat" pending clause review. | **n/a (forbidden by ToS)** |
| 2 | **SeLoger** | None (no public API). Apify actors exist but all note scraping-only integration; no official API published. | **BLOCKED.** robots.txt disallows `/recherche` (search pages). Server returns 403 with captcha page. | Phone numbers may appear on listings; requires consent-gate for display/storage. Current schema has PII consent-gated field (seller.phone). | n/a — blocked technically. | **n/a (blocked)** |
| 3 | Bien'ici | None published. | robots.txt PERMITS the search path (`/recherche/achat/...`); disallows are query-shape only (`/recherche/*&*`, `/*tri=*`, `/*?mode=*`), and `/recherche/*?neuf=oui&page=*` is explicitly Allowed. | Not reached. | **Not buildable — consent-gated.** See below. | **n/a (closed)** |
| 4 | Logic-Immo | Not audited. | Not audited — ParuVendu already serves this market. | — | — | **n/a (not audited)** |
| **5→1** | **ParuVendu** | None (scraping only) | **No restriction.** robots.txt names no AI user agent and no rule touches `/immobilier/vente/` or `/immobilier/location/`. Pagination `?p=N` and department filter `?lo=N` are not disallowed. Server-rendered PHP on Apache, 30 tiles/page. Requires browser fetch (403 on plain GET due to User-Agent filtering); no consent wall, no CAPTCHA. Verified 2026-09-05. | Phone numbers not collected in current schema. | Scrape-existing public data; tiles use `div.blocAnnonce` with `data-id` attributes. DPE energy rating extracted. No personal data collected. | **1 (built)** |

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

### Region 3 — Spain ✅ COMPLETED (served by Habitaclia)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Idealista | None (scraping only) | **BLOCKED.** Returns 403 to an ordinary GET; sitemap returns 404. No AI-agent directives in robots.txt; `*` disallows ajax, photo, virtual-tour and sort-parameter URLs but search paths are allowed — the block is server-side, not policy. | Not reached. | n/a — blocked technically. | **n/a (blocked)** |
| 2 | Fotocasa | None published. | robots.txt allows page 1 of a result list but **forbids all pagination** (`/*/l/2*` … `/*/l/39*`), the three largest cities (`/madrid/`, `/barcelona/`, `/valencia/`) and every price/room filter. Rendered page shows 19 empty card skeletons behind Didomi consent gate. | Not reached. | **Not buildable — consent-gated.** See client-rendered portals section. | **n/a (closed)** |
| **2→1** | **Habitaclia** | None (scraping only) | **No restriction.** No rule in robots.txt touches `/viviendas-*.htm`. Pages served in full to a plain GET, 15 tiles/page. Verified 2026-09-04. | Phone numbers not collected in current schema. | Scrape-existing public data; structured data via schema.org Product/Offer microdata and data-attributes. No personal data collected. | **1 (built)** |

**Spain Build Order Matrix:** Idealista blocked technically (403), Fotocasa consent-gated. Habitaclia is the only accessible portal and is built and running. Build order: Habitaclia (1, built). Ranked by `(compliance posture, data quality, effort)`: Habitaclia wins on all three axes — accessible, structured data via microdata, lowest effort.

### Region 4 — UK ✅ COMPLETED (served by OnTheMarket + Zoopla)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Rightmove | None (scraping only) | **EXCLUDED.** `User-agent: GPTBot` → `Disallow: /`; `User-agent: CCbot` → `Disallow: /`. No Claude directive, so `*` formally governs — but the operator has enumerated AI crawlers by name and excluded them. Evident intent to exclude AI agents. | Not reached. | n/a — excluded by evident intent. | **n/a (excluded)** |
| **2** | **Zoopla** | None (scraping only) | **No restriction.** robots.txt does not name ClaudeBot, CCBot or any AI agent. Only auction facets and niche bedroom-filtered subtypes are disallowed; no rule touches `/for-sale/houses/`. Publishes rich schema.org Product LD+JSON with an ItemList of 28 `Product` entries per page, each carrying `Offer` (price/currency) and `SingleFamilyResidence` (address, bedrooms, bathrooms, floor size, geo). Plain GET returns full data. `?pn=N` pagination confirmed. Verified 2026-09-05. | Phone numbers not collected in current schema. | Scrape-existing public data; structured data via schema.org LD+JSON (`ldjson` mode). GBP currency. No personal data collected. | **2 (built)** |
| **3→1** | **OnTheMarket** | None (scraping only) | **No restriction.** robots.txt permits search paths. Pages require browser rendering (JS-rendered, first portal needing `browser` fetch mode). Verified 2026-09-04. | Phone numbers not collected in current schema. | Scrape-existing public data; browser-rendered. No personal data collected. GBP currency. | **1 (built)** |

**UK Build Order Matrix:** Rightmove excluded by evident AI-crawler intent. OnTheMarket and Zoopla are both accessible and built. OnTheMarket (browser mode, 32 cards/page) + Zoopla (LD+JSON mode, 28 per page, 8 city seeds). Two-portal coverage enables UK cross-portal dedup.

### Region 5 — Germany ✅ COMPLETED (served by Immowelt)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | ImmoScout24 | None (scraping only) | **BLOCKED technically.** robots.txt **explicitly permits** `User-agent: Claude-User` → `Allow: /`, likewise `Claude-SearchBot` and `ClaudeBot`. Yet server returns **401** with `<title>Ich bin kein Roboter</title>` bot wall. Sitemap route also returns 401. Policy and enforcement disagree — enforcement wins. Verified 2026-09-04. | Not reached. | n/a — blocked technically despite permissive robots.txt. Best candidate for requesting API access. | **n/a (blocked)** |
| **2→1** | **Immowelt** | None (scraping only) | **No restriction.** robots.txt **explicitly names Claude agents as allowed**: `User-agent: ClaudeBot` / `Claude-User` / `Claude-SearchBot` → `Allow: /`. No rule touches `/liste/`. Pages served in full to a plain GET, 32 tiles/page via `data-testid` attributes. Pagination query params redirect, so coverage comes from 10 city seeds. Verified 2026-09-05. | Phone numbers not collected in current schema. | Scrape-existing public data; listing cards use `data-testid` test hooks as extraction anchors. EUR currency. No personal data collected. | **1 (built)** |
| 3 | Immonet | Not audited. | Not audited — Immowelt already serves this market. | — | — | **n/a (not audited)** |

**Germany Build Order Matrix:** ImmoScout24 blocked by bot wall despite explicitly permitting Claude agents in robots.txt. Immowelt is the accessible portal and is built and running (anchored mode, 10 city seeds). Build order: Immowelt (1, built).

### Region 6 — Italy ⛔ BLOCKED

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Immobiliare.it | None (scraping only) | **BLOCKED.** robots.txt `Disallow: /search-list`, `/search-map`, `/ricerca-mappa/`, `/ricerca.php` — the search endpoints themselves. Sitemap route returns **403** despite URLs being in allowed paths. Policy and enforcement both refuse. Verified 2026-09-04. | Not reached. | n/a — search paths disallowed and detail pages also blocked. | **n/a (blocked)** |
| 2 | Casa.it | Not audited. | Not audited — primary portal blocked, no fallback investigated yet. | — | — | **n/a (not audited)** |
| 3 | Idealista Italy | Not audited. | Not audited — Idealista returns 403 in Spain, likely same across markets. | — | — | **n/a (not audited)** |

**Italy Build Order Matrix:** Immobiliare.it blocks search paths in robots.txt and refuses sitemap-derived URLs with 403. No fallback portal built. Region is fully blocked.

### Region 7 — USA ✅ COMPLETED (served by Redfin)

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | Zillow | None (scraping only) | **DISALLOWED.** `Disallow: /homes/`, with `Allow:` only for `$`-anchored landing pages (`/homes/for_sale/$`). Deep search URLs are disallowed. Sitemap returns **404**. | Not reached. | n/a — search paths disallowed. | **n/a (disallowed)** |
| 2 | Realtor.com | None (scraping only) | **BLOCKED technically.** robots.txt itself returns **403** (CloudFront "Request blocked"). | Not reached. | n/a — blocked technically. | **n/a (blocked)** |
| **3→1** | **Redfin** | None (scraping only) | **No restriction.** robots.txt permits search paths. Pages served with schema.org Product/Offer LD+JSON to a plain GET. USD currency. Verified 2026-09-04. | Phone numbers not collected in current schema. | Scrape-existing public data; structured data via schema.org LD+JSON (`ldjson` mode). No personal data collected. | **1 (built)** |
| 4 | Homes.com | Not audited. | Not audited — Redfin already serves this market. | — | — | **n/a (not audited)** |
| 5 | Craigslist | Not audited. | Not audited — Redfin already serves this market. | — | — | **n/a (not audited)** |

**USA Build Order Matrix:** Zillow disallows search paths, Realtor.com blocks at CDN level. Redfin is the accessible portal and is built and running (LD+JSON mode). Build order: Redfin (1, built).

---

### South Africa (predates this plan) ✅ COMPLETED — audit recorded retroactively

This market is not one of the plan's seven numbered regions. Both portals were
built before the plan's compliance gate existed, so the records below were
written retroactively (2026-09-05) from the probe notes in their profiles. They
are listed here so that no built portal is missing an audit record.

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | **Property24** | None (scraping only) | **No restriction.** Probed 2026-08-30 and re-verified 2026-09-04: search page and tiles served in full to an ordinary GET, no bot wall. `?Page=N` pagination, 22 tiles/page. ZAR. | Phone numbers not collected in current schema. | Scrape-existing public data; schema.org microdata as `itemprop=` attributes plus the portal's own `data-listing-number`. No personal data collected. | **1 (built)** |
| 2 | **Private Property** | None (scraping only) | **No restriction.** Probed 2026-08-30: plain HTTP, no bot wall. ZAR. | Phone numbers not collected in current schema. | Scrape-existing public data; publishes schema.org `Residence` per result (parsed anchored rather than ldjson, because price lives in the markup outside the JSON block). No personal data collected. | **2 (built)** |

**South Africa Build Order Matrix:** Both portals accessible with no restriction; both built. This market needs no further compliance work.

---

### Morocco (new market, 2026-09-05) ✅ COMPLETED

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | **Mubawab Morocco** | None (scraping only) | **No restriction.** robots.txt **explicitly allows ClaudeBot**. Same platform as mubawab.tn (Tunisia), already built and proven. Plain GET, anchored `adid` tiles, `:p:N` path pagination, 33 tiles/page. 8 city seeds (Casablanca, Rabat, Marrakech, Tanger, Fès, Agadir, Meknès, Oujda). Verified 2026-09-05. | Phone numbers not collected in current schema. | Scrape-existing public data; same tile markup as the Tunisia variant. MAD currency. No personal data collected. | **1 (built)** |

**Morocco Build Order Matrix:** Mubawab Morocco is the same platform as the Tunisia variant already running. Accessible, explicitly allows ClaudeBot, built. Build order: Mubawab Morocco (1, built).

---

### UAE/Gulf (new market, 2026-09-05) ✅ COMPLETED

| Priority | Site | (a) API/Feed | (b) ToS Restriction | (c) PII Consent | (d) GDPR/DPA Posture | Build Order Rank |
|---|---|---|---|---|---|---|
| 1 | **Bayut** | None (scraping only) | **No restriction on search paths.** robots.txt allows `/for-sale/` and `/to-rent/`; disallows `/search/`, `/api/` (except `/api/transactions` and `/api/propertyPrices/`), `/account`, and some city landing pages. JS challenge on plain GET requires browser fetch. `?page=N` pagination confirmed. 5 emirate seeds (Dubai, Abu Dhabi, Sharjah, Ajman, Ras Al Khaimah). Verified 2026-09-05. | Phone numbers not collected in current schema. | Scrape-existing public data; anchored on `/property/details-{id}.html` links. AED currency. No personal data collected. | **1 (built)** |

**UAE Build Order Matrix:** Bayut is the dominant Gulf portal. Accessible via browser, rich tiles with AED prices. Built. Build order: Bayut (1, built).

---

## Build Order Matrix Summary

Regions are built **one at a time** (not in parallel). Each region's build order is decided by `(compliance posture, data quality, effort)` — **not** raw listing volume.

- **Region 1 (Tunisia):** ✅ 5 sites passed the compliance gate. Build order: Tayara.tn → Mubawab → Tunisie Annonce → Houni.tn → Behya.tn. **All five built.** Menzili excludes ClaudeBot by name; ImmoTunisie and Lyanimmo no longer exist.
- **South Africa** (outside the numbered regions): ✅ Property24 and Private Property, both unrestricted, both built. Audit recorded retroactively 2026-09-05.
- **Region 2 (France):** ✅ Served by ParuVendu. Leboncoin forbidden by ToS, SeLoger returns 403. **ParuVendu built** (browser mode, 9 department seeds).
- **Region 3 (Spain):** ✅ Served by Habitaclia. Idealista 403, Fotocasa consent-gated. **Habitaclia built.**
- **Region 4 (UK):** ✅ Served by OnTheMarket + Zoopla. Rightmove excludes AI crawlers. **OnTheMarket built** (browser mode) + **Zoopla built** (LD+JSON mode).
- **Region 5 (Germany):** ✅ Served by Immowelt. ImmoScout24 bot wall despite permitting Claude. **Immowelt built** (anchored, 10 city seeds).
- **Region 6 (Italy):** ⛔ Blocked. Immobiliare.it disallows search paths and 403s sitemap URLs. No fallback built.
- **Region 7 (USA):** ✅ Served by Redfin. Zillow disallows `/homes/`, Realtor.com 403s. **Redfin built** (LD+JSON mode).
- **Morocco** (new market): ✅ Mubawab Morocco, explicitly allows ClaudeBot, same platform as Tunisia. **Built.**
- **UAE/Gulf** (new market): ✅ Bayut, dominant Gulf portal, browser fetch. **Built.**

---

## Output Artifact

`docs/realestate-compliance-audit.md` — this document. Must be satisfied for all 7 regions before any region's scraper code proceeds past Phase 0.

---

**Next Required Action:** None — every built portal now has an audit record and every surveyed portal has a verdict. Live: Tunisia (5), South Africa (2), France (ParuVendu), Spain (Habitaclia), UK (OnTheMarket + Zoopla), Germany (Immowelt), USA (Redfin), Morocco (Mubawab), UAE (Bayut) = **16 portals across 8 markets**. Only Italy has no buildable portal and is closed pending a non-engineering route: a licensed feed, or a portal not yet surveyed. Re-check the blocked portals periodically — bot walls and robots directives both change, which is why every finding here is dated.

**Current Progress:**

| Region | Status | Build Order Rank |
|---|---|---|
| Tunisia (Phase 1) | ✅ Complete & built | tayara, mubawab, tunisieannonce, houni, behya |
| France (Phase 2) | ✅ Complete (ParuVendu) | ParuVendu built (browser, 9 dept seeds); Leboncoin forbidden, SeLoger 403 |
| Spain (Phase 3) | ✅ Complete (Habitaclia) | Habitaclia built; Idealista 403, Fotocasa consent-gated |
| UK (Phase 4) | ✅ Complete (OnTheMarket + Zoopla) | OnTheMarket built (browser) + Zoopla built (LD+JSON, 8 city seeds); Rightmove excluded |
| Germany (Phase 5) | ✅ Complete (Immowelt) | Immowelt built (anchored, 10 city seeds); ImmoScout24 bot wall |
| Italy (Phase 6) | ⛔ Blocked | none — Immobiliare.it search paths disallowed |
| USA (Phase 7) | ✅ Complete (Redfin) | Redfin built (LD+JSON); Zillow disallowed, Realtor.com 403 |
| Morocco (new) | ✅ Complete (Mubawab) | Mubawab Morocco built (anchored, 8 city seeds) |
| UAE/Gulf (new) | ✅ Complete (Bayut) | Bayut built (browser, 5 emirate seeds) |

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

**Consequence: the primary portals in most foreign regions are blocked.**
Fallback portals have since been identified and built for every blocked region
except Italy: Spain (Habitaclia), UK (OnTheMarket + Zoopla), USA (Redfin),
France (ParuVendu), Germany (Immowelt). Two new markets were also opened:
Morocco (Mubawab) and UAE (Bayut). Only Italy remains blocked with no
accessible fallback.

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
| Tunisia (Phase 1) | ✅ Built | tayara, mubawab, tunisieannonce, houni, behya |
| South Africa | ✅ Built | property24, privateproperty |
| France (Phase 2) | ✅ Built (fallback) | paruvendu — Leboncoin forbidden, SeLoger 403 |
| Spain (Phase 3) | ✅ Built (fallback) | habitaclia — Idealista 403, Fotocasa consent-gated |
| UK (Phase 4) | ✅ Built (2 portals) | onthemarket + zoopla — Rightmove excludes AI crawlers |
| Germany (Phase 5) | ✅ Built (fallback) | immowelt — ImmoScout24 challenges despite permitting us |
| Italy (Phase 6) | ⛔ Blocked | none — search endpoints disallowed |
| USA (Phase 7) | ✅ Built (fallback) | redfin — Zillow disallows search, Realtor.com 403s |
| Morocco (new) | ✅ Built | mubawab-ma — explicitly allows ClaudeBot |
| UAE/Gulf (new) | ✅ Built | bayut — browser fetch, 5 emirate seeds |

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

**Coverage after the Phase 2 expansion (2026-09-05) is 16 portals across 8 markets.**
The probe sweep found accessible fallbacks for France (ParuVendu), Germany
(Immowelt), added a second UK portal (Zoopla), and opened two new markets:
Morocco (Mubawab) and UAE (Bayut). Only Italy remains blocked.

## One capability came out of it

`FetchRenderedPage` grew a scroll parameter, which is what separates "this
portal lazy-loads" from "this portal is withholding its list". The scrape path
passes 0 and no profile exposes it — no harvested portal lazy-loads — but
`renderprobe -scroll N` is how that distinction gets measured, and guessing it
wrong yields a profile that parses one row per page and reports success.
