"""Property analytics: price-per-m2, area stats, market trends, suspicious detection."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from jester.agents.property_analytics import (
    PriceStats,
    ListingAnalytics,
    MarketTrend,
    compute_price_per_m2,
    compute_area_price_stats,
    analyze_listing_vs_market,
    compute_market_trends,
    detect_duplicates_and_suspicious,
    generate_price_heatmap_data,
    get_all_listings_with_obs,
)


# ---------------------------------------------------------------------------
# compute_price_per_m2
# ---------------------------------------------------------------------------

class TestComputePricePerM2:
    def test_basic(self):
        assert compute_price_per_m2(200000, 100) == 2000.0

    def test_string_surface(self):
        assert compute_price_per_m2(200000, "100") == 2000.0

    def test_string_price(self):
        assert compute_price_per_m2("200000", 100) == 2000.0

    def test_none_price(self):
        assert compute_price_per_m2(None, 100) is None

    def test_none_surface(self):
        assert compute_price_per_m2(200000, None) is None

    def test_zero_surface(self):
        assert compute_price_per_m2(200000, 0) is None

    def test_negative_surface(self):
        assert compute_price_per_m2(200000, -10) is None

    def test_garbage_string(self):
        assert compute_price_per_m2(200000, "abc") is None


# ---------------------------------------------------------------------------
# compute_area_price_stats
# ---------------------------------------------------------------------------

def _listings_with_ppm2(city, prices, currency="TND"):
    """Build listing dicts with pre-computed price_per_m2."""
    return [
        {"city": city, "price_per_m2": p, "currency": currency,
         "portal": "test", "listing_id": str(i)}
        for i, p in enumerate(prices)
    ]


class TestComputeAreaPriceStats:
    def test_groups_by_city(self):
        listings = (
            _listings_with_ppm2("Ariana", [2000, 2200, 2400])
            + _listings_with_ppm2("La Marsa", [3000, 3500, 4000])
        )
        stats = compute_area_price_stats(listings, "city")
        areas = {s.area for s in stats}
        assert "Ariana" in areas
        assert "La Marsa" in areas

    def test_needs_minimum_samples(self):
        listings = _listings_with_ppm2("Tiny", [2000, 2200])
        stats = compute_area_price_stats(listings, "city")
        assert len(stats) == 0

    def test_median_is_correct(self):
        listings = _listings_with_ppm2("Ariana", [1000, 2000, 3000])
        stats = compute_area_price_stats(listings, "city")
        ariana = [s for s in stats if s.area == "Ariana"][0]
        assert ariana.median_price_per_m2 == 2000

    def test_average_is_correct(self):
        listings = _listings_with_ppm2("Ariana", [1000, 2000, 3000])
        stats = compute_area_price_stats(listings, "city")
        ariana = [s for s in stats if s.area == "Ariana"][0]
        assert ariana.avg_price_per_m2 == 2000.0

    def test_sorted_descending_by_average(self):
        listings = (
            _listings_with_ppm2("Low", [1000, 1100, 1200])
            + _listings_with_ppm2("High", [5000, 5100, 5200])
        )
        stats = compute_area_price_stats(listings, "city")
        assert stats[0].area == "High"

    def test_respects_currency_grouping(self):
        listings = (
            _listings_with_ppm2("CityA", [2000, 2200, 2400], currency="TND")
            + _listings_with_ppm2("CityA", [500, 600, 700], currency="EUR")
        )
        stats = compute_area_price_stats(listings, "city")
        currencies = {s.currency for s in stats}
        assert "TND" in currencies
        assert "EUR" in currencies


# ---------------------------------------------------------------------------
# analyze_listing_vs_market
# ---------------------------------------------------------------------------

class TestAnalyzeListingVsMarket:
    def test_listing_above_average_is_positive(self):
        city_stats = [PriceStats(
            area="Ariana", area_type="city", listing_count=10,
            avg_price_per_m2=2000, median_price_per_m2=2000,
            min_price_per_m2=1500, max_price_per_m2=2500, currency="TND",
        )]
        listings = [{"portal": "tayara", "listing_id": "1", "url": "u",
                     "city": "Ariana", "currency": "TND", "price_per_m2": 2500,
                     "days_on_market": 10}]
        results = analyze_listing_vs_market(listings, city_stats)
        assert results[0].price_vs_avg_pct > 0

    def test_listing_below_30pct_is_suspicious(self):
        city_stats = [PriceStats(
            area="Ariana", area_type="city", listing_count=10,
            avg_price_per_m2=2000, median_price_per_m2=2000,
            min_price_per_m2=1500, max_price_per_m2=2500, currency="TND",
        )]
        listings = [{"portal": "tayara", "listing_id": "1", "url": "u",
                     "city": "Ariana", "currency": "TND", "price_per_m2": 1000,
                     "days_on_market": 5}]
        results = analyze_listing_vs_market(listings, city_stats)
        assert results[0].is_suspicious is True

    def test_listing_at_market_is_not_suspicious(self):
        city_stats = [PriceStats(
            area="Ariana", area_type="city", listing_count=10,
            avg_price_per_m2=2000, median_price_per_m2=2000,
            min_price_per_m2=1500, max_price_per_m2=2500, currency="TND",
        )]
        listings = [{"portal": "tayara", "listing_id": "1", "url": "u",
                     "city": "Ariana", "currency": "TND", "price_per_m2": 2000,
                     "days_on_market": 10}]
        results = analyze_listing_vs_market(listings, city_stats)
        assert results[0].is_suspicious is False

    def test_missing_city_no_crash(self):
        listings = [{"portal": "tayara", "listing_id": "1", "url": "u",
                     "city": None, "currency": "TND", "price_per_m2": 2000,
                     "days_on_market": None}]
        results = analyze_listing_vs_market(listings, [])
        assert results[0].price_vs_avg_pct is None


# ---------------------------------------------------------------------------
# detect_duplicates_and_suspicious
# ---------------------------------------------------------------------------

class TestDetectDuplicatesAndSuspicious:
    def test_cross_portal_gallery_hash_duplicate(self):
        listings = [
            {"portal": "tayara", "listing_id": "1", "url": "u1",
             "gallery_hash": "abc123", "price": 200000, "currency": "TND"},
            {"portal": "mubawab", "listing_id": "2", "url": "u2",
             "gallery_hash": "abc123", "price": 200000, "currency": "TND"},
        ]
        dupes, _ = detect_duplicates_and_suspicious(listings)
        assert len(dupes) == 1
        assert set(dupes[0]["portals"]) == {"tayara", "mubawab"}

    def test_same_portal_gallery_hash_not_flagged(self):
        listings = [
            {"portal": "tayara", "listing_id": "1", "url": "u1",
             "gallery_hash": "abc123", "price": 200000, "currency": "TND"},
            {"portal": "tayara", "listing_id": "2", "url": "u2",
             "gallery_hash": "abc123", "price": 210000, "currency": "TND"},
        ]
        dupes, _ = detect_duplicates_and_suspicious(listings)
        assert len(dupes) == 0

    def test_phone_reuse_flagged_above_threshold(self):
        listings = [
            {"portal": "tayara", "listing_id": str(i), "url": f"u{i}",
             "phone": "+21612345678", "title": f"Listing {i}",
             "price": 200000 + i * 10000, "currency": "TND"}
            for i in range(6)
        ]
        _, suspicious = detect_duplicates_and_suspicious(listings)
        assert len(suspicious) == 1
        assert suspicious[0]["type"] == "phone_reuse"
        assert suspicious[0]["count"] == 6

    def test_phone_reuse_not_flagged_below_threshold(self):
        listings = [
            {"portal": "tayara", "listing_id": str(i), "url": f"u{i}",
             "phone": "+21612345678", "title": f"Listing {i}",
             "price": 200000, "currency": "TND"}
            for i in range(3)
        ]
        _, suspicious = detect_duplicates_and_suspicious(listings)
        assert len(suspicious) == 0


# ---------------------------------------------------------------------------
# generate_price_heatmap_data
# ---------------------------------------------------------------------------

class TestGeneratePriceHeatmapData:
    def test_includes_listings_with_coordinates(self):
        listings = [
            {"latitude": 36.8, "longitude": 10.2, "price_per_m2": 2000,
             "price": 200000, "currency": "TND", "surface": 100,
             "city": "Ariana", "url": "u1"},
        ]
        data = generate_price_heatmap_data(listings)
        assert len(data) == 1
        assert data[0]["lat"] == 36.8
        assert data[0]["price_per_m2"] == 2000

    def test_excludes_listings_without_coordinates(self):
        listings = [
            {"latitude": None, "longitude": 10.2, "price_per_m2": 2000,
             "price": 200000, "currency": "TND", "surface": 100,
             "city": "Ariana", "url": "u1"},
            {"latitude": 36.8, "longitude": None, "price_per_m2": 2000,
             "price": 200000, "currency": "TND", "surface": 100,
             "city": "Ariana", "url": "u2"},
        ]
        data = generate_price_heatmap_data(listings)
        assert len(data) == 0

    def test_excludes_listings_without_price_per_m2(self):
        listings = [
            {"latitude": 36.8, "longitude": 10.2, "price_per_m2": None,
             "price": 200000, "currency": "TND", "surface": 100,
             "city": "Ariana", "url": "u1"},
        ]
        data = generate_price_heatmap_data(listings)
        assert len(data) == 0


# ---------------------------------------------------------------------------
# compute_market_trends
# ---------------------------------------------------------------------------

class TestComputeMarketTrends:
    def test_returns_trend_with_enough_observations(self):
        now = datetime.utcnow()
        listings = [
            {"city": "Ariana", "currency": "TND", "price": 200000 + i * 5000,
             "surface": 100,
             "observed_at": (now - timedelta(days=20 - i)).isoformat()}
            for i in range(6)
        ]
        trends = compute_market_trends(listings, "city", period_days=30)
        assert len(trends) >= 1
        assert trends[0].area == "Ariana"
        assert trends[0].listing_count == 6

    def test_skips_areas_with_few_observations(self):
        now = datetime.utcnow()
        listings = [
            {"city": "Tiny", "currency": "TND", "price": 200000,
             "surface": 100,
             "observed_at": (now - timedelta(days=5)).isoformat()},
        ]
        trends = compute_market_trends(listings, "city", period_days=30)
        assert len(trends) == 0

    def test_rising_prices_give_positive_change(self):
        now = datetime.utcnow()
        listings = [
            {"city": "Ariana", "currency": "TND",
             "price": 100000 + i * 20000, "surface": 100,
             "observed_at": (now - timedelta(days=10 - i)).isoformat()}
            for i in range(6)
        ]
        trends = compute_market_trends(listings, "city", period_days=30)
        ariana = [t for t in trends if t.area == "Ariana"][0]
        assert ariana.price_change_pct > 0


# ---------------------------------------------------------------------------
# get_all_listings_with_obs (integration with DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def analytics_db(tmp_path):
    """A small archive with multiple observations for analytics testing."""
    db = sqlite3.connect(tmp_path / "analytics.db")
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

    now = datetime.utcnow().isoformat()
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
               ("tayara", "t1", "https://tayara.tn/t1", week_ago, now))
    db.execute(
        "INSERT INTO listing_observation "
        "(portal, listing_id, observed_at, price, currency, gallery_hash, payload) "
        "VALUES (?,?,?,?,?,?,?)",
        ("tayara", "t1", week_ago, 200000, "TND", "gal1",
         json.dumps({"city": "Ariana", "surface": 100, "rooms": 3,
                      "title": "S+2 Ariana"})))
    db.execute(
        "INSERT INTO listing_observation "
        "(portal, listing_id, observed_at, price, currency, gallery_hash, payload) "
        "VALUES (?,?,?,?,?,?,?)",
        ("tayara", "t1", now, 195000, "TND", "gal1",
         json.dumps({"city": "Ariana", "surface": 100, "rooms": 3,
                      "title": "S+2 Ariana"})))

    db.commit()
    return db


class TestGetAllListingsWithObs:
    def test_loads_listing_with_observations(self, analytics_db):
        listings = get_all_listings_with_obs(analytics_db)
        assert len(listings) == 1
        l = listings[0]
        assert l["portal"] == "tayara"
        assert l["listing_id"] == "t1"
        assert l["observation_count"] == 2

    def test_price_history_recorded(self, analytics_db):
        listings = get_all_listings_with_obs(analytics_db)
        l = listings[0]
        prices = [p for _, p in l["price_history"]]
        assert 200000 in prices
        assert 195000 in prices

    def test_price_per_m2_computed(self, analytics_db):
        listings = get_all_listings_with_obs(analytics_db)
        l = listings[0]
        assert l["price_per_m2"] == pytest.approx(1950.0)
