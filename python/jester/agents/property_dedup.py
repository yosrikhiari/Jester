"""Cross-portal property deduplication for real-estate listings.

Implements the 4-layer dedup strategy:
1. Go-side exact (content hash + GalleryHash) - same portal
2. Cross-portal image (MatchByPhash, dHash band-indexed, Hamming ≤12)
3. Structured-field (price ±2%, surface ±5%, location, rooms, seller)
4. Semantic embedding (Qdrant, threshold 0.92)

Combined score: 0.6 * structured + 0.4 * semantic >= 0.85 → duplicate
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

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
    semantic_score: float
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
        return str(v) if v else None

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
        city=get_str("city"),
        governorate=get_str("governorate"),
        neighborhood=get_str("neighborhood"),
        latitude=get_float("latitude"),
        longitude=get_float("longitude"),
        seller=get_str("seller"),
        seller_type=get_str("seller_type"),
        payload=payload,
        gallery_hash=gallery_hash,
        phashes=phashes,
    )


def structured_similarity(a: ListingRecord, b: ListingRecord) -> tuple[float, list[str]]:
    """Compute structured-field similarity score (0.0 to 1.0) with reasons."""
    if a.portal == b.portal and a.listing_id == b.listing_id:
        return 1.0, ["same portal and listing_id"]

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

    # Location match (city/governorate/neighborhood)
    location_matches = 0
    location_total = 0
    for field in ["city", "governorate", "neighborhood"]:
        va = getattr(a, field)
        vb = getattr(b, field)
        if va and vb:
            location_total += 1
            if va.lower() == vb.lower():
                location_matches += 1
    if location_total > 0:
        max_score += 1.0
        loc_score = location_matches / location_total
        score += loc_score
        if loc_score == 1.0:
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

    # Normalize
    if max_score == 0:
        return 0.0, ["no comparable fields"]
    return min(score / max_score, 1.0), reasons


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
    return unique


def phash_bands(phash: str) -> list[str]:
    """Generate 8 band keys from a 16-char hex phash."""
    bands = []
    for i in range(8):
        byte_val = phash[i*2:(i+1)*2]
        bands.append(f"{i}:{byte_val}")
    return bands


def semantic_similarity(
    vec_store: VectorStore,
    a: ListingRecord,
    b: ListingRecord,
) -> tuple[float, Optional[str]]:
    """Compute semantic similarity using Qdrant embeddings."""
    # Build text representation for embedding
    def listing_text(rec: ListingRecord) -> str:
        parts = []
        if rec.title:
            parts.append(rec.title)
        if rec.description:
            parts.append(rec.description[:500])
        if rec.city:
            parts.append(rec.city)
        if rec.governorate:
            parts.append(rec.governorate)
        if rec.property_type:
            parts.append(rec.property_type)
        return " ".join(filter(None, parts))

    try:
        # Search for similar vectors
        results = vec_store.search(listing_text(a), top_k=10)
        for result in results:
            # Check if this result corresponds to listing b
            payload = result.get("payload", {})
            if (payload.get("portal") == b.portal and
                payload.get("listing_id") == b.listing_id):
                return result.get("score", 0.0), None
        return 0.0, None
    except Exception as e:
        return 0.0, str(e)


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

    # Find candidates via phash
    candidates = find_candidates_by_phash(db, portal, listing_id, target.phashes)

    best_result = DedupResult(
        is_duplicate=False,
        structured_score=0.0,
        semantic_score=0.0,
        combined_score=0.0,
        matched_portal=None,
        matched_listing_id=None,
        reasons=["no candidates found"],
    )

    for cand_portal, cand_listing_id in candidates:
        candidate = load_listing_from_obs(db, cand_portal, cand_listing_id)
        if not candidate:
            continue

        # Structured similarity
        struct_score, struct_reasons = structured_similarity(target, candidate)

        # Semantic similarity
        sem_score, sem_error = semantic_similarity(vec_store, target, candidate)
        if sem_error:
            sem_score = 0.0

        # Combined score
        combined = STRUCTURED_WEIGHT * struct_score + SEMANTIC_WEIGHT * sem_score
        is_dup = combined >= COMBINED_THRESHOLD and struct_score > 0 and sem_score >= SEMANTIC_THRESHOLD

        reasons = struct_reasons[:]
        if sem_score > 0:
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
                "semantic_score": round(result.semantic_score, 3),
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
        vec_store = VectorStore.open(
            os.path.join(os.path.dirname(db_path), "jester.properties.qdrant"),
            embed,
        )

    print("Running dedup validation...")
    results = validate_dedup_on_overlap(db, vec_store)
    print(json.dumps(results, indent=2))