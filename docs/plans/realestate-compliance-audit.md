# Real Estate Scraping Compliance Audit

**Status:** DRAFT  
**Last Updated:** 2026-09-01  
**Purpose:** Per-site compliance audit covering ToS, robots.txt, official API/feed availability, PII consent, and GDPR/DPA posture. This audit is a **first-class input to build order**, not a footnote.

---

## Audit Criteria (per site)

For each site, this audit records:

1. **(a) Official API / Feed Availability** — Does the site offer a documented API, RSS feed, or other official programmatic access method?
2. **(b) ToS Restrictions on Automated Access** — Does the Terms of Service explicitly prohibit, restrict, or permit automated access / scraping?
3. **(c) Phone/Contact Data Consent (PII)** — How should phone numbers and contact information be handled? What consent is required?
4. **(d) GDPR/DPA Posture** — Is this scraping existing publicly-available data, or creating new personal data records? What data protection obligations apply?
5. **robots.txt** — What does the site's robots.txt say about crawling?

**Build Order Matrix:** Sites are ranked by `(compliance posture, data quality, effort)` — **NOT** raw listing volume alone. A site with an official API and permissive ToS may be prioritized ABOVE a higher-volume site with restrictive ToS and no API.

---

## Region Phase 1 — Tunisia (Foundation)

### Site Priority Matrix

| Priority | Site | Estimated Volume | Build Target | Audit Status |
|---|---|---|---|---|
| 1 | Tayara.tn | ~50k listings | ✅ Build | ✔ Complete |
| 2 | Mubawab Tunisia | ~50k+ listings | ✅ Build | ⏳ Pending |
| 3 | Tunisie Annonce | ~30k listings | ✅ Build | ⏳ Pending |
| 4 | Houni.tn | ~20k listings | ✅ Build | ⏳ Pending |
| 5 | Behya | Unknown | 🔜 Later | ⏳ Pending |
| 6 | ImmoTunisie | Unknown | 🔜 Later | ⏳ Pending |
| 7 | Menzili | Unknown | 🔜 Later | ⏳ Pending |
| 8 | Lyanimmo | Unknown | 🔜 Later | ⏳ Pending |

---

## Tayara.tn

**Domain:** `https://www.tayara.tn`  
**Audit Date:** 2026-09-01  
**Audited By:** Jester Team  
**Market:** Tunisia (TN)

### (a) Official API / Feed Availability

- **Status:** ❌ No official API documented
- **RSS/Feed:** ❌ No RSS feed found
- **Notes:** Tayara.tn is a classifieds platform with no publicly documented API. Integration would require HTML parsing of listing pages.

### (b) ToS Restrictions on Automated Access

- **ToS URL:** `https://www.tayara.tn/terms` (example - verify actual URL)
- **Status:** ⚠️ **REQUIRES MANUAL REVIEW**
- **Key Findings:** 
  - ToS must be reviewed in detail before scraping begins
  - Look for clauses prohibiting automated access, data collection, or commercial use
  - Check if ToS explicitly permits indexing/aggregation
- **Recommendation:** **REVIEW REQUIRED** - Legal review of ToS needed before implementation

### (c) Phone/Contact Data Consent (PII)

- **Phone Numbers Published:** ✅ Yes - sellers publish phone numbers on listings
- **Consent Model:** Public display on listing page (user-initiated publication)
- **Our Handling:**
  - Phone numbers are **publicly displayed** by the listing author
  - Storage: Will be stored in `listing.Payload` JSON (not indexed separately)
  - **Access Control:** Phone data should be treated as PII - access-controlled, not exposed in public APIs without user consent
  - **Retention:** Follow standard Jester retention policy (see `scripts/retention.sh`)
- **GDPR Consideration:** Public data, but still personal data under GDPR/CCPA - must have lawful basis for processing

### (d) GDPR/DPA Posture

- **Data Type:** Scraping **existing, publicly-available** listing data
- **Personal Data:** May include seller names, phone numbers (when published), agency names
- **Lawful Basis:** Legitimate interest (market research, price transparency) OR public task
- **Data Subject Rights:** Must support:
  - Right to access (can query what we store about a listing/seller)
  - Right to erasure (can remove listing data on request)
  - Right to rectification (can correct inaccurate data)
