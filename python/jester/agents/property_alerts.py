"""Property alerts agent for real-estate listings.

Generates alerts for:
- New listings matching user criteria
- Price reductions on tracked listings
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from jester.store import open_db


@dataclass
class AlertCriteria:
    """User-defined alert criteria."""
    area: Optional[str] = None  # city or governorate (legacy)
    city: Optional[str] = None
    governorate: Optional[str] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    rooms_min: Optional[int] = None
    rooms_max: Optional[int] = None
    surface_min: Optional[float] = None
    surface_max: Optional[float] = None
    property_type: Optional[str] = None
    transaction_type: Optional[str] = None


@dataclass
class PropertyAlert:
    """A single alert notification."""
    alert_type: str  # "new_listing" or "price_reduction"
    portal: str
    listing_id: str
    url: str
    title: str
    price: int
    currency: str
    surface: Optional[float]
    rooms: Optional[int]
    city: Optional[str]
    governorate: Optional[str]
    price_change: Optional[int] = None  # for price_reduction
    previous_price: Optional[int] = None
    matched_criteria: list[str] = None


def matches_criteria(listing: dict, criteria: AlertCriteria) -> list[str]:
    """Check if a listing matches the alert criteria. Returns list of matched criteria."""
    matched = []

    if criteria.area and listing.get("city"):
        if criteria.area.lower() in listing["city"].lower():
            matched.append(f"area:{criteria.area}")
    if criteria.city and listing.get("city"):
        if criteria.city.lower() in listing["city"].lower():
            matched.append(f"city:{criteria.city}")
    if criteria.governorate and listing.get("governorate"):
        if criteria.governorate.lower() in listing["governorate"].lower():
            matched.append(f"governorate:{criteria.governorate}")
    if criteria.price_min and listing.get("price"):
        if listing["price"] >= criteria.price_min:
            matched.append(f"price>={criteria.price_min}")
    if criteria.price_max and listing.get("price"):
        if listing["price"] <= criteria.price_max:
            matched.append(f"price<={criteria.price_max}")
    if criteria.rooms_min and listing.get("rooms"):
        if listing["rooms"] >= criteria.rooms_min:
            matched.append(f"rooms>={criteria.rooms_min}")
    if criteria.rooms_max and listing.get("rooms"):
        if listing["rooms"] <= criteria.rooms_max:
            matched.append(f"rooms<={criteria.rooms_max}")
    if criteria.surface_min and listing.get("surface"):
        if listing["surface"] >= criteria.surface_min:
            matched.append(f"surface>={criteria.surface_min}")
    if criteria.surface_max and listing.get("surface"):
        if listing["surface"] <= criteria.surface_max:
            matched.append(f"surface<={criteria.surface_max}")
    if criteria.property_type and listing.get("property_type"):
        if criteria.property_type.lower() in listing["property_type"].lower():
            matched.append(f"type:{criteria.property_type}")
    if criteria.transaction_type and listing.get("transaction_type"):
        if criteria.transaction_type.lower() == listing["transaction_type"].lower():
            matched.append(f"transaction:{criteria.transaction_type}")

    return matched


def get_new_listings_since(db: sqlite3.Connection, since: datetime) -> list[dict]:
    """Get listings first observed since the given time."""
    since_str = since.isoformat()
    rows = db.execute(
        """SELECT o.portal, o.listing_id, l.url, o.price, o.currency, o.payload,
                  l.first_seen_at
           FROM listing_observation o
           JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
           WHERE o.observed_at >= ? AND o.id = (
               SELECT MIN(id) FROM listing_observation
               WHERE portal = o.portal AND listing_id = o.listing_id
           )
           ORDER BY o.observed_at DESC""",
        (since_str,),
    ).fetchall()

    listings = []
    for row in rows:
        portal, listing_id, url, price, currency, payload_json, first_seen = row
        payload = json.loads(payload_json) if payload_json else {}
        payload.update({
            "portal": portal,
            "listing_id": listing_id,
            "url": url,
            "price": price,
            "currency": currency,
            "first_seen_at": first_seen,
        })
        listings.append(payload)
    return listings


def get_price_reductions(db: sqlite3.Connection, since: datetime) -> list[dict]:
    """Get listings that had a price reduction since the given time."""
    since_str = since.isoformat()
    rows = db.execute(
        """SELECT portal, listing_id, url, price, currency, payload,
                  observed_at, prev_price
           FROM (
               SELECT o.portal, o.listing_id, l.url, o.price, o.currency, o.payload,
                      o.observed_at,
                      LAG(o.price) OVER (PARTITION BY o.portal, o.listing_id ORDER BY o.observed_at) as prev_price
               FROM listing_observation o
               JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
               WHERE o.price IS NOT NULL
               ORDER BY o.portal, o.listing_id, o.observed_at
           )
           WHERE observed_at >= ?""",
        (since_str,),
    ).fetchall()

    reductions = []
    for row in rows:
        portal, listing_id, url, price, currency, payload_json, observed_at, prev_price = row
        if prev_price is not None and price is not None and price < prev_price:
            payload = json.loads(payload_json) if payload_json else {}
            payload.update({
                "portal": portal,
                "listing_id": listing_id,
                "url": url,
                "price": price,
                "currency": currency,
                "previous_price": prev_price,
                "price_change": price - prev_price,  # negative
                "observed_at": observed_at,
            })
            reductions.append(payload)
    return reductions


def evaluate_alerts(db: sqlite3.Connection, criteria: AlertCriteria, lookback_hours: int = 24) -> list[PropertyAlert]:
    """Evaluate alert criteria against recent observations."""
    since = datetime.utcnow() - timedelta(hours=lookback_hours)
    alerts = []

    # Check new listings
    new_listings = get_new_listings_since(db, since)
    for listing in new_listings:
        matched = matches_criteria(listing, criteria)
        if matched:
            alerts.append(PropertyAlert(
                alert_type="new_listing",
                portal=listing.get("portal", ""),
                listing_id=listing.get("listing_id", ""),
                url=listing.get("url", ""),
                title=listing.get("title", ""),
                price=listing.get("price", 0),
                currency=listing.get("currency", ""),
                surface=listing.get("surface"),
                rooms=listing.get("rooms"),
                city=listing.get("city"),
                governorate=listing.get("governorate"),
                matched_criteria=matched,
            ))

    # Check price reductions
    reductions = get_price_reductions(db, since)
    for listing in reductions:
        matched = matches_criteria(listing, criteria)
        if matched:
            alerts.append(PropertyAlert(
                alert_type="price_reduction",
                portal=listing.get("portal", ""),
                listing_id=listing.get("listing_id", ""),
                url=listing.get("url", ""),
                title=listing.get("title", ""),
                price=listing.get("price", 0),
                currency=listing.get("currency", ""),
                surface=listing.get("surface"),
                rooms=listing.get("rooms"),
                city=listing.get("city"),
                governorate=listing.get("governorate"),
                price_change=listing.get("price_change"),
                previous_price=listing.get("previous_price"),
                matched_criteria=matched,
            ))

    return alerts


def format_alert_digest(alerts: list[PropertyAlert]) -> str:
    """Format alerts as a human-readable digest."""
    if not alerts:
        return "No alerts in the last period."

    lines = [f"Property Alerts Digest ({len(alerts)} alerts)", "=" * 50, ""]

    # Group by alert type
    new_listings = [a for a in alerts if a.alert_type == "new_listing"]
    price_reductions = [a for a in alerts if a.alert_type == "price_reduction"]

    if new_listings:
        lines.append(f"📋 NEW LISTINGS ({len(new_listings)})")
        lines.append("-" * 30)
        for a in new_listings:
            lines.append(f"  {a.title}")
            lines.append(f"    {a.price:,} {a.currency} | {a.surface}m² | {a.rooms} rooms | {a.city}, {a.governorate}")
            lines.append(f"    {a.url}")
            lines.append(f"    Matched: {', '.join(a.matched_criteria)}")
            lines.append("")

    if price_reductions:
        lines.append(f"📉 PRICE REDUCTIONS ({len(price_reductions)})")
        lines.append("-" * 30)
        for a in price_reductions:
            lines.append(f"  {a.title}")
            lines.append(f"    {a.previous_price:,} → {a.price:,} {a.currency} ({a.price_change:+,} change)")
            lines.append(f"    {a.url}")
            lines.append(f"    Matched: {', '.join(a.matched_criteria)}")
            lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/jester.db"
    db = open_db(db_path)

    # Example criteria
    criteria = AlertCriteria(
        governorate="Tunis",
        price_max=500000,
        rooms_min=2,
        surface_min=100,
    )

    alerts = evaluate_alerts(db, criteria, lookback_hours=24)
    print(format_alert_digest(alerts))