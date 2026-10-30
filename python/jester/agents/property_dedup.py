"""Cross-portal property deduplication for real-estate listings.

Implements the 4-layer dedup strategy:
1. Go-side exact (content hash + GalleryHash) - same portal
2. Cross-portal image (MatchByPhash, dHash band-indexed, Hamming ≤12)
3. Structured-field (price ±2%, surface ±5%, location, rooms, seller)
4. Semantic embedding (Qdrant collection `properties__jester`, threshold 0.92),
   which has to be filled by sync_property_vectors before it can answer

Combined score: 0.6 * structured + 0.4 * semantic >= 0.85 → duplicate, or the
stricter structured-only bar when the semantic layer has no answer at all.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

from jester.dedup_sweep import cosine
from jester.store import open_db
from jester.vector import VectorStore, UnavailableVectorStore


@dataclass
class ListingRecord:
    """Normalized listing record from the database."""
    portal: str
    listing_id: str
    url: str
    price: Optional[int]
    currency: str
    surface: Optional[float]
    rooms: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    city: Optional[str]
    governorate: Optional[str]
    neighborhood: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    seller: Optional[str]
    seller_type: Optional[str]
    payload: dict
    gallery_hash: Optional[str]
    phashes: list[str]


@dataclass
class DedupResult:
    """Result of a duplicate detection check."""
    is_duplicate: bool
    structured_score: float
    #: None when the semantic layer had no answer. That is a different fact
    #: from 0.0 and is recorded differently: 0.0 means two descriptions were
    #: compared and did not match, None means they never were.
    semantic_score: Optional[float]
    combined_score: float
    matched_portal: Optional[str]
    matched_listing_id: Optional[str]
    reasons: list[str]


# Thresholds from the plan
STRUCTURED_WEIGHT = 0.6
SEMANTIC_WEIGHT = 0.4
COMBINED_THRESHOLD = 0.85
SEMANTIC_THRESHOLD = 0.92
PRICE_TOLERANCE_PCT = 0.02  # ±2%
SURFACE_TOLERANCE_PCT = 0.05  # ±5%

# When the vector store cannot answer, semantics are UNKNOWN, not 0.0. Scoring
# them as zero made a duplicate unreportable twice over: the third clause of
# is_dup can never be satisfied, and 0.6 * 1.0 = 0.6 never reaches the 0.85
# combined threshold either. A validation run over 1000 real listings returned
# zero duplicates for that reason alone - and the store was not down, it had
# simply never been written, so "cannot answer" was the ordinary state of the
# world here. Rather than pretend, the check falls back to a deliberately
# stricter structured-only bar and SAYS that it did.
STRUCTURED_ONLY_THRESHOLD = 0.95

# ...and it must stand on more than one field. Price is the only field every
# portal publishes, so a ratio-based score reaches 1.0 on price agreement
# alone. Asking prices are round: R1 695 000 appears on unrelated listings all
# day. 2.0 is price (1.0) plus location (1.0), the least that distinguishes a
# match from a coincidence. Measured on a 1000-listing archive: without this
# floor the structured-only rule reported 65 duplicates, every one of them
# resting on nothing but an identical price.
STRUCTURED_ONLY_MIN_EVIDENCE = 2.0

# Hamming distance at which two dHashes are the same photograph. Mirrors
# mediahash.SameThreshold on the Go side so both languages call the same pair
# of images identical.
PHASH_SAME_THRESHOLD = 12

# Blocking is recall-oriented: it decides what gets SCORED, not what counts as
# a duplicate, so it may be loose. The cap stops one very common asking price
# from pulling in half a portal.
BLOCKING_CANDIDATE_LIMIT = 200

# Words that identify nothing on their own. "Cape Town City Centre" and
# "Gardens, Cape Town City Bowl" share three of these and are not the same
# place; "Kenilworth Upper" and "Kenilworth Upper, Southern Suburbs" share a
# real one.
GENERIC_LOCALITY_TOKENS = frozenset({
    "western", "eastern", "northern", "southern", "north", "south", "east",
    "west", "cape", "town", "city", "centre", "center", "central", "bowl",
    "suburbs", "suburb", "estate", "africa", "the", "and",
})


def load_listing_from_obs(db: sqlite3.Connection, portal: str, listing_id: str) -> Optional[ListingRecord]:
    """Load a normalized listing record from the latest observation."""
    row = db.execute(
        """SELECT o.portal, o.listing_id, l.url, o.price, o.currency, o.payload,
                  o.gallery_hash, l.listing_id
           FROM listing_observation o
           JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
           WHERE o.portal = ? AND o.listing_id = ?
           ORDER BY o.id DESC LIMIT 1""",
        (portal, listing_id),
    ).fetchone()
    if not row:
        return None

    portal, listing_id, url, price, currency, payload_json, gallery_hash, _ = row
    payload = json.loads(payload_json) if payload_json else {}

    # Get phashes from listing_media for this observation
    phashes = []
    obs_row = db.execute(
        "SELECT id FROM listing_observation WHERE portal = ? AND listing_id = ? ORDER BY id DESC LIMIT 1",
        (portal, listing_id),
    ).fetchone()
    if obs_row:
        obs_id = obs_row[0]
        media_rows = db.execute(
            "SELECT phash FROM listing_media WHERE observation_id = ? AND phash IS NOT NULL",
            (obs_id,),
        ).fetchall()
        phashes = [r[0] for r in media_rows]

    # Extract structured fields from payload
    def get_float(key):
        v = payload.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def get_int(key):
        v = payload.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def get_str(key):
        v = payload.get(key)
        if v is None:
            return None
        if isinstance(v, list):
            v = v[0] if v else None
        return str(v) if v else None

    def get_first(*keys):
        """First non-empty value among several names for the same fact.

        The portals do not share a vocabulary and this loader only knew
        Tayara's. Property24 publishes `location`, Private Property publishes
        `locality`/`region`/`street`, so on every South African listing city,
        governorate and neighborhood all came back None - which meant
        structured_similarity had no location to compare and fell back to
        price as its ONLY evidence. Two listings agreeing on price alone then
        scored 1.0, identical to two agreeing on everything.
        """
        for k in keys:
            v = get_str(k)
            if v:
                return v
        return None

    return ListingRecord(
        portal=portal,
        listing_id=listing_id,
        url=url,
        price=price,
        currency=currency,
        surface=get_float("surface"),
        rooms=get_int("rooms"),
        bedrooms=get_int("bedrooms"),
        bathrooms=get_int("bathrooms"),
        city=get_first("city", "locality", "location"),
        governorate=get_first("governorate", "region", "province"),
        neighborhood=get_first("neighborhood", "suburb", "street"),
        latitude=get_float("latitude"),
        longitude=get_float("longitude"),
        seller=get_str("seller"),
        seller_type=get_str("seller_type"),
        payload=payload,
        gallery_hash=gallery_hash,
        phashes=phashes,
    )


def _hamming(a: str, b: str) -> int:
    """Bit distance between two hex dHashes; 64 when either will not parse."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def _place_agreement(va, vb):
    """How far two place descriptions agree, or None when not comparable.

    Containment rather than overlap: the coarser description legitimately
    carries extra words, so "Kenilworth Upper" inside "Kenilworth Upper,
    Southern Suburbs" is full agreement, not half.
    """
    if not va or not vb:
        return None
    ta = _place_words(va)
    tb = _place_words(vb)
    if not ta or not tb:
        # Nothing identifying survived on one side. That is an absence of
        # evidence, not evidence of a match.
        return None
    shared = ta & tb
    if not shared:
        return 0.0
    return len(shared) / min(len(ta), len(tb))