- **Data Protection Impact:** Low-Medium risk (public data, but includes contact info)
- **Recommendation:** 
  - Document lawful basis for processing
  - Implement data subject request handling
  - Do not re-publish PII externally without additional consent

### robots.txt

- **URL:** `https://www.tayara.tn/robots.txt`
- **Status:** ✅ **CHECKED - 2026-09-01**
- **Key Directives:**
  - User-agent: *
  - Allow: /
  - Host: https://www.tayara.tn
  - Sitemap: https://www.tayara.tn/sitemap.xml
  - Sitemap: https://www.tayara.tn/sitemap-index.xml
- **Compliance:** ✅ **PASS** - Site allows all crawling (Allow: /), no restrictions on property pages

### Build Order Impact

- **Compliance Score:** ⚠️ MEDIUM (pending ToS review only - robots.txt ✅ PASS)
- **API Availability:** ❌ No (HTML parsing or Next.js API endpoint required)
- **Data Quality:** ✅ HIGH (largest classifieds volume in Tunisia)
- **Effort:** MEDIUM (Next.js client-side rendering - may need API discovery or headless browser)
- **Recommendation:** **Proceed with Tayara as Priority 1** AFTER:
  1. ⏳ ToS manual review confirms automated access is not explicitly prohibited
  2. ✅ robots.txt check confirms property pages are crawlable (COMPLETE: Allow: /)
  3. ✅ PII handling documented and approved (DONE: see section above)

**Next Steps for Tayara:**
1. [ ] Manual review of ToS (legal team or owner approval) - BLOCKING
2. [✅] Check robots.txt and document findings here (COMPLETE: Allow: /)
3. [✅] Profile template created (config/profiles/tayara.yaml)
4. [⏳] API discovery: inspect Next.js __NEXT_DATA__ or network calls for JSON endpoints
5. [ ] If ToS passes: refine profile selectors and proceed to worker integration (Task 1.2)

---

## Mubawab Tunisia

**Domain:** `https://www.mubawab.tn`  
**Audit Date:** 2026-09-01  
**Market:** Tunisia (TN)

### (a) Official API / Feed Availability

- **Status:** ⏳ **TO BE INVESTIGATED**
- **Notes:** Mubawab is a real-estate-focused platform (multi-region). May have structured data or API.

### (b) ToS Restrictions on Automated Access

- **ToS URL:** (To be determined)
- **Status:** ⏳ **REQUIRES REVIEW**

### (c) Phone/Contact Data Consent (PII)

- **Status:** ⏳ **TO BE INVESTIGATED**
- **Expected:** Phone numbers likely published on listings (standard for real-estate portals)

### (d) GDPR/DPA Posture

- **Status:** ⏳ **TO BE ASSESSED**
- **Expected:** Public listing data with contact info - similar posture to Tayara

### robots.txt

- **URL:** `https://www.mubawab.tn/robots.txt`
- **Status:** ⏳ **REQUIRES CHECK**

### Build Order Impact

- **Status:** ⏳ **Audit incomplete** - proceed after Tayara audit is finalized

---

## Tunisie Annonce

**Domain:** `https://www.tunisieannonce.com` (example - verify actual domain)  
**Audit Date:** 2026-09-01  
**Market:** Tunisia (TN)

### (a) Official API / Feed Availability

- **Status:** ⏳ **TO BE INVESTIGATED**

### (b) ToS Restrictions on Automated Access

- **Status:** ⏳ **REQUIRES REVIEW**

### (c) Phone/Contact Data Consent (PII)

- **Status:** ⏳ **TO BE INVESTIGATED**

### (d) GDPR/DPA Posture

- **Status:** ⏳ **TO BE ASSESSED**

### robots.txt

- **Status:** ⏳ **REQUIRES CHECK**

### Build Order Impact

- **Status:** ⏳ **Audit incomplete**

---

## Houni.tn

**Domain:** `https://www.houni.tn` (example - verify actual domain)  
**Audit Date:** 2026-09-01  
**Market:** Tunisia (TN)

### (a) Official API / Feed Availability

- **Status:** ⏳ **TO BE INVESTIGATED**

### (b) ToS Restrictions on Automated Access

- **Status:** ⏳ **REQUIRES REVIEW**

### (c) Phone/Contact Data Consent (PII)

- **Status:** ⏳ **TO BE INVESTIGATED**

### (d) GDPR/DPA Posture

- **Status:** ⏳ **TO BE ASSESSED**

### robots.txt

- **Status:** ⏳ **REQUIRES CHECK**

### Build Order Impact

- **Status:** ⏳ **Audit incomplete**

---

## Tunisia Build Order Matrix Summary

Based on **completed audits only**, the current build order is:

| Rank | Site | Compliance | API | Data Quality | Effort | Decision |
|---|---|---|---|---|---|---|
| 1 | Tayara.tn | ⚠️ MEDIUM (pending ToS/robots.txt) | ❌ | ✅ HIGH | MEDIUM | **Proceed after ToS+robots.txt review** |
| 2 | Mubawab Tunisia | ⏳ Pending | ⏳ | ✅ HIGH | ⏳ | Audit after Tayara |
| 3 | Tunisie Annonce | ⏳ Pending | ⏳ | MEDIUM | ⏳ | Audit after Mubawab |
| 4 | Houni.tn | ⏳ Pending | ⏳ | MEDIUM | ⏳ | Audit after Tunisie Annonce |

**Matrix may be re-ordered** once all audits are complete. For example:
- If Mubawab offers an official API → moves to Priority 1
- If Tayara robots.txt blocks property pages → moves down or excluded
- If any site has explicitly permissive ToS → gains priority

---

## Regional Compliance Notes

### Tunisia-Specific Considerations

- **Language:** Listings are primarily in Arabic and French - selectors must handle both
- **Currency:** Tunisian Dinar (TND) - store as `currency: "TND"`
- **PII Regulation:** Tunisia has Law 2004-63 on personal data protection - similar principles to GDPR
- **Market Context:** Real-estate listings routinely publish owner/agency phone numbers - this is market standard

### General Compliance Principles (All Regions)

1. **No Anti-Bot Bypass:** If a site blocks automated access (technically or legally), it is deferred or excluded. No evasion techniques.
2. **Respect robots.txt:** Always. Disallowed paths are not scraped.
3. **Rate Limiting:** Implement respectful crawl delays (minimum 1-2 seconds between requests)
4. **PII Minimization:** Only collect PII that is publicly displayed and necessary for the use case
5. **Official APIs First:** If a site offers an API, use it instead of scraping HTML
6. **ToS Trumps Volume:** A compliant, smaller site beats a legally-restricted larger one

---

## Audit Status Summary

- ✅ **Complete:** 0/4 sites (Tayara draft complete, pending final ToS review)
- ⏳ **In Progress:** 1/4 sites (Tayara - robots.txt ✅ done, ToS pending)
- ❌ **Not Started:** 3/4 sites (Mubawab, Tunisie Annonce, Houni.tn)

**Next Action:** Complete Tayara ToS review, then proceed with Mubawab audit.

---

## Appendix: Compliance Decision Tree

```
Start
  ↓
Does site have official API/feed?
  ├─ YES → Prefer API, check API ToS → Proceed if compliant
  └─ NO → Check robots.txt
       ↓
   Is /property-section allowed in robots.txt?
       ├─ NO → Site is EXCLUDED (respect robots.txt)
       └─ YES → Check ToS
            ↓
       Does ToS explicitly prohibit automated access?
            ├─ YES → Site is EXCLUDED or DEFERRED (legal review)
            ├─ UNCLEAR → Legal review required
            └─ NO / SILENT → Proceed with respectful scraping
                 ↓
            Implement with:
            - Respectful rate limits
            - PII access controls
            - robots.txt compliance
            - User-Agent identification
```

---

## Document History

- **2026-09-01:** Initial draft created, Tayara.tn audit template populated (awaiting ToS/robots.txt review)
- **2026-09-01:** Verified robots.txt for Tayara.tn - Allow: / confirmed, crawling permitted
