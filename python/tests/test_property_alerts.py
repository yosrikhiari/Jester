"""Property alerts: criteria matching, new-listing detection, price-reduction detection."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from jester.agents.property_alerts import (
    AlertCriteria,
    PropertyAlert,
    evaluate_alerts,
    format_alert_digest,
    get_new_listings_since,
    get_price_reductions,
    matches_criteria,
)


# ---------------------------------------------------------------------------
# matches_criteria
# ---------------------------------------------------------------------------

class TestMatchesCriteria:
    """Pure-function tests: no database needed."""

    def test_city_match(self):
        listing = {"city": "La Soukra", "price": 300000}
        criteria = AlertCriteria(city="Soukra")
        matched = matches_criteria(listing, criteria)
        assert "city:Soukra" in matched

    def test_city_mismatch(self):
        listing = {"city": "Ariana", "price": 300000}
        criteria = AlertCriteria(city="Soukra")
        assert matches_criteria(listing, criteria) == []

    def test_governorate_match(self):
        listing = {"governorate": "Tunis", "price": 200000}
        criteria = AlertCriteria(governorate="Tunis")
        matched = matches_criteria(listing, criteria)
        assert "governorate:Tunis" in matched

    def test_price_range(self):
        listing = {"city": "Ariana", "price": 250000}
        criteria = AlertCriteria(price_min=200000, price_max=300000)
        matched = matches_criteria(listing, criteria)
        assert "price>=200000" in matched
        assert "price<=300000" in matched

    def test_price_below_min_no_match(self):
        listing = {"city": "Ariana", "price": 100000}
        criteria = AlertCriteria(price_min=200000)
        assert matches_criteria(listing, criteria) == []

    def test_price_above_max_no_match(self):
        listing = {"city": "Ariana", "price": 500000}
        criteria = AlertCriteria(price_max=300000)
        assert matches_criteria(listing, criteria) == []

    def test_rooms_range(self):
        listing = {"city": "Ariana", "rooms": 3, "price": 200000}
        criteria = AlertCriteria(rooms_min=2, rooms_max=4)
        matched = matches_criteria(listing, criteria)
        assert "rooms>=2" in matched
        assert "rooms<=4" in matched

    def test_surface_range(self):
        listing = {"city": "Ariana", "surface": 120.0, "price": 200000}
        criteria = AlertCriteria(surface_min=100.0, surface_max=200.0)
        matched = matches_criteria(listing, criteria)
        assert "surface>=100.0" in matched
        assert "surface<=200.0" in matched

    def test_property_type(self):
        listing = {"property_type": "Apartment", "price": 200000}
        criteria = AlertCriteria(property_type="apartment")
        matched = matches_criteria(listing, criteria)
        assert "type:apartment" in matched

    def test_transaction_type(self):
        listing = {"transaction_type": "sale", "price": 200000}
        criteria = AlertCriteria(transaction_type="sale")
        matched = matches_criteria(listing, criteria)
        assert "transaction:sale" in matched

    def test_no_criteria_matches_nothing(self):
        listing = {"city": "Ariana", "price": 200000, "rooms": 3}
        criteria = AlertCriteria()
        assert matches_criteria(listing, criteria) == []

    def test_missing_listing_fields_skip_gracefully(self):
        listing = {"price": 200000}
        criteria = AlertCriteria(city="Ariana", rooms_min=2, surface_min=80.0)
        assert matches_criteria(listing, criteria) == []

    def test_area_legacy_field(self):
        listing = {"city": "La Marsa", "price": 300000}
        criteria = AlertCriteria(area="Marsa")
        matched = matches_criteria(listing, criteria)
        assert "area:Marsa" in matched

    def test_multiple_criteria_all_match(self):
        listing = {
            "city": "Ariana",
            "governorate": "Ariana",
            "price": 250000,
            "rooms": 3,
            "surface": 120.0,
            "property_type": "Apartment",
            "transaction_type": "sale",
        }
        criteria = AlertCriteria(
            city="Ariana",
            governorate="Ariana",
            price_min=200000,
            price_max=300000,
            rooms_min=2,
            rooms_max=4,
            surface_min=100.0,
            surface_max=200.0,
            property_type="apartment",
            transaction_type="sale",
        )
        matched = matches_criteria(listing, criteria)
        assert len(matched) >= 9


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.utcnow().isoformat()


def _hours_ago_iso(h):
    return (datetime.utcnow() - timedelta(hours=h)).isoformat()


@pytest.fixture
def alert_db(tmp_path):
    """A small archive with known listings for alert testing."""
    db = sqlite3.connect(tmp_path / "alerts.db")
    db.executescript("""
        CREATE TABLE listing (
            portal TEXT, listing_id TEXT, url TEXT,
            first_seen_at TEXT, last_seen_at TEXT
        );
        CREATE TABLE listing_observation (
            id INTEGER PRIMARY KEY, portal TEXT, listing_id TEXT,
            observed_at TEXT, run_id TEXT, content_hash TEXT,
            gallery_hash TEXT, price INTEGER, currency TEXT,
            status TEXT, payload TEXT
        );
    """)

    now = _now_iso()
    recent = _hours_ago_iso(6)
    old = _hours_ago_iso(48)

    # A recent new listing in Ariana
    db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
               ("tayara", "t1", "https://tayara.tn/t1", recent, now))
    db.execute(
        "INSERT INTO listing_observation (portal, listing_id, observed_at, price, currency, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("tayara", "t1", recent, 280000, "TND",
         json.dumps({"city": "Ariana", "governorate": "Ariana", "rooms": 3,
                      "surface": 120, "title": "S+2 Ariana"})))

    # An old listing (should not appear in 24h lookback)
    db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
               ("tayara", "t2", "https://tayara.tn/t2", old, old))
    db.execute(
        "INSERT INTO listing_observation (portal, listing_id, observed_at, price, currency, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("tayara", "t2", old, 400000, "TND",
         json.dumps({"city": "Tunis", "governorate": "Tunis", "rooms": 4})))

    # A listing with a price reduction (two observations)
    db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
               ("mubawab", "m1", "https://mubawab.tn/m1", old, now))
    db.execute(
        "INSERT INTO listing_observation (portal, listing_id, observed_at, price, currency, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("mubawab", "m1", old, 350000, "TND",
         json.dumps({"city": "La Marsa", "governorate": "Tunis", "rooms": 2,
                      "title": "S+1 La Marsa"})))
    db.execute(
        "INSERT INTO listing_observation (portal, listing_id, observed_at, price, currency, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("mubawab", "m1", recent, 320000, "TND",
         json.dumps({"city": "La Marsa", "governorate": "Tunis", "rooms": 2,
                      "title": "S+1 La Marsa"})))

    db.commit()
    return db


# ---------------------------------------------------------------------------
# get_new_listings_since
# ---------------------------------------------------------------------------

class TestGetNewListingsSince:
    def test_finds_recent_listings(self, alert_db):
        since = datetime.utcnow() - timedelta(hours=24)
        listings = get_new_listings_since(alert_db, since)
        portals = [(l["portal"], l["listing_id"]) for l in listings]
        assert ("tayara", "t1") in portals

    def test_excludes_old_listings(self, alert_db):
        since = datetime.utcnow() - timedelta(hours=24)
        listings = get_new_listings_since(alert_db, since)
        portals = [(l["portal"], l["listing_id"]) for l in listings]
        assert ("tayara", "t2") not in portals


# ---------------------------------------------------------------------------
# get_price_reductions
# ---------------------------------------------------------------------------

class TestGetPriceReductions:
    def test_finds_price_drop(self, alert_db):
        since = datetime.utcnow() - timedelta(hours=24)
        reductions = get_price_reductions(alert_db, since)
        drops = [(r["portal"], r["listing_id"]) for r in reductions]
        assert ("mubawab", "m1") in drops

    def test_price_change_is_negative(self, alert_db):
        since = datetime.utcnow() - timedelta(hours=24)
        reductions = get_price_reductions(alert_db, since)
        for r in reductions:
            if r["portal"] == "mubawab" and r["listing_id"] == "m1":
                assert r["price_change"] < 0
                assert r["previous_price"] == 350000
                assert r["price"] == 320000


# ---------------------------------------------------------------------------
# evaluate_alerts
# ---------------------------------------------------------------------------

class TestEvaluateAlerts:
    def test_matching_criteria_fires_new_listing_alert(self, alert_db):
        criteria = AlertCriteria(city="Ariana", price_max=300000)
        alerts = evaluate_alerts(alert_db, criteria, lookback_hours=24)
        new = [a for a in alerts if a.alert_type == "new_listing"]
        assert len(new) >= 1
        assert new[0].portal == "tayara"
        assert new[0].listing_id == "t1"

    def test_non_matching_criteria_fires_nothing(self, alert_db):
        criteria = AlertCriteria(city="Sfax")
        alerts = evaluate_alerts(alert_db, criteria, lookback_hours=24)
        assert alerts == []

    def test_price_reduction_alert_fires(self, alert_db):
        criteria = AlertCriteria(city="Marsa")
        alerts = evaluate_alerts(alert_db, criteria, lookback_hours=24)
        reductions = [a for a in alerts if a.alert_type == "price_reduction"]
        assert len(reductions) >= 1
        assert reductions[0].price_change < 0


# ---------------------------------------------------------------------------
# format_alert_digest
# ---------------------------------------------------------------------------

class TestFormatAlertDigest:
    def test_empty_alerts(self):
        assert "No alerts" in format_alert_digest([])

    def test_digest_contains_listing_info(self):
        alert = PropertyAlert(
            alert_type="new_listing",
            portal="tayara", listing_id="t1",
            url="https://tayara.tn/t1",
            title="S+2 Ariana", price=280000, currency="TND",
            surface=120.0, rooms=3,
            city="Ariana", governorate="Ariana",
            matched_criteria=["city:Ariana"],
        )
        digest = format_alert_digest([alert])
        assert "NEW LISTINGS" in digest
        assert "280,000" in digest
        assert "Ariana" in digest

    def test_digest_contains_price_reduction(self):
        alert = PropertyAlert(
            alert_type="price_reduction",
            portal="mubawab", listing_id="m1",
            url="https://mubawab.tn/m1",
            title="S+1 La Marsa", price=320000, currency="TND",
            surface=None, rooms=2,
            city="La Marsa", governorate="Tunis",
            price_change=-30000, previous_price=350000,
            matched_criteria=["city:Marsa"],
        )
        digest = format_alert_digest([alert])
        assert "PRICE REDUCTIONS" in digest
        assert "350,000" in digest
        assert "320,000" in digest