def _place_words(text) -> set:
    """Identifying words in one place description."""
    cleaned = "".join(c if c.isalnum() else " " for c in str(text).lower())
    return {
        w for w in cleaned.split()
        if len(w) > 2 and w not in GENERIC_LOCALITY_TOKENS and not w.isdigit()
    }


def structured_similarity(a: ListingRecord, b: ListingRecord) -> tuple[float, list[str]]:
    """Structured-field similarity (0.0 to 1.0) with reasons."""
    score, reasons, _ = structured_similarity_detailed(a, b)
    return score, reasons


def structured_similarity_detailed(
    a: ListingRecord, b: ListingRecord
) -> tuple[float, list[str], float]:
    """As structured_similarity, plus the EVIDENCE the score stands on.

    The third value is the total weight of the fields both listings actually
    carried. It matters because the score is a ratio: two listings that agree
    on price and have nothing else in common score 1.0, exactly like two that
    agree on price, surface, location, rooms and seller. Without the weight
    there is no way to tell a strong match from a lucky one, and asking prices
    are round numbers that thousands of unrelated listings share.
    """
    if a.portal == b.portal and a.listing_id == b.listing_id:
        return 1.0, ["same portal and listing_id"], 0.0

    score = 0.0
    max_score = 0.0
    reasons = []

    # Price similarity (±2%)
    if a.price is not None and b.price is not None and a.currency == b.currency:
        max_score += 1.0
        price_diff = abs(a.price - b.price) / max(a.price, b.price)
        if price_diff <= PRICE_TOLERANCE_PCT:
            score += 1.0
            reasons.append(f"price match ({a.price} vs {b.price}, diff {price_diff:.1%})")
        elif price_diff <= 0.10:
            score += 0.5
            reasons.append(f"price close ({a.price} vs {b.price}, diff {price_diff:.1%})")

    # Surface similarity (±5%)
    if a.surface is not None and b.surface is not None:
        max_score += 1.0
        surf_diff = abs(a.surface - b.surface) / max(a.surface, b.surface)
        if surf_diff <= SURFACE_TOLERANCE_PCT:
            score += 1.0
            reasons.append(f"surface match ({a.surface} vs {b.surface} m², diff {surf_diff:.1%})")
        elif surf_diff <= 0.15:
            score += 0.5
            reasons.append(f"surface close ({a.surface} vs {b.surface} m², diff {surf_diff:.1%})")

    # Location match (city/governorate/neighborhood), compared by place-words
    # rather than by string equality. The portals describe the same suburb at
    # different granularity - Property24 says "Kenilworth Upper" where Private
    # Property says "Kenilworth Upper, Southern Suburbs", and "Belhar" against
    # "Belhar, Cape Flats" - so `==` reports every real pair as a mismatch. Both
    # of those are genuine same-suburb, same-price listings that scored 0.5 and
    # were rejected. Generic words are dropped first, which is what stops
    # "Cape Town City Centre" from agreeing with "Cape Town City Bowl": once
    # the filler is gone the first has no identifying word left at all, and an
    # empty comparison is recorded as NOT COMPARABLE rather than as a match.
    location_matches = 0.0
    location_total = 0
    for field in ["city", "governorate", "neighborhood"]:
        agreement = _place_agreement(getattr(a, field), getattr(b, field))
        if agreement is not None:
            location_total += 1
            location_matches += agreement
    if location_total > 0:
        max_score += 1.0
        loc_score = location_matches / location_total
        score += loc_score
        # Compare the counts, not the quotient: "every field agreed" is what
        # is meant, and it is an integer fact. Asking a float whether it is
        # exactly 1.0 is the same question asked in a way that can be wrong.
        if location_matches == location_total:
            reasons.append("exact location match")
        elif loc_score > 0:
            reasons.append(f"partial location match ({location_matches}/{location_total})")

    # Rooms match
    if a.rooms is not None and b.rooms is not None:
        max_score += 0.5
        if a.rooms == b.rooms:
            score += 0.5
            reasons.append(f"rooms match ({a.rooms})")
        elif abs(a.rooms - b.rooms) == 1:
            score += 0.25
            reasons.append(f"rooms close ({a.rooms} vs {b.rooms})")

    # Bedrooms match
    if a.bedrooms is not None and b.bedrooms is not None:
        max_score += 0.5
        if a.bedrooms == b.bedrooms:
            score += 0.5
            reasons.append(f"bedrooms match ({a.bedrooms})")

    # Seller match
    if a.seller and b.seller:
        max_score += 0.5
        if a.seller.lower() == b.seller.lower():
            score += 0.5
            reasons.append(f"seller match ({a.seller})")

    # Gallery hash exact match (strong signal)
    if a.gallery_hash and b.gallery_hash and a.gallery_hash == b.gallery_hash:
        max_score += 1.0
        score += 1.0
        reasons.append("exact gallery hash match")

    # A shared photograph, counted ONE WAY ONLY.
    #
    # Agreement is strong evidence: measured against hand-checked pairs, every
    # cross-portal pair within the threshold was the same property, and neither
    # confirmed false positive came near it (distances 30 and 33).
    #
    # Disagreement is NOT evidence of difference, so it does not enter
    # max_score. Two agents photograph the same house differently, and
    # mediahash states the rule plainly: dHash survives no crop, watermark,
    # mirror or heavy grade, so "a miss is a real outcome and must read as
    # 'not proven identical' rather than 'proven different'". Scoring a miss
    # against the pair would have rejected a verified duplicate whose two
    # portals simply used different pictures.
    if a.phashes and b.phashes:
        best = min(_hamming(x, y) for x in a.phashes for y in b.phashes)
        if best <= PHASH_SAME_THRESHOLD:
            max_score += 1.0
            score += 1.0
            reasons.append(f"shares a photograph (hamming {best})")

    # Normalize
    if max_score == 0:
        return 0.0, ["no comparable fields"], 0.0
    reasons.append(f"evidence weight {max_score:.1f}")
    return min(score / max_score, 1.0), reasons, max_score


