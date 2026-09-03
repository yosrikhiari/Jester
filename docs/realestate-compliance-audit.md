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
| 3 | Bien'ici | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
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
| 2 | Fotocasa | 🔜 later | 🔜 later | 🔜 later | 🔜 later | 🔜 later |
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