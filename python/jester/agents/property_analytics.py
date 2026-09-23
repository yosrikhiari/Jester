"""Property analytics agent for real-estate listings.

Computes:
- Price per m² vs neighborhood/city averages
- Days-on-market
- Agency vs owner classification
- Duplicate/suspicious detection
- Neighborhood trends / historical changes
- Geographic price heatmaps
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from jester.store import open_db


@dataclass
class PriceStats:
    """Price statistics for a geographic area."""
    area: str
    area_type: str  # "city", "governorate", "neighborhood"
    listing_count: int
    avg_price_per_m2: float
    median_price_per_m2: float
    min_price_per_m2: float
    max_price_per_m2: float
    currency: str


@dataclass
class ListingAnalytics:
    """Analytics for a single listing."""
    portal: str
    listing_id: str
    url: str
    price_per_m2: Optional[float]
    days_on_market: Optional[int]
    is_duplicate: bool
    is_suspicious: bool
    seller_type: str
    price_vs_avg_pct: Optional[float]  # percentage vs local average


@dataclass
class MarketTrend:
    """Market trend for an area over time."""
    area: str
    area_type: str
    period_start: str
    period_end: str
    avg_price_per_m2: float
    listing_count: int
    price_change_pct: Optional[float]


def compute_price_per_m2(price: Optional[int], surface) -> Optional[float]:
    """Compute price per m². Handles string surface values."""
    try:
        if price is None or surface is None:
            return None
        # float() reads a numeric string and a number the same way, so the
        # isinstance check that used to guard this did nothing: both arms of
        # the conditional were the identical call.
        s = float(surface)
        p = float(price)
        if s > 0:
            return p / s
    except Exception:
        return None
    return None


def get_all_listings_with_obs(db: sqlite3.Connection) -> list[dict]:
    """Get all listings with their latest observation and full history."""
    rows = db.execute(
        """SELECT o.portal, o.listing_id, l.url, o.price, o.currency, o.payload,
                  o.observed_at, o.gallery_hash,
                  l.first_seen_at, l.last_seen_at
           FROM listing_observation o
           JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
           ORDER BY o.portal, o.listing_id, o.observed_at"""
    ).fetchall()

    # Group by portal+listing_id
    listings = defaultdict(list)
    for row in rows:
        portal, listing_id, url, price, currency, payload_json, observed_at, gallery_hash, first_seen, last_seen = row
        payload = json.loads(payload_json) if payload_json else {}
        payload.update({
            "portal": portal,
            "listing_id": listing_id,
            "url": url,
            "price": price,
            "currency": currency,
            "observed_at": observed_at,
            "gallery_hash": gallery_hash,
            "first_seen_at": first_seen,
            "last_seen_at": last_seen,
        })
        listings[(portal, listing_id)].append(payload)

    # Convert to list with computed fields
    result = []
    for (portal, listing_id), obs_list in listings.items():
        latest = obs_list[-1]
        first = obs_list[0]

        # Compute days on market
        try:
            first_dt = datetime.fromisoformat(first["first_seen_at"].replace("Z", "+00:00"))
            last_dt = datetime.fromisoformat(latest["last_seen_at"].replace("Z", "+00:00"))
            days_on_market = (last_dt - first_dt).days
        except Exception:
            days_on_market = None

        # Price per m²
        price = latest.get("price")
        surface = latest.get("surface")
        price_per_m2 = compute_price_per_m2(price, surface)

        result.append({
            "portal": portal,
            "listing_id": listing_id,
            "url": latest.get("url"),
            "title": latest.get("title"),
            "price": price,
            "currency": latest.get("currency"),
            "surface": surface,
            "rooms": latest.get("rooms"),
            "bedrooms": latest.get("bedrooms"),
            "bathrooms": latest.get("bathrooms"),
            "city": latest.get("city"),
            "governorate": latest.get("governorate"),
            "neighborhood": latest.get("neighborhood"),
            "seller": latest.get("seller"),
            "seller_type": latest.get("seller_type"),
            "price_per_m2": price_per_m2,
            "days_on_market": days_on_market,
            "observation_count": len(obs_list),
            "price_history": [(o["observed_at"], o["price"]) for o in obs_list if o.get("price")],
            "first_seen_at": first.get("first_seen_at"),
            "last_seen_at": latest.get("last_seen_at"),
            "observed_at": latest.get("observed_at"),
        })

    return result


def compute_area_price_stats(listings: list[dict], area_field: str) -> list[PriceStats]:
    """Compute price per m² statistics grouped by geographic area."""
    area_data = defaultdict(list)

    for l in listings:
        area_val = l.get(area_field)
        ppm2 = l.get("price_per_m2")
        currency = l.get("currency")
        if area_val and ppm2 is not None and currency:
            area_data[(area_val, currency)].append(ppm2)

    stats = []
    for (area, currency), prices in area_data.items():
        if len(prices) < 3:
            continue  # Need minimum samples
        sorted_prices = sorted(prices)
        n = len(sorted_prices)
        stats.append(PriceStats(
            area=area,
            area_type=area_field,
            listing_count=n,
            avg_price_per_m2=sum(prices) / n,
            median_price_per_m2=sorted_prices[n // 2],
            min_price_per_m2=sorted_prices[0],
            max_price_per_m2=sorted_prices[-1],
            currency=currency,
        ))

    return sorted(stats, key=lambda s: s.avg_price_per_m2, reverse=True)


def analyze_listing_vs_market(listings: list[dict], city_stats: list[PriceStats]) -> list[ListingAnalytics]:
    """Analyze each listing against its local market average."""
    # Build lookup: (city, currency) -> avg_price_per_m2
    city_avg = {}
    for s in city_stats:
        city_avg[(s.area, s.currency)] = s.avg_price_per_m2

    results = []
    for l in listings:
        city = l.get("city")
        currency = l.get("currency")
        ppm2 = l.get("price_per_m2")
        seller_type = l.get("seller_type", "unknown")

        avg = city_avg.get((city, currency)) if city and currency else None
        vs_avg = None
        if avg and ppm2 and avg > 0:
            vs_avg = ((ppm2 - avg) / avg) * 100

        # Detect duplicates (simplified - same gallery_hash on different portals)
        is_dup = False
        # This would need cross-portal checking

        # Detect suspicious (price significantly below market)
        is_suspicious = False
        if vs_avg is not None and vs_avg < -30:  # 30% below average
            is_suspicious = True

        results.append(ListingAnalytics(
            portal=l["portal"],
            listing_id=l["listing_id"],
            url=l["url"],
            price_per_m2=ppm2,
            days_on_market=l.get("days_on_market"),
            is_duplicate=is_dup,
            is_suspicious=is_suspicious,
            seller_type=seller_type,
            price_vs_avg_pct=vs_avg,
        ))

    return results


def compute_market_trends(listings: list[dict], area_field: str, period_days: int = 30) -> list[MarketTrend]:
    """Compute price trends over time for each area."""
    cutoff = datetime.utcnow() - timedelta(days=period_days)
    cutoff_str = cutoff.isoformat()

    # Group observations by area and time period
    area_periods = defaultdict(list)

    for l in listings:
        area_val = l.get(area_field)
        currency = l.get("currency")
        price = l.get("price")
        surface = l.get("surface")
        observed_at = l.get("observed_at")

        if not area_val or not currency or price is None or surface is None:
            continue

        try:
            obs_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if obs_dt < cutoff:
                continue
        except Exception:
            continue

        try:
            s = float(surface)   # see compute_price_per_m2: str and number
            p = float(price)     # take the same path through float()
            ppm2 = p / s if s > 0 else None
            if ppm2 is None:
                continue
        except Exception:
            continue
        area_periods[(area_val, currency)].append((obs_dt, ppm2))

    trends = []
    for (area, currency), observations in area_periods.items():
        if len(observations) < 5:
            continue

        observations.sort(key=lambda x: x[0])
        prices = [p for _, p in observations]

        # Split into two halves for trend
        mid = len(prices) // 2
        first_half = prices[:mid]
        second_half = prices[mid:]

        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        change_pct = ((avg_second - avg_first) / avg_first) * 100 if avg_first > 0 else None

        trends.append(MarketTrend(
            area=area,
            area_type=area_field,
            period_start=observations[0][0].isoformat(),
            period_end=observations[-1][0].isoformat(),
            avg_price_per_m2=avg_second,
            listing_count=len(observations),
            price_change_pct=change_pct,
        ))

    return sorted(trends, key=lambda t: t.price_change_pct or 0, reverse=True)


def detect_duplicates_and_suspicious(listings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Detect duplicates across portals and suspicious patterns."""
    duplicates = []
    suspicious = []

    # Group by gallery_hash for exact image duplicates
    gallery_groups = defaultdict(list)
    for l in listings:
        if l.get("gallery_hash"):
            gallery_groups[l["gallery_hash"]].append(l)

    for hash_val, group in gallery_groups.items():
        portals = set(l["portal"] for l in group)
        if len(portals) > 1:
            duplicates.append({
                "gallery_hash": hash_val,
                "portals": list(portals),
                "listings": [(l["portal"], l["listing_id"], l["url"], l["price"], l["currency"]) for l in group],
            })

    # Suspicious: same phone number across many listings
    phone_groups = defaultdict(list)
    for l in listings:
        phone = l.get("phone")
        if phone:
            phone_groups[phone].append(l)

    for phone, group in phone_groups.items():
        if len(group) > 5:  # More than 5 listings with same phone
            portals = set(l["portal"] for l in group)
            suspicious.append({
                "type": "phone_reuse",
                "phone": phone,
                "count": len(group),
                "portals": list(portals),
                "listings": [(l["portal"], l["listing_id"], l["title"], l["price"], l["currency"]) for l in group],
            })

    # Suspicious: price significantly below market (will be checked after compute_area_price_stats)
    return duplicates, suspicious


