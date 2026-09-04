# Real-Estate Cross-Portal Dedup — Validation Run

**Status:** Task 3.4 ANSWERED, with a measured precision and an explicitly unmeasured recall.
**Date:** 2026-09-04
**Plan:** `plans/2026-08-31-real-estate-intelligence.md`, Region Phase 1 → Phase 3, Task 3.4.

An earlier revision of this document recorded the run as BLOCKED. Both blocks
have since been removed; what they were, and why removing them changed the
answer, is kept below because the failure modes were silent ones.

---

## Dataset

A live harvest of **1000 unique listings**, recorded through the real
`store.RecordObservation` path, with galleries fingerprinted during the walk.

| Portal | Market | Listings | Pages | Media rows | Hashed |
|---|---|---|---|---|---|
| property24 | za | 419 | 20 | 623 | 515 |
| privateproperty | za | 400 | 20 | 400 | 400 |
| tayara | tn | 181 | 19 | 1220 | 8 |
| **total** | | **1000** | 59 | **2243** | **923** |

7384 band index rows. Zero duplicate `(portal, listing_id)` pairs in the input.
Tayara's 1212 unhashed photographs are all WebP — see *Known gaps*.

## Result

| | |
|---|---|
| listings checked | 1000 |
| duplicate matches | 53 (30 unordered pairs) |
| of those, confirmed by a shared photograph | 32 |
| **precision (hand-verified sample)** | **5/7 = 71%** |
| recall | **not measured** — see below |

Precision was measured by opening the live detail page for each pair in a
12-pair sample and comparing street address and bedroom count: 5 same
property, 2 different, 5 unconfirmable (neither portal published a street
address). 71% is over the 7 that could be decided.

**Recall is unknown and should not be guessed.** It needs a hand-labelled set
of known-overlapping listings, which this dataset does not contain; the four
confirmed pairs below are a reasonable seed for building one.

### Verified true positives

| Price | property24 | privateproperty | Confirmed by |
|---|---|---|---|
| R995 000 | 48 Buitenkant St, Zonnebloem | 48 Buitenkant St | street + 1 bed + photo (hamming 8) |
| R975 000 | 59 Main Service Road, Heathfield | 59 Main Service Road | street + 1 bed |
| R1 250 000 | 1 Howe St | 1 Howe St | street + photo (hamming 2) |
| R950 000 | 13 Education street, Belhar | 13 Education Street | street + 2 beds + photo (hamming 9) |

### Verified false positives

| Price | property24 | privateproperty | Why it slipped through |
|---|---|---|---|
| R1 399 000 | 26 Polar Mews, 3 Thor Cir | 61 Riverside Mews | same suburb, price and bedroom count; different buildings |
| R1 330 000 | 3512 Woodstock Quarter | 1 Albert Road | same suburb and bedrooms, price within 0.9% |

Both survive every field the list page publishes. The field that separates
them is the **street address**, which Private Property publishes on its result
page and Property24 publishes only on the detail page.

## What was blocking this, and what changed

**1. No phash was ever written.** `check_duplicate` found candidates only
through `find_candidates_by_phash`, and `listing_media.phash` was empty on
every row ever stored: `mediahash` was referenced only for *reading* in
`store.go`, and nothing computed a hash. Every listing therefore took the
`"no candidates found"` path and the harness reported zero duplicates over a
1000-listing archive while appearing healthy.
→ `internal/realestate/media.go` now fetches and hashes galleries
(`listingharvest -hash-media N`).

**2. The semantic gate made a positive impossible without Qdrant.** `is_dup`
required `sem_score >= 0.92`, and with `SEMANTIC_WEIGHT = 0.4` the best
attainable combined score without semantics is `0.6 * 1.0 = 0.6` — below the
0.85 threshold. Semantics were mandatory, not additive, which the plan's
"0.6 * structured + 0.4 * semantic >= 0.85" does not convey.
→ When the vector store cannot answer, the score is recorded as *unknown*
rather than 0.0 and a stricter structured-only rule applies.

Three further defects surfaced only because the run was checked rather than
trusted:

**3. A ratio was being read as a quantity of proof.** `structured_similarity`
normalises by the fields *both* listings carry, so two listings agreeing on
price alone scored 1.0 — identical to two agreeing on everything. The first
working run reported **65 duplicates resting on nothing but an identical
price**; R1 695 000 is a round asking price that unrelated listings share.
→ `structured_similarity_detailed` now returns the evidence weight, and the
structured-only rule requires at least 2.0 of it.

**4. The record loader only spoke Tayara's vocabulary.** It read `city`,
`governorate`, `neighborhood`. Property24 publishes `location`; Private
Property publishes `locality`, `region`, `street`. Location was therefore
`None` on all 791 South African listings, leaving price as the only comparable
field. → aliases added; 998 of 1000 now resolve a location.

**5. Locations were compared with string equality.** "Kenilworth Upper"
against "Kenilworth Upper, Southern Suburbs" is a mismatch under `==`, and
that pattern covers most real pairs — the portals describe the same suburb at
different granularity. → compared by place-words with generic filler removed,
which is also what stops "Cape Town City Centre" agreeing with "Cape Town City
Bowl": once the filler is gone the first has no identifying word left, and an
empty comparison is recorded as *not comparable* rather than as a match.

## Threshold recommendations

Now that there is something to tune against:

1. **Keep `STRUCTURED_ONLY_MIN_EVIDENCE` at 2.0 and treat it as the real
   gate.** Score alone is not meaningful on this data; the evidence weight is.
2. **Use a shared photograph as confirmation, never as a filter.** Every pair
   within `PHASH_SAME_THRESHOLD` (12, matching `mediahash.SameThreshold`) was
   a true positive, and both false positives sat at 30 and 33. But two of five
   verified duplicates shared *no* photograph — different agents, different
   pictures — so scoring a miss against a pair rejects real duplicates. It is
   counted one way only, exactly as `mediahash` prescribes: a miss reads as
   "not proven identical", never "proven different".
3. **Do not lower `COMBINED_THRESHOLD` to compensate for missing semantics.**
   The structured-only path exists for that and is deliberately stricter.
4. **`find_candidates_by_phash` returns band matches without a distance
   check** — 16326 candidates over this archive, against 90 from blocking.
   Bands are a prefilter; confirming each with a Hamming distance (as
   `store.go` does with `SameImage`) would cut the scoring work by orders of
   magnitude at no cost to recall.

## Known gaps

- **Tayara photographs cannot be hashed.** Its CDN serves `image/webp` and
  ignores content negotiation — every `Accept` header and URL suffix tried
  returned WebP. `mediahash` is standard-library only, so this is the
  dependency decision it documents (`golang.org/x/image/webp`), not a bug.
  1212 of 1220 Tayara photographs are unhashed as a result. Cross-portal dedup
  for Tunisia is unaffected today, because Tayara is the only Tunisian portal
  currently harvested.
- **Street address is the next precision lever.** Both confirmed false
  positives would be settled by it. Private Property already publishes it on
  the result page; Property24 needs a detail-page fetch.
- **Semantic matching is still unexercised.** Every number here comes from
  the structured-only path. The combined rule has never run.

## Reproducing

    go run ./cmd/listingharvest -config ../config \
        -portals property24,privateproperty,tayara \
        -target 1000 -hash-media 2 -db <scratch>/dedup.db \
        -run harvest-v4 -out <scratch>/listings.jsonl
    go run ./cmd/listingeval -in <scratch>/listings.jsonl -config ../config
    python -m pytest tests/test_property_dedup.py

---

# Re-measurement with street addresses — 2026-09-04

The previous run named the street address as the next precision lever and
recorded bedroom mismatches as the cause of both confirmed false positives.
Both have since been addressed: `bedrooms` and `bathrooms` were added to the
two South African profiles, and a detail-page fetch tier now reads the street
address from the listing's own page.

## Method

400 listings (property24 210, privateproperty 190, 10 pages each). Dedup found
**13 matches / 7 unordered pairs**. Rather than fetch a detail page for all 400,
only the 14 listings in those pairs were fetched - the same answer for 14
requests instead of 400, at a portal that had already rate-limited this project
once. Street addresses were extracted with the SHIPPED profile pattern, not a
hand-written one, so the measurement exercises the code that runs in production.

## Result

| | |
|---|---|
| pairs proposed | 7 |
| confirmed same property | **2** |
| confirmed different | **0** |
| unconfirmable | 5 |
| precision on confirmable pairs | **2/2** |

**The denominator is too small to call this a precision figure**, and it should
not be quoted as one. Both sides of a pair need a published street address, and
Property24 carries one on roughly half its listings; five of seven pairs had a
blank on at least one side.

What can be said: the two false positives found last time were bedroom
mismatches, `bedrooms` now participates in scoring, and this run produced no
false positive at all. That is consistent with the fix working. It is not proof.

## A measurement bug worth recording

The first attempt at this reported **0/2 - a precision of zero** - and was
wrong. Private Property publishes `402 Stellenberg, 6 Protea  Road` where
Property24 publishes `6 Protea Road`: the same building, one side naming the
unit and the complex. The comparison stripped the unit prefix but did not
collapse whitespace, and `6 protea  road` (two spaces) failed containment
against `6 protea road`.

Two genuine duplicates were about to be recorded as false positives, which
would have argued for weakening a rule that is working. Address comparison
needs normalising on both sides before it is trusted - the same lesson the
`_place_agreement` change learned for suburb names, in a narrower place.

## Still open

- **Recall remains unmeasured.** It needs a hand-labelled overlap set; the
  confirmed pairs from this run and the last are the seed for one.
- **Semantic matching is still unexercised** - every number here comes from the
  structured-only path, because no vector store was reachable.
- **Street coverage is the ceiling on this method.** Roughly half of Property24
  listings publish one; pairs where neither side does cannot be settled this
  way at all.