def find_candidates_by_phash(db: sqlite3.Connection, portal: str, listing_id: str, phashes: list[str]) -> list[tuple[str, str]]:
    """Find candidate listings from other portals that share similar phashes."""
    if not phashes:
        return []

    candidates = []
    for phash in phashes:
        matches = db.execute(
            """SELECT DISTINCT o.portal, o.listing_id, l.url
               FROM listing_media_band b
               JOIN listing_media m ON m.observation_id = b.observation_id AND m.position = b.position
               JOIN listing_observation o ON o.id = m.observation_id
               JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
               WHERE b.band IN ({}) AND o.portal <> ?
               ORDER BY o.portal, o.listing_id""".format(",".join("?" * 8)),
            list(phash_bands(phash)) + [portal],
        ).fetchall()
        candidates.extend([(r[0], r[1]) for r in matches])

    # Deduplicate
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # CONFIRM each band match with a real distance.
    #
    # A band index is a PREFILTER: two images share a band when one of eight
    # byte-slices of their hashes is equal, which happens constantly on
    # property photographs - white walls, grey skies, the same estate-agent
    # watermark. Measured over a real archive: 120 listings produced 2478 band
    # candidates of which exactly ONE was within the distance threshold. The
    # other 2477 were each loaded from the database and scored in full.
    #
    # store.go has always done this on the Go side (SameImage after the band
    # lookup); this is the same step, in the language that was missing it.
    return [c for c in unique if _shares_a_photograph(db, phashes, c)]