def generate_price_heatmap_data(listings: list[dict]) -> list[dict]:
    """Generate data for geographic price heatmap."""
    heatmap_data = []
    for l in listings:
        lat = l.get("latitude")
        lng = l.get("longitude")
        ppm2 = l.get("price_per_m2")
        if lat is not None and lng is not None and ppm2 is not None:
            heatmap_data.append({
                "lat": lat,
                "lng": lng,
                "price_per_m2": ppm2,
                "price": l.get("price"),
                "currency": l.get("currency"),
                "surface": l.get("surface"),
                "city": l.get("city"),
                "url": l.get("url"),
            })
    return heatmap_data


def run_full_analytics(db_path: str = "data/jester.db") -> dict:
    """Run complete analytics pipeline."""
    db = open_db(db_path)

    listings = get_all_listings_with_obs(db)

    # Compute area stats
    city_stats = compute_area_price_stats(listings, "city")
    governorate_stats = compute_area_price_stats(listings, "governorate")
    neighborhood_stats = compute_area_price_stats(listings, "neighborhood")

    # Analyze listings vs market
    listing_analytics = analyze_listing_vs_market(listings, city_stats)

    # Market trends
    city_trends = compute_market_trends(listings, "city", period_days=90)
    governorate_trends = compute_market_trends(listings, "governorate", period_days=90)

    # Detect duplicates and suspicious
    duplicates, suspicious = detect_duplicates_and_suspicious(listings)

    # Price heatmap
    heatmap = generate_price_heatmap_data(listings)

    return {
        "summary": {
            "total_listings": len(listings),
            "portals": list(set(l["portal"] for l in listings)),
            "date_range": {
                "earliest": min(l["first_seen_at"] for l in listings),
                "latest": max(l["last_seen_at"] for l in listings),
            },
        },
        "price_stats": {
            "by_city": [vars(s) for s in city_stats],
            "by_governorate": [vars(s) for s in governorate_stats],
            "by_neighborhood": [vars(s) for s in neighborhood_stats],
        },
        "listing_analytics": [vars(a) for a in listing_analytics],
        "market_trends": {
            "by_city": [vars(t) for t in city_trends],
            "by_governorate": [vars(t) for t in governorate_trends],
        },
        "duplicates": duplicates,
        "suspicious": suspicious,
        "heatmap": heatmap,
    }


if __name__ == "__main__":
    import sys

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/jester.db"
    results = run_full_analytics(db_path)
    print(json.dumps(results, indent=2, default=str))