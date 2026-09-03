# Plan: Real-Estate Scraping & Intelligence Subsystem (Multi-Region)

**Date:** 2026-08-31
**Status:** SAVED FOR REVIEW — awaiting approval before Phase 0 execution
**Owner:** Jester core team

> **Decision gate:** This plan is saved only. Do **not** begin execution until the owner approves. Execution order is gated by the Phase 0 compliance audit findings (see Compliance Checkpoints).

---

## Overview

Add a region-oriented real-estate scraping and intelligence subsystem to Jester by **extending the existing idea-mining pipeline** (new source types + agents), not by creating a separate service. It sits **alongside** the existing idea-mining sources (Reddit/YouTube/TikTok/HN etc.) — it does not replace them.

**Build regions, one at a time (not in parallel).** Tunisia is the foundation region that proves the pipeline end-to-end and validates dedup; each subsequent region is a full build phase that repeats the same per-region gate (compliance audit → priority-portal profile → end-to-end → dedup revalidation).

### Region Phase 1 — Tunisia (foundation, 8-site priority list, 4 build targets)

| Priority | Site | Why | Build target |
|---|---|---|---|
| 1 | Tayara.tn | Highest classifieds volume, owner + agency listings | ✅ Build |
| 2 | Mubawab Tunisia | Real-estate-focused, structured filters, 50k+ listings | ✅ Build |
| 3 | Tunisie Annonce | Older classifieds platform, real-estate section | ✅ Build |
| 4 | Houni.tn | Dedicated property marketplace | ✅ Build |
| 5 | Behya | Property listings | 🔜 later |
| 6 | ImmoTunisie | Real-estate focused | 🔜 later |
| 7 | Menzili | Property classifieds | 🔜 later |
| 8 | Lyanimmo | Agency inventory | 🔜 later |

### Region Phase 2 — France

| Priority | Site | Build target |
|---|---|---|
| 1 | Leboncoin | ✅ Build |
| 2 | SeLoger | ✅ Build |
| 3 | Bien'ici | 🔜 later |
| 4 | Logic-Immo | 🔜 later |
| 5 | ParuVendu | 🔜 later |

### Region Phase 3 — Spain

| Priority | Site | Build target |
|---|---|---|
| 1 | Idealista | ✅ Build |
| 2 | Fotocasa | 🔜 later |
| 3 | Habitaclia | 🔜 later |

### Region Phase 4 — UK

| Priority | Site | Build target |
|---|---|---|
| 1 | Rightmove | ✅ Build |
| 2 | Zoopla | 🔜 later |
| 3 | OnTheMarket | 🔜 later |

### Region Phase 5 — Germany

| Priority | Site | Build target |
|---|---|---|
| 1 | ImmoScout24 | ✅ Build |
| 2 | Immowelt | 🔜 later |
| 3 | Immonet | 🔜 later |

### Region Phase 6 — Italy

| Priority | Site | Build target |
|---|---|---|
| 1 | Immobiliare.it | ✅ Build |
| 2 | Casa.it | 🔜 later |
| 3 | Idealista Italy | 🔜 later |

### Region Phase 7 — USA

| Priority | Site | Build target |
|---|---|---|
| 1 | Zillow | ✅ Build |
| 2 | Realtor.com | ✅ Build |
| 3 | Redfin | 🔜 later |
| 4 | Homes.com | 🔜 later |
| 5 | Craigslist | 🔜 later |

Each region's ✅ Build targets are the build scope for that region phase; 🔜 later sites are carried in the region register but not built in that region's phase. Region build order is decided per-region by the compliance audit matrix (compliance posture, data quality, effort), **not** raw listing volume. The priority pairs above (e.g. Leboncoin+SeLoger, Zillow+Realtor.com) are the initial ✅ Build target set for each region; findings from that region's audit may re-order them.

---

## 1. Architectural Fit & Tradeoffs

**Decision: extend the existing pipeline** (new `source: realestate_listing` types + dedicated agents) rather than a standalone microservice.

**Reuses:**
- `go/internal/siteprofile/` — config-driven portal parser (`ldjson` / `anchored` modes)
- `go/internal/mediahash/` — dHash image dedup (`MatchByPhash`, `Decode`)
- `go/internal/store/` — `listing`, `listing_observation`, `listing_media`, `listing_media_band` tables
- `ingest_batch.kind='listing'` — bypasses LLM extractor
- Observation append (never update) → automatic price-history
- `Listing.GalleryHash()` — fingerprints ordered dHashes
- `scripts/retention.sh`, `scripts/migrate.sh` — unchanged
- Config registry pattern from `config/sources.yaml`

**Key tradeoff:** reusing the pipeline trades "cleaner isolation" for lower ops overhead, shared retention/migration, and consistent run lifecycle. This is the right call for Phase 1; a dedicated service remains a documented future option if real-estate load decouples.

---

## 2. Schema & Boundary Ownership

| Concern | Owner | Implementation |
|---|---|---|
| Real-estate source config | **Go** | NEW `realestate_config` (source→portal profile mapping, JSON payload schema) |
| Property alerts | **Python** | NEW `property_alerts` |
| Property analytics | **Python** | NEW `property_analytics` |
| Listing core data | Go | EXISTING `listing` + observation/media/band tables — **no schema change** |

**Normalized Property schema (24 fields)** — the canonical cross-source model every portal profile maps into. Extended/non-core fields go in `Listing.Payload` (below); the rest map to existing columns or derived analytics:

| # | Field | Type | Source / destination |
|---|---|---|---|
| 1 | `source` | string | Listing.Portal |
| 2 | `source_url` | string | Listing.URL |
| 3 | `source_id` | string | Listing.ListingID |
| 4 | `title` | string | Payload |
| 5 | `description` | text | Payload |
| 6 | `property_type` | enum | Payload `property_type` |
| 7 | `transaction_type` | enum (sale/rent) | Payload `transaction_type` |
| 8 | `price` | float | Listing.Price |
| 9 | `currency` | string | Listing.Currency |
| 10 | `surface` | float (m²) | Payload `surface` |
| 11 | `rooms` | int | Payload `rooms` |
| 12 | `bedrooms` | int | Payload `bedrooms` |
| 13 | `bathrooms` | int | Payload `bathrooms` |
| 14 | `floor` | int | Payload `floor` |
| 15 | `city` | string | Payload `geo.city` |
| 16 | `governorate` | string | Payload `geo.governorate` |
| 17 | `neighborhood` | string | Payload `geo.neighborhood` |
| 18 | `latitude` | float | Payload `geo.lat` |
| 19 | `longitude` | float | Payload `geo.lng` |
| 20 | `images[]` | []string | Listing.Media[] |
| 21 | `seller` | string | Payload `seller.name` |
| 22 | `seller_type` | enum (owner/agency) | Payload `seller.type` |
| 23 | `phone` | string (PII — consent-gated) | Payload `seller.phone` |
| 24 | `published_at` / `updated_at` / `scraped_at` | datetime | Observation timestamps |

**Field mapping to existing tables:**
- `source` → `Listing.Portal`
- `source_url` → `Listing.URL`
- `source_id` → `Listing.ListingID`
- `price` → `Listing.Price`
- `currency` → `Listing.Currency`
- `images[]` → `Listing.Media[]`
- `scraped_at` → `Listing.Observation.ScrapedAt`
- **Extended fields** (surface, rooms, price_m2, geo, phone) → `Listing.Payload` JSON blob (**no schema change**)

---

## 3. Region-Based Build Order

**Region phases are built one at a time (not in parallel).** Region Phase 1 (Tunisia) is the foundation: it proves the pipeline end-to-end and validates dedup. Every subsequent region repeats the same per-region gate: **compliance audit → priority-portal profile(s) → end-to-end → dedup revalidation**.

Per-region ✅ Build target order is decided by that region's compliance audit matrix (compliance posture, data quality, effort), **not** raw listing volume. The priority pairs below are the initial build scope; audit findings may re-order them. 🔜 later sites are carried in the region register but not built in that region's phase.

**Region Phase 1 — Tunisia (foundation):**
1. **Phase 0 — Compliance audit** (before ANY scraper code)
2. **Phase 1 — Tayara.tn** end-to-end (profile + worker case + smoke test)
3. **Phase 2 — Mubawab Tunisia** end-to-end
4. **Phase 3 — Two-source dedup validation** (requires two sources live — dedup only provable with overlap)
5. **Phase 4 — Tunisie Annonce + Houni.tn** end-to-end
6. **Phase 5 — Property alerts + analytics agents** (Python)
7. **Phase 6 — Retention/migration confirmation + docs handoff**

**Region Phase 2 — France:** audit → Leboncoin → SeLoger → dedup revalidation (🔜 later: Bien'ici, Logic-Immo, ParuVendu)
**Region Phase 3 — Spain:** audit → Idealista → dedup revalidation (🔜 later: Fotocasa, Habitaclia)
**Region Phase 4 — UK:** audit → Rightmove → dedup revalidation (🔜 later: Zoopla, OnTheMarket)
**Region Phase 5 — Germany:** audit → ImmoScout24 → dedup revalidation (🔜 later: Immowelt, Immonet)
**Region Phase 6 — Italy:** audit → Immobiliare.it → dedup revalidation (🔜 later: Casa.it, Idealista Italy)
**Region Phase 7 — USA:** audit → Zillow + Realtor.com → dedup revalidation (🔜 later: Redfin, Homes.com, Craigslist)

---

## 4. Dedup Design (4 layers)

**The core hard problem** in real-estate scraping is **cross-source duplicate detection**: the same physical property appears on multiple portals with different titles, descriptors, and seller text. Without dedup, price analytics, days-on-market, and neighborhood averages are distorted by double-counting.

Concrete example (the shape we must resolve correctly):
- Mubawab: *"S+2 haut standing à La Soukra - 180m²"*
- Tayara: *"Appartement S2 La Soukra 180 m2"*
- An agency site: *"Superbe appartement 2 chambres La Soukra"*

These are likely the **same apartment**, but share no title tokens — keyword/title matching alone fails. Dedup therefore layers:

1. **Go-side exact** — content hash + `GalleryHash()` ordered fingerprint (same portal)
2. **Cross-portal image** — `MatchByPhash` (dHash band-indexed, Hamming ≤12). Strong cue: the same photo set reused across portals.
3. **NEW structured-field** — price ±2%, surface ±5%, location (governorate/neighborhood), rooms, seller. Normalizes "S2"/"2 chambres"/"S+2" → `bedrooms=2`.
4. **NEW semantic embedding** — Qdrant collection `properties__<db-stem>`, threshold **0.92** (vs. comment dedup 0.87). Catches paraphrased titles the structured layer misses.

**Combined score:** `0.6 * structured + 0.4 * semantic >= 0.85` → duplicate. Validation (Phase 3) proves precision/recall on real two-source overlap before the alerts layer trusts it.

---

## 4.A — Intelligence Layer Capabilities (explicit)

The point of the subsystem is not just to scrape — it is to turn normalized, deduped listings into **decision-useful intelligence**. The analytic layer (`property_analytics.py`) and alert layer (`property_alerts.py`) together must deliver, at minimum, these explicit capabilities:

| Capability | Where | Example unit of analysis |
|---|---|---|
| **Price per m² vs. neighborhood/city averages** | `property_analytics.py` | E.g. computed median price/m² for an apartment by surface band in La Soukra vs. Tunis governorate avg — surfaces a listing as over/under market (e.g. 2,500 DT/m² vs. a 2,100 DT/m² local median) |
| **New-listing alerts** | `property_alerts.py` | Fire when a listing matching user criteria (area, price range, rooms, surface) first appears |
| **Price-reduction alerts** | `property_alerts.py` | Fire when an observed listing's price drops (leverage append-only observation history) |
| **Days-on-market** | `property_analytics.py` | Duration from first observation to sold/removed/current, per portal |
| **Agency vs. owner classification** | `property_analytics.py` | Uses `seller_type`; owner-listed properties often price differently |
| **Duplicate / suspicious detection** | `property_dedup.py` + analytics | Duplicate rate across portals; suspicious patterns (listed well below market, quantity of phones reused across listings) |
| **Neighborhood trends / historical changes** | `property_analytics.py` | Price-by-m² trend lines over time from observation history, per governorate/neighborhood |
| **Geographic price heatmaps** | `property_analytics.py` (output artifact) | Price/m² aggregated onto `geo.lat`/`geo.lng` buckets for visualization |
| **Optional AI property summaries** | `python/jester/agents/*.py` | LLM-generated human-readable summary of a listing/property from its normalized fields (opt-in, rate-limited) |

These capabilities depend on the dedup layer being trustworthy — which is why Phase 3 validation gates Phase 5 (see build order).

---

## 5. Reused vs. Net-New Infrastructure

| Reused (no change) | Net-new (built region-by-region) |
|---|---|
| `siteprofile` parser | `realestate_config` table (Go) — one row per portal |
| `mediahash` image dedup | `property_dedup.py` (Python) — region extensible |
| `store` listing tables | `property_analytics.py`, `property_alerts.py` — region parametrized |
| `ingest_batch.kind='listing'` | 12 portal profiles (`config/profiles/*.yaml`) across 7 regions |
| `retention.sh`, `migrate.sh` | worker `fetchSource()` portal cases (4 Tunisia + 8 foreign) |
| config registry | Qdrant `properties__*` collection(s) |

The same net-new components are built once in Region Phase 1 (Tunisia) and **reused/extended** by each later region — the shared architecture is not re-implemented per region. Each region only adds its own `config/profiles/*.yaml`, its `fetchSource()` cases, its compliance audit section, and its dedup-validation section.

---

## 6. Compliance Checkpoints (non-negotiable)

Before ANY scraper code for a site, per-site audit of:
- ToS (automated-access clauses)
- `robots.txt`
- Official API / available feed
- Phone & contact-data consent (PII)
- GDPR / DPA posture (scrape-existing vs. new data)

**Output:** `docs/realestate-compliance-audit.md`.

Compliance is a **first-class input to the per-site build order**, not a footnote:

- **Official API / feed availability can re-prioritize a site ABOVE a higher-volume API-less one.** A compliant, permissioned integration that yields clean structured data beats a larger but legally restricted or API-less feed — it is safer, cheaper to maintain, and more reliable. The build-order matrix in the audit must rank sites by `(compliance posture, data quality, effort)` — not raw listing volume alone.
- **A site with restrictive ToS, no official API, or blocking `robots.txt` may move later in the build order or be excluded entirely.** No anti-bot/bypass scraping is used to get around an access restriction; either the site is integrated within its rules or it is deferred/excluded.
- The audit records, per site: (a) official API/feed availability, (b) any ToS restriction on automated access, (c) phone/contact-data consent decision (PII), (d) GDPR/DPA posture (scrape-existing vs. new data). Findings update the matrix before that site's scraper phase begins.

---

## Tasks

> Each task is bite-sized with a TDD cycle. The final task of each phase is always the **Verification** task.
>
> **Region phases are built one at a time (not in parallel).** Region Phase 1 (Tunisia) is the foundation — detailed phases 0–6 below. Each subsequent Region Phase 2–7 repeats the same per-region gate: **compliance audit → priority-portal profile(s) → end-to-end → dedup revalidation**. Region phase tasks are grouped below; a later region is not started until the current one's phases are complete and verified.

### Region Phase 1 — Tunisia (foundation, detailed phases)

#### Phase 0 — Compliance gate

- [ ] **Task 0.1** — Draft `docs/realestate-compliance-audit.md` template with per-site checklist (ToS, robots.txt, API/feed, PII consent, GDPR/DPA) for the 4 Tunisia sites; populate findings for Tayara.tn first.
- [ ] **Task 0.2 — Verification** — Reviewer confirms the audit covers all 4 sites, and that the compliance status column explicitly records (a) official API/feed availability, (b) any ToS restriction on automated access, (c) phone/contact consent decision, and (d) GDPR/DPA posture. Build order matrix reflects the findings.

#### Phase 1 — Tayara.tn end-to-end

- [ ] **Task 1.1** — Author `config/profiles/tayara.yaml` using the `siteprofile` schema (`ldjson` or `anchored` mode) with stable field selectors, and add a `realestate_config` row mapping `source_id`/`source_url`/`price`/`currency`/`images` and extended fields into `listing.Payload`.
- [ ] **Task 1.2** — Register the Tayara case in `go/cmd/worker/main.go` `fetchSource()` switch, emitting `ingest_batch.kind='listing'` and calling `RecordObservation()` (append-only price history).
- [ ] **Task 1.3** — Add Go unit test feeding a recorded Tayara sample HTML through the profile and asserting the extracted fields land in the correct `listing`/observation/media columns.
- [ ] **Task 1.4 — Verification** — Run the Go test suite; run a **smoke ingest** against a small Tayara sample and confirm a `listing` + `listing_observation` + `listing_media`(+`_band`) rows appear with correct `portal`/`listing_id`/`price` and that a second ingest appends a new observation (price history, no update).

#### Phase 2 — Mubawab Tunisia end-to-end

- [ ] **Task 2.1** — Author `config/profiles/mubawab.yaml` + `realestate_config` row (same mapping as Phase 1).
- [ ] **Task 2.2** — Register Mubawab case in `fetchSource()` switch.
- [ ] **Task 2.3** — Add Go unit test with recorded Mubawab sample HTML.
- [ ] **Task 2.4 — Verification** — Run Go test suite; smoke-ingest Mubawab sample and confirm listing rows land correctly; confirm **two distinct sources now produce independent observation streams** (prerequisite for dedup validation).

#### Phase 3 — Two-source dedup validation

- [ ] **Task 3.1** — Implement `python/jester/agents/property_dedup.py`: cross-portal `MatchByPhash` layer + structured-field matcher (price ±2%, surface ±5%, location, rooms, seller) + semantic matcher (Qdrant `properties__*`, threshold 0.92).
- [ ] **Task 3.2** — Implement combined scoring `0.6*structured + 0.4*semantic >= 0.85` and a lab harness that ingests known-overlap fixtures from both Tayara and Mubawab.
- [ ] **Task 3.3** — Add pytest fixtures using records from both live sources (real overlapping listings if compliant) to prove both duplicate-detection and non-duplicate cases.
- [ ] **Task 3.4 — Verification** — `pytest` green; run the lab harness over the real two-source dataset and produce `docs/realestate-dedup-validation.md` with precision/recall numbers, false-positive examples, and any threshold tuning recommendation.

#### Phase 4 — Tunisie Annonce + Houni.tn end-to-end

- [ ] **Task 4.1** — Author `config/profiles/tunisieannonce.yaml` + `config/profiles/houni.yaml` and `realestate_config` rows.
- [ ] **Task 4.2** — Register both cases in `fetchSource()` switch.
- [ ] **Task 4.3** — Add Go unit tests with recorded sample HTML for each; add cross-portal dedup fixtures for both against Tayara.
- [ ] **Task 4.4 — Verification** — Run full Go + pytest suites; smoke-ingest both; confirm all 4 sources produce consistent observation streams and cross-portal duplicates are flagged through the combined matcher.

#### Phase 5 — Property alerts & analytics agents (Python)

- [ ] **Task 5.1** — Implement `python/jester/agents/property_alerts.py`: subscribe criteria (area, price range, room count, surface) evaluated against new observations; output digest + channel-ready payload (consistent with existing alert conventions).
- [ ] **Task 5.2** — Implement `python/jester/agents/property_analytics.py`: price-per-m², price history by portal/area, listing-count trends, dedup rates; write to `property_analytics`.
- [ ] **Task 5.3** — Wire CLI subcommands (`jester property-alerts`, `jester property-analytics`) in `python/jester/cli.py`; add pytest for both agents over seeded fixture data.
- [ ] **Task 5.4 — Verification** — `pytest` green; CLI runs produce expected alert digest and analytics tables; alert fire/no-fire matches fixture criteria exactly.

#### Phase 6 — Retention/migration + docs handoff

- [ ] **Task 6.1** — Confirm `scripts/retention.sh` and `scripts/migrate.sh` treat the real-estate tables (`listing_*`) correctly; adjust globs/table lists if needed and add a test for the new `realestate_config`/`property_*` tables.
- [ ] **Task 6.2 — Verification** — Run retention/migration in a scratch DB; confirm no real-estate rows are dropped unexpectedly and indexes on `listing_media_band` and new tables are present; update `README.md` source/section list to reflect the 4 new Tunisia portals + agents.

---

### Region Phase 2 — France

> Region gate: **compliance audit → priority-portal profiles → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (France)

- [ ] **Task FR-0.1** — Extend `docs/realestate-compliance-audit.md` with the France site checklist (ToS, robots.txt, API/feed, PII consent, GDPR/DPA) for Leboncoin, SeLoger, and the 🔜 later sites (Bien'ici, Logic-Immo, ParuVendu). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task FR-0.2 — Verification** — Reviewer confirms the audit records, for each France site: (a) official API/feed availability, (b) any ToS restriction on automated access, (c) phone/contact consent decision, and (d) GDPR/DPA posture. Matrix may re-order the priority pair.

#### Phase 1 — Leboncoin end-to-end

- [ ] **Task FR-1.1** — Author `config/profiles/leboncoin.yaml` + `realestate_config` row (mapping `source_id`/`source_url`/`price`/`currency`/`images` and extended fields into `listing.Payload`).
- [ ] **Task FR-1.2** — Register the Leboncoin case in `go/cmd/worker/main.go` `fetchSource()` switch, emitting `ingest_batch.kind='listing'` and calling `RecordObservation()`.
- [ ] **Task FR-1.3** — Add Go unit test with recorded Leboncoin sample HTML.
- [ ] **Task FR-1.4 — Verification** — Run Go test suite; smoke-ingest a small Leboncoin sample and confirm listing/observation/media rows land correctly; second ingest appends (price history, no update).

#### Phase 2 — SeLoger end-to-end

- [ ] **Task FR-2.1** — Author `config/profiles/seloger.yaml` + `realestate_config` row.
- [ ] **Task FR-2.2** — Register the SeLoger case in `fetchSource()` switch.
- [ ] **Task FR-2.3** — Add Go unit test with recorded SeLoger sample HTML.
- [ ] **Task FR-2.4 — Verification** — Run Go test suite; smoke-ingest a SeLoger sample; confirm two distinct France sources now produce independent observation streams (prerequisite for dedup revalidation).

#### Phase 3 — France dedup revalidation

- [ ] **Task FR-3.1** — Add cross-portal dedup fixtures for France (Leboncoin vs SeLoger, plus vs. Tunisia if overlapping regions) to the `property_dedup.py` lab harness.
- [ ] **Task FR-3.2 — Verification** — `pytest` green; run the lab harness over the real two-source France dataset; update `docs/realestate-dedup-validation.md` with precision/recall, false-positive examples, and any threshold tuning for the France market.

---

### Region Phase 3 — Spain

> Region gate: **compliance audit → priority-portal profile → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (Spain)

- [ ] **Task ES-0.1** — Extend `docs/realestate-compliance-audit.md` with the Spain checklist for Idealista and the 🔜 later sites (Fotocasa, Habitaclia). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task ES-0.2 — Verification** — Reviewer confirms the audit records (a)–(d) for each Spain site; matrix may re-order the priority target.

#### Phase 1 — Idealista end-to-end

- [ ] **Task ES-1.1** — Author `config/profiles/idealista.yaml` + `realestate_config` row.
- [ ] **Task ES-1.2** — Register the Idealista case in `fetchSource()` switch.
- [ ] **Task ES-1.3** — Add Go unit test with recorded Idealista sample HTML.
- [ ] **Task ES-1.4 — Verification** — Run Go test suite; smoke-ingest an Idealista sample; confirm listing/observation/media rows land correctly and price history appends.

#### Phase 2 — Spain dedup revalidation

- [ ] **Task ES-2.1** — Add cross-portal dedup fixtures for Spain (Idealista vs. other live sources) to the lab harness.
- [ ] **Task ES-2.2 — Verification** — `pytest` green; run the lab harness over the real Spanish dataset; update `docs/realestate-dedup-validation.md` for the Spanish market.

---

### Region Phase 4 — UK

> Region gate: **compliance audit → priority-portal profile → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (UK)

- [ ] **Task UK-0.1** — Extend `docs/realestate-compliance-audit.md` with the UK checklist for Rightmove and the 🔜 later sites (Zoopla, OnTheMarket). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task UK-0.2 — Verification** — Reviewer confirms the audit records (a)–(d) for each UK site; matrix may re-order the priority target.

#### Phase 1 — Rightmove end-to-end

- [ ] **Task UK-1.1** — Author `config/profiles/rightmove.yaml` + `realestate_config` row.
- [ ] **Task UK-1.2** — Register the Rightmove case in `fetchSource()` switch.
- [ ] **Task UK-1.3** — Add Go unit test with recorded Rightmove sample HTML.
- [ ] **Task UK-1.4 — Verification** — Run Go test suite; smoke-ingest a Rightmove sample; confirm listing/observation/media rows land correctly and price history appends.

#### Phase 2 — UK dedup revalidation

- [ ] **Task UK-2.1** — Add cross-portal dedup fixtures for the UK (Rightmove vs. other live sources) to the lab harness.
- [ ] **Task UK-2.2 — Verification** — `pytest` green; run the lab harness over the real UK dataset; update `docs/realestate-dedup-validation.md` for the UK market.

---

### Region Phase 5 — Germany

> Region gate: **compliance audit → priority-portal profile → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (Germany)

- [ ] **Task DE-0.1** — Extend `docs/realestate-compliance-audit.md` with the Germany checklist for ImmoScout24 and the 🔜 later sites (Immowelt, Immonet). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task DE-0.2 — Verification** — Reviewer confirms the audit records (a)–(d) for each Germany site; matrix may re-order the priority target.

#### Phase 1 — ImmoScout24 end-to-end

- [ ] **Task DE-1.1** — Author `config/profiles/immoscout24.yaml` + `realestate_config` row.
- [ ] **Task DE-1.2** — Register the ImmoScout24 case in `fetchSource()` switch.
- [ ] **Task DE-1.3** — Add Go unit test with recorded ImmoScout24 sample HTML.
- [ ] **Task DE-1.4 — Verification** — Run Go test suite; smoke-ingest an ImmoScout24 sample; confirm listing/observation/media rows land correctly and price history appends.

#### Phase 2 — Germany dedup revalidation

- [ ] **Task DE-2.1** — Add cross-portal dedup fixtures for Germany (ImmoScout24 vs. other live sources) to the lab harness.
- [ ] **Task DE-2.2 — Verification** — `pytest` green; run the lab harness over the real German dataset; update `docs/realestate-dedup-validation.md` for the German market.

---

### Region Phase 6 — Italy

> Region gate: **compliance audit → priority-portal profile → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (Italy)

- [ ] **Task IT-0.1** — Extend `docs/realestate-compliance-audit.md` with the Italy checklist for Immobiliare.it and the 🔜 later sites (Casa.it, Idealista Italy). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task IT-0.2 — Verification** — Reviewer confirms the audit records (a)–(d) for each Italy site; matrix may re-order the priority target.

#### Phase 1 — Immobiliare.it end-to-end

- [ ] **Task IT-1.1** — Author `config/profiles/immobiliare.yaml` + `realestate_config` row.
- [ ] **Task IT-1.2** — Register the Immobiliare.it case in `fetchSource()` switch.
- [ ] **Task IT-1.3** — Add Go unit test with recorded Immobiliare.it sample HTML.
- [ ] **Task IT-1.4 — Verification** — Run Go test suite; smoke-ingest an Immobiliare.it sample; confirm listing/observation/media rows land correctly and price history appends.

#### Phase 2 — Italy dedup revalidation

- [ ] **Task IT-2.1** — Add cross-portal dedup fixtures for Italy (Immobiliare.it vs. other live sources) to the lab harness.
- [ ] **Task IT-2.2 — Verification** — `pytest` green; run the lab harness over the real Italian dataset; update `docs/realestate-dedup-validation.md` for the Italian market.

---

### Region Phase 7 — USA

> Region gate: **compliance audit → priority-portal profiles → end-to-end → dedup revalidation**.

#### Phase 0 — Compliance audit (USA)

- [ ] **Task US-0.1** — Extend `docs/realestate-compliance-audit.md` with the USA checklist for Zillow, Realtor.com, and the 🔜 later sites (Redfin, Homes.com, Craigslist). Rank the build-order matrix by `(compliance posture, data quality, effort)`.
- [ ] **Task US-0.2 — Verification** — Reviewer confirms the audit records, for each USA site: (a) official API/feed availability, (b) any ToS restriction on automated access, (c) phone/contact consent decision, and (d) GDPR/DPA posture. Matrix may re-order the priority pair.

#### Phase 1 — Zillow end-to-end

- [ ] **Task US-1.1** — Author `config/profiles/zillow.yaml` + `realestate_config` row.
- [ ] **Task US-1.2** — Register the Zillow case in `fetchSource()` switch.
- [ ] **Task US-1.3** — Add Go unit test with recorded Zillow sample HTML.
- [ ] **Task US-1.4 — Verification** — Run Go test suite; smoke-ingest a Zillow sample; confirm listing/observation/media rows land correctly and price history appends.

#### Phase 2 — Realtor.com end-to-end

- [ ] **Task US-2.1** — Author `config/profiles/realtor.yaml` + `realestate_config` row.
- [ ] **Task US-2.2** — Register the Realtor.com case in `fetchSource()` switch.
- [ ] **Task US-2.3** — Add Go unit test with recorded Realtor.com sample HTML.
- [ ] **Task US-2.4 — Verification** — Run Go test suite; smoke-ingest a Realtor.com sample; confirm two distinct USA sources now produce independent observation streams (prerequisite for dedup revalidation).

#### Phase 3 — USA dedup revalidation

- [ ] **Task US-3.1** — Add cross-portal dedup fixtures for USA (Zillow vs Realtor.com, plus vs. other live sources) to the lab harness.
- [ ] **Task US-3.2 — Verification** — `pytest` green; run the lab harness over the real two-source USA dataset; update `docs/realestate-dedup-validation.md` with precision/recall, false-positive examples, and any threshold tuning for the US market.

---

## Region Flex Notes (apply per region as it is built)

- **Currency**: store `currency` per listing; analytical aggregations must be currency-aware when a region has multiple currencies. Tunisia uses DT; the France/Spain/UK/Germany/Italy/USA phases each have their own currency (EUR/GBP/USD) — the normalized schema already carries `currency` per listing, so cross-region analytics must never aggregate across currencies.
- **Multi-language**: portal selectors and alert criteria must tolerate region-localized labels (Arabic/French/Spanish/English/German/Italian). A portal profile's selectors and a region's alert criteria are defined within that region's phase.
- **Administrative taxonomy**: Tunisia normalizes governorates; each later region defines its own region taxonomy in the geo field (France départements, Spain provincias, UK local authorities, Germany Bundesländer, Italy provincie/regioni, USA states/counties). The geo field and `city`/`neighborhood` are region-parametrized per the schema.

---

## Definition of Done

**Multi-region scope (7 regions, 12 portal profiles):** Tunisia (foundation) + France + Spain + UK + Germany + Italy + USA.

- **Region Phase 1 (Tunisia) — foundation**: the 4 ✅ Build Tunisia portals (Tayara.tn, Mubawab, Tunisie Annonce, Houni.tn) ingest through the existing pipeline (config-driven, `kind='listing'`).
- **Each subsequent region phase (2–7)**: all of that region's ✅ Build targets (Leboncoin/SeLoger, Idealista, Rightmove, ImmoScout24, Immobiliare.it, Zillow/Realtor.com) ingest through the pipeline and pass that region's own gate — **compliance audit → priority-portal profile(s) → end-to-end → dedup revalidation** — before the next region is started (regions built one at a time, not in parallel).
- Price history via observation append; cross-portal + semantic dedup validated with real two-source overlap per region → `docs/realestate-dedup-validation.md` updated for each region's market.
- Compliance gate satisfied for all scraped sites → `docs/realestate-compliance-audit.md` covers all 7 regions.
- Alerts + analytics agents run via CLI and are tested (region-parametrized, currency-aware — cross-region analytics never aggregate across currencies).
- No schema migration to core `listing*` tables; extended fields live in `listing.Payload`.
- README updated (source/section list reflects all built region portals).