def _shares_a_photograph(db, phashes, candidate) -> bool:
    """Whether a band candidate actually holds a near-identical photograph."""
    other = load_listing_from_obs(db, candidate[0], candidate[1])
    if not other or not other.phashes:
        return False
    return any(
        _hamming(a, b) <= PHASH_SAME_THRESHOLD
        for a in phashes
        for b in other.phashes
    )


def _locality_tokens(payload: dict) -> set:
    """Identifying words from whatever the portal calls a place.

    Portals disagree on the field name - property24 says location, Private
    Property says locality/region/street, Tayara says city/governorate - so
    all of them are read and the generic words dropped.
    """
    parts = []
    for key in ("location", "locality", "region", "street", "city",
                "governorate", "neighborhood", "title"):
        v = payload.get(key)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    words = set()
    for chunk in parts:
        words |= _place_words(chunk)
    return words


def find_candidates_by_blocking(db, portal, listing_id, rec, limit=BLOCKING_CANDIDATE_LIMIT):
    """Find cross-portal candidates without needing an image fingerprint.

    WHY THIS EXISTS. find_candidates_by_phash was the only candidate source the
    harness had, and it returns nothing whenever the photographs were not
    fingerprinted - which was every listing ever stored, because nothing wrote
    listing_media.phash. Even with hashing in place it returns nothing whenever
    a portal re-encodes, crops or watermarks its images, and dHash survives
    none of those. A dedup layer that can only see byte-identical photographs
    is not a dedup layer.

    The block is a price window plus one shared identifying place-word. It is
    meant to be generous: everything it returns is then SCORED by
    structured_similarity, which is where precision is supposed to come from.
    """
    if rec.price is None or rec.price <= 0:
        return []
    lo = int(rec.price * (1 - PRICE_TOLERANCE_PCT))
    hi = int(rec.price * (1 + PRICE_TOLERANCE_PCT))
    rows = db.execute(
        """SELECT o.portal, o.listing_id, o.payload
           FROM listing_observation o
           WHERE o.portal <> ? AND o.currency = ? AND o.price BETWEEN ? AND ?
           GROUP BY o.portal, o.listing_id""",
        (portal, rec.currency, lo, hi),
    ).fetchall()

    want = _locality_tokens(rec.payload)
    out = []
    for cand_portal, cand_id, payload_json in rows:
        if len(out) >= limit:
            break
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except (TypeError, ValueError):
            payload = {}
        # No place-words on either side is not evidence of a match, and a price
        # window alone would block half the archive together.
        if not want or not (want & _locality_tokens(payload)):
            continue
        out.append((cand_portal, cand_id))
    return out


def phash_bands(phash: str) -> list[str]:
    """Generate 8 band keys from a 16-char hex phash."""
    bands = []
    for i in range(8):
        byte_val = phash[i*2:(i+1)*2]
        bands.append(f"{i}:{byte_val}")
    return bands


def listing_key(portal: str, listing_id: str) -> str:
    """The index key one listing's vector is stored under.

    The writer (sync_property_vectors) and the reader (semantic_similarity)
    are separate functions called on separate days, and this string is the only
    thing connecting them: if they ever disagree, the reader finds nothing,
    every pair becomes unknown, and the index stops being used while still
    holding every vector in it.
    """
    return f"{portal}/{listing_id}"


def listing_text(rec: ListingRecord) -> str:
    """The words a listing is embedded from.

    Module level rather than the closure it was, because a caller that wants
    the target's vector BEFORE looping over candidates could not get one.
    """
    parts = []
    title = rec.payload.get("title")
    if title:
        parts.append(str(title))
    desc = rec.payload.get("description")
    if desc:
        parts.append(str(desc)[:500])
    if rec.city:
        parts.append(rec.city)
    if rec.governorate:
        parts.append(rec.governorate)
    prop_type = rec.payload.get("property_type")
    if prop_type:
        parts.append(str(prop_type))
    return " ".join(filter(None, parts))


def semantic_similarity(
    vec_store: VectorStore,
    a: ListingRecord,
    b: ListingRecord,
    a_vector=None,
) -> tuple[Optional[float], Optional[str]]:
    """How similar two listings read, or why that cannot be said.

    Returns (score, None) with a real cosine, or (None, why) when the semantic
    layer has no answer. Those are different facts and the caller has to be
    able to tell them apart, because it applies a different rule to each: a
    measured score goes into the combined formula, an unknown one goes to the
    stricter structured-only bar and is named in the reasons.

    Two things used to make this function report zero where it knew nothing.
    It asked `search` for the five nearest points and looked for listing b
    among them, so b was invisible when it was simply not in the index AND when
    it was the sixth-nearest in an index of thousands - and nothing ever wrote
    a property vector, so the first case was the only case there ever was. The
    score now comes from the two vectors themselves, which is the comparison
    the layer always claimed to make, and an absent vector is reported as
    absent.
    """
    text = listing_text(a)
    if not text:
        return None, f"{listing_key(a.portal, a.listing_id)} has no text to embed"
    try:
        if a_vector is None:
            a_vector = vec_store.embed_text(text)
        b_vector = vec_store.vector_for(listing_key(b.portal, b.listing_id))
        if b_vector is None:
            return None, (f"{listing_key(b.portal, b.listing_id)} has no vector in the "
                          "index; run `jester property-dedup --sync-vectors`")
        return cosine(a_vector, b_vector), None
    except Exception as exc:  # noqa: BLE001 - a store that cannot answer says why
        return None, str(exc)


def check_duplicate(
    db: sqlite3.Connection,
    vec_store: VectorStore,
    portal: str,
    listing_id: str,
) -> DedupResult:
    """Check if a listing is a duplicate of any listing on another portal."""
    target = load_listing_from_obs(db, portal, listing_id)
    if not target:
        return DedupResult(
            is_duplicate=False,
            structured_score=0.0,
            semantic_score=0.0,
            combined_score=0.0,
            matched_portal=None,
            matched_listing_id=None,
            reasons=["target listing not found"],
        )

    # phash first, then blocking. The fallback is not a second-best: dHash
    # misses any image a portal re-encoded, cropped or watermarked, so the two
    # layers answer different questions and both run.
    candidates = find_candidates_by_phash(db, portal, listing_id, target.phashes)
    seen_cands = set(candidates)
    for cand in find_candidates_by_blocking(db, portal, listing_id, target):
        if cand not in seen_cands:
            seen_cands.add(cand)
            candidates.append(cand)

    best_result = DedupResult(
        is_duplicate=False,
        structured_score=0.0,
        semantic_score=0.0,
        combined_score=0.0,
        matched_portal=None,
        matched_listing_id=None,
        reasons=["no candidates found"],
    )

    # The target's vector, made once for the whole check. Every candidate is
    # compared against this same listing, so embedding it per candidate sent
    # the identical text to the backend once per candidate.
    target_text = listing_text(target)
    try:
        target_vector = vec_store.embed_text(target_text) if target_text else None
    except Exception:  # noqa: BLE001 - semantic_similarity reports the failure per pair
        target_vector = None

    for cand_portal, cand_listing_id in candidates:
        candidate = load_listing_from_obs(db, cand_portal, cand_listing_id)
        if not candidate:
            continue

        # Structured similarity
        struct_score, struct_reasons, struct_evidence = structured_similarity_detailed(target, candidate)

        # Semantic similarity
        sem_score, sem_why = semantic_similarity(vec_store, target, candidate,
                                                 a_vector=target_vector)
        sem_known = sem_score is not None

        # Combined score. An unknown semantic half contributes nothing rather
        # than a zero, and the rule applied below is not the combined one.
        combined = STRUCTURED_WEIGHT * struct_score + (
            SEMANTIC_WEIGHT * sem_score if sem_known else 0.0)

        reasons = struct_reasons[:]
        if not sem_known:
            # Semantics are UNKNOWN here, not zero, and the combined rule
            # cannot be applied to a score that was never measured. The
            # structured-only bar is deliberately higher than the combined one
            # because it stands on less evidence.
            is_dup = (
                struct_score >= STRUCTURED_ONLY_THRESHOLD
                and struct_evidence >= STRUCTURED_ONLY_MIN_EVIDENCE
            )
            reasons.append(
                f"semantic unknown ({sem_why}); structured-only rule, score "
                f"{struct_score:.3f} vs {STRUCTURED_ONLY_THRESHOLD}, evidence "
                f"{struct_evidence:.1f} vs {STRUCTURED_ONLY_MIN_EVIDENCE}"
            )
        else:
            is_dup = (
                combined >= COMBINED_THRESHOLD
                and struct_score > 0
                and sem_score >= SEMANTIC_THRESHOLD
            )
            # Always said when the layer answered, including when it answered
            # zero. The `> 0` that used to gate this line existed to hide the
            # phantom zero of a store that had never been asked; a measured
            # disagreement is a finding and printing it is the point.
            reasons.append(f"semantic score: {sem_score:.3f}")
            reasons.append(f"combined: {combined:.3f} (threshold {COMBINED_THRESHOLD})")

        if is_dup and combined > best_result.combined_score:
            best_result = DedupResult(
                is_duplicate=True,
                structured_score=struct_score,
                semantic_score=sem_score,
                combined_score=combined,
                matched_portal=cand_portal,
                matched_listing_id=cand_listing_id,
                reasons=reasons,
            )
        elif combined > best_result.combined_score:
            best_result = DedupResult(
                is_duplicate=False,
                structured_score=struct_score,
                semantic_score=sem_score,
                combined_score=combined,
                matched_portal=cand_portal,
                matched_listing_id=cand_listing_id,
                reasons=reasons,
            )

    return best_result


#: Listings per batch write. Same size `reembed` uses: one embed call and one
#: round trip per chunk, small enough that a chunk which has to be retried row
#: by row loses little.
SYNC_CHUNK = 64

#: How many failure messages a sync carries back. The counts are the fact; the
#: messages are for whoever has to explain one of them.
SYNC_ERROR_SAMPLES = 5


def _listing_rows(db, portal=None, listing_id=None):
    """Every listing the archive holds, or the one named.

    DISTINCT rather than every observation: a listing is re-observed on each
    pass, so the raw table would write thirty points for a listing seen thirty
    times and the reader would answer with the same property over and over.
    """
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_observation'"
    ).fetchone()
    if not has_table:
        # The listing tables belong to the Go collector. A database that never
        # ran it has nothing to sync, which is a reportable count and not a
        # crash in the middle of a treat.
        return []
    sql = "SELECT DISTINCT portal, listing_id FROM listing_observation"
    where, args = [], []
    if portal:
        where.append("portal = ?")
        args.append(portal)
    if listing_id:
        where.append("listing_id = ?")
        args.append(listing_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.execute(sql + " ORDER BY portal, listing_id", args).fetchall()


def _write_chunk(vec_store, items, out) -> None:
    """One batch write, with the per-item fallback `reembed` established.

    `upsert_many` raises for the whole chunk on ANY failure, which would lose
    every listing in it to one bad row; the retry sends them singly so the
    failure that survives is the one that is real.
    """
    try:
        vec_store.upsert_many(items)
        out["synced"] += len(items)
        return
    except Exception:  # noqa: BLE001 - the chunk is retried row by row below
        pass
    for key, text, payload in items:
        try:
            vec_store.upsert(key, text, payload)
            out["synced"] += 1
        except Exception as exc:  # noqa: BLE001 - counted; the rest still syncs
            out["failed"] += 1
            if len(out["errors"]) < SYNC_ERROR_SAMPLES:
                out["errors"].append(f"{key}: {exc}")


def sync_property_vectors(db, vec_store, portal=None, listing_id=None) -> dict:
    """Write one vector per listing into the property index (DD-M4).

    `semantic_similarity` has always read this collection and nothing in the
    codebase ever wrote it. The archivist embeds nuggets, the clustering pass
    embeds chunks, and `properties__jester` stayed empty in every deployment,
    which the reader reported back as 0.0 - "these two listings are not
    similar" - for every pair ever checked.

    Returns counts rather than raising, because a Qdrant that will not take a
    write is a fact the caller reports and works around, not a traceback in the
    middle of a run. A listing with nothing to embed is skipped: an empty text
    carries no meaning to compare, and a point made of it sits near every other
    point made of nothing.
    """
    out = {"scanned": 0, "synced": 0, "skipped": 0, "failed": 0, "errors": []}
    items = []
    for row in _listing_rows(db, portal, listing_id):
        rec = load_listing_from_obs(db, row[0], row[1])
        if rec is None:
            continue
        out["scanned"] += 1
        text = listing_text(rec)
        if not text:
            out["skipped"] += 1
            continue
        items.append((listing_key(rec.portal, rec.listing_id), text, {
            "portal": rec.portal, "listing_id": rec.listing_id,
            "price": rec.price, "currency": rec.currency, "city": rec.city}))
        if len(items) >= SYNC_CHUNK:
            _write_chunk(vec_store, items, out)
            items = []
    if items:
        _write_chunk(vec_store, items, out)
    return out


def validate_dedup_on_overlap(db: sqlite3.Connection, vec_store: VectorStore) -> dict:
    """Run dedup validation on known overlapping listings across portals.

    Returns precision/recall metrics and examples.
    """
    # Get all listings that have observations
    listings = db.execute(
        """SELECT DISTINCT portal, listing_id FROM listing_observation ORDER BY portal, listing_id"""
    ).fetchall()

    results = {
        "total_checked": 0,
        "duplicates_found": 0,
        "false_positives": 0,
        "examples": [],
    }

    for portal, listing_id in listings:
        result = check_duplicate(db, vec_store, portal, listing_id)
        results["total_checked"] += 1

        if result.is_duplicate:
            results["duplicates_found"] += 1
            results["examples"].append({
                "source": f"{portal}/{listing_id}",
                "match": f"{result.matched_portal}/{result.matched_listing_id}",
                "structured_score": round(result.structured_score, 3),
                "semantic_score": (round(result.semantic_score, 3)
                                   if result.semantic_score is not None else None),
                "combined_score": round(result.combined_score, 3),
                "reasons": result.reasons,
            })

    return results


if __name__ == "__main__":
    import sys
    import os

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/jester.db"
    qdrant_url = os.environ.get("JESTER_QDRANT_URL")

    db = open_db(db_path)

    # Initialize vector store
    from jester.embed import select_embedding
    from jester.config import load_config

    cfg = load_config("config")
    embed = select_embedding(cfg.thresholds)

    if qdrant_url:
        vec_store = VectorStore.for_url(
            qdrant_url, embed, collection="properties__jester"
        )
    else:
        # The same collection the command reads. This path used to open the
        # default `nuggets` store, so the validation harness compared property
        # texts against a nugget space and called the result a semantic score.
        vec_store = VectorStore.open(
            os.path.join(os.path.dirname(db_path), "jester.properties.qdrant"),
            embed,
            collection="properties__jester",
        )

    print("Running dedup validation...")
    results = validate_dedup_on_overlap(db, vec_store)
    print(json.dumps(results, indent=2))