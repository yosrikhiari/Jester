"""Cross-portal dedup: the rules that decide whether two listings are one property.

Every case here came off a real 1000-listing, three-portal archive. The
harness previously reported zero duplicates over that archive and looked
healthy doing it, so these tests are written against the specific ways it
managed to be silent.
"""
import json
import sqlite3

import pytest

from jester.agents.property_dedup import (
    ListingRecord,
    STRUCTURED_ONLY_MIN_EVIDENCE,
    STRUCTURED_ONLY_THRESHOLD,
    PHASH_SAME_THRESHOLD,
    _place_agreement,
    find_candidates_by_blocking,
    find_candidates_by_phash,
    structured_similarity_detailed,
)


def rec(portal, listing_id, **kw):
    base = dict(
        url=f"https://{portal}.test/{listing_id}",
        price=None, currency="ZAR", surface=None, rooms=None, bedrooms=None,
        bathrooms=None, city=None, governorate=None, neighborhood=None,
        latitude=None, longitude=None, seller=None, seller_type=None,
        payload={}, gallery_hash=None, phashes=[],
    )
    base.update(kw)
    return ListingRecord(portal=portal, listing_id=listing_id, **base)


class TestPlaceAgreement:
    """Portals describe the same suburb at different granularity."""

    @pytest.mark.parametrize("a,b", [
        ("Kenilworth Upper", "Kenilworth Upper, Southern Suburbs"),
        ("Belhar", "Belhar, Cape Flats"),
        ("Claremont", "Claremont Upper, Southern Suburbs"),
        ("Zonnebloem", "Zonnebloem, Cape Town City Bowl"),
    ])
    def test_coarser_description_is_full_agreement(self, a, b):
        # These are real pairs. Compared with `==` every one reads as a
        # mismatch, which is how the matcher rejected genuine duplicates.
        assert _place_agreement(a, b) == 1.0

    def test_generic_words_alone_are_not_comparable(self):
        # "Cape Town City Centre" is entirely filler. An empty comparison has
        # to be NOT COMPARABLE, never a match - otherwise every listing in the
        # city agrees with every other.
        assert _place_agreement("Cape Town City Centre", "Gardens, Cape Town City Bowl") is None

    def test_missing_side_is_not_comparable(self):
        assert _place_agreement(None, "Belhar") is None
        assert _place_agreement("Belhar", "") is None

    def test_different_suburbs_do_not_agree_fully(self):
        assert _place_agreement("Rondebosch", "Newlands") == 0.0


class TestEvidence:
    """A ratio is not a quantity of proof."""

    def test_price_alone_scores_full_but_carries_one_field(self):
        a = rec("property24", "1", price=1695000)
        b = rec("privateproperty", "2", price=1695000)
        score, _, evidence = structured_similarity_detailed(a, b)
        # The score is 1.0 because the one comparable field agreed. That is
        # exactly why the score alone cannot be the rule: R1 695 000 is a round
        # asking price shared by unrelated listings all day.
        assert score == 1.0
        assert evidence < STRUCTURED_ONLY_MIN_EVIDENCE

    def test_price_and_location_clear_the_floor(self):
        a = rec("property24", "1", price=14950000, city="Kenilworth Upper")
        b = rec("privateproperty", "2", price=14950000,
                city="Kenilworth Upper, Southern Suburbs")
        score, _, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence >= STRUCTURED_ONLY_MIN_EVIDENCE

    def test_bedroom_mismatch_sinks_an_otherwise_perfect_match(self):
        # Both false positives found by hand-checking a real sample were this:
        # same price, same suburb, different property.
        a = rec("property24", "1", price=7950000, city="Meadowridge", bedrooms=4)
        b = rec("privateproperty", "2", price=7950000,
                city="Meadowridge, Southern Suburbs", bedrooms=3)
        score, _, _ = structured_similarity_detailed(a, b)
        assert score < STRUCTURED_ONLY_THRESHOLD

    def test_bedrooms_agreeing_keeps_the_match(self):
        a = rec("property24", "1", price=995000, city="Zonnebloem", bedrooms=1)
        b = rec("privateproperty", "2", price=995000,
                city="Zonnebloem, Cape Town City Bowl", bedrooms=1)
        score, _, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence > 2.0


@pytest.fixture
def archive(tmp_path):
    """A miniature two-portal archive."""
    db = sqlite3.connect(tmp_path / "a.db")
    db.executescript(
        """
        CREATE TABLE listing (portal TEXT, listing_id TEXT, url TEXT,
                              first_seen_at TEXT, last_seen_at TEXT);
        CREATE TABLE listing_observation (id INTEGER PRIMARY KEY, portal TEXT,
                              listing_id TEXT, observed_at TEXT, run_id TEXT,
                              content_hash TEXT, gallery_hash TEXT, price INTEGER,
                              currency TEXT, status TEXT, payload TEXT);
        CREATE TABLE listing_media (observation_id INTEGER, position INTEGER,
                              url TEXT, phash TEXT);
        """
    )
    def add(portal, lid, price, payload):
        db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
                   (portal, lid, f"https://{portal}.test/{lid}", "", ""))
        db.execute(
            "INSERT INTO listing_observation (portal, listing_id, price, currency, payload)"
            " VALUES (?,?,?,?,?)", (portal, lid, price, "ZAR", json.dumps(payload)))
    add("property24", "p1", 995000, {"location": "Zonnebloem", "bedrooms": "1"})
    add("privateproperty", "q1", 995000, {"locality": "Zonnebloem, Cape Town City Bowl"})
    add("privateproperty", "q2", 995000, {"locality": "Rondebosch"})
    add("privateproperty", "q3", 4400000, {"locality": "Zonnebloem"})
    db.commit()
    return db


class TestBlocking:
    """Candidate generation must not depend on image fingerprints."""

    def test_finds_a_cross_portal_candidate_with_no_phash_anywhere(self, archive):
        target = rec("property24", "p1", price=995000, city="Zonnebloem",
                     payload={"location": "Zonnebloem"})
        got = find_candidates_by_blocking(archive, "property24", "p1", target)
        # q1 shares price and place. q2 is the same price in another suburb;
        # q3 is the same suburb at another price. Neither is a candidate.
        assert ("privateproperty", "q1") in got
        assert ("privateproperty", "q2") not in got
        assert ("privateproperty", "q3") not in got

    def test_never_blocks_a_listing_against_its_own_portal(self, archive):
        target = rec("privateproperty", "q1", price=995000,
                     payload={"locality": "Zonnebloem, Cape Town City Bowl"})
        got = find_candidates_by_blocking(archive, "privateproperty", "q1", target)
        assert all(p != "privateproperty" for p, _ in got)

    def test_a_listing_with_no_price_blocks_nothing(self, archive):
        target = rec("property24", "p1", price=None, payload={"location": "Zonnebloem"})
        assert find_candidates_by_blocking(archive, "property24", "p1", target) == []


class TestPhotographAgreement:
    """A shared photograph counts one way only."""

    def test_a_shared_photograph_adds_evidence(self):
        a = rec("property24", "1", price=995000, city="Zonnebloem", bedrooms=1,
                phashes=["84636cac6ec8093b"])
        b = rec("privateproperty", "2", price=995000, city="Zonnebloem, Cape Town City Bowl",
                bedrooms=1, phashes=["84636cac6ec8093b"])
        score, reasons, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence > 3.0
        assert any("shares a photograph" in r for r in reasons)

    def test_different_photographs_are_not_held_against_a_pair(self):
        """Verified duplicate at R975 000: same property, distance 33.

        Two agents photograph the same house differently, and dHash survives
        no crop or watermark. Scoring the miss would reject a real duplicate,
        which is why disagreement never enters the evidence weight.
        """
        shared = dict(price=975000, bedrooms=1)
        without = structured_similarity_detailed(
            rec("property24", "1", city="Heathfield", **shared),
            rec("privateproperty", "2", city="Heathfield, Southern Suburbs", **shared))
        withdiff = structured_similarity_detailed(
            rec("property24", "1", city="Heathfield", phashes=["0000000000000000"], **shared),
            rec("privateproperty", "2", city="Heathfield, Southern Suburbs",
                phashes=["ffffffffffffffff"], **shared))
        # identical outcome: the disagreeing photographs changed nothing
        assert withdiff[0] == without[0]
        assert withdiff[2] == without[2]
        assert withdiff[0] >= STRUCTURED_ONLY_THRESHOLD

    def test_threshold_matches_the_go_side(self):
        # mediahash.SameThreshold is 12; both languages must call the same
        # pair of images identical or the archive disagrees with itself.
        assert PHASH_SAME_THRESHOLD == 12


class TestPhashCandidatesAreConfirmed:
    """A band index is a prefilter, not an answer."""

    def _archive(self, tmp_path, near, far):
        import sqlite3, json
        db = sqlite3.connect(tmp_path / "p.db")
        db.executescript(
            "CREATE TABLE listing (portal TEXT, listing_id TEXT, url TEXT,"
            " first_seen_at TEXT, last_seen_at TEXT);"
            "CREATE TABLE listing_observation (id INTEGER PRIMARY KEY, portal TEXT,"
            " listing_id TEXT, observed_at TEXT, run_id TEXT, content_hash TEXT,"
            " gallery_hash TEXT, price INTEGER, currency TEXT, status TEXT, payload TEXT);"
            "CREATE TABLE listing_media (observation_id INTEGER, position INTEGER,"
            " url TEXT, phash TEXT);"
            "CREATE TABLE listing_media_band (observation_id INTEGER, position INTEGER,"
            " band TEXT);"
        )
        for oid, (portal, lid, ph) in enumerate(
                [("a", "1", near[0]), ("b", "2", near[1]), ("b", "3", far)], start=1):
            db.execute("INSERT INTO listing VALUES (?,?,?,?,?)", (portal, lid, "", "", ""))
            db.execute("INSERT INTO listing_observation (id,portal,listing_id,price,"
                       "currency,payload) VALUES (?,?,?,?,?,?)",
                       (oid, portal, lid, 100000, "ZAR", json.dumps({})))
            db.execute("INSERT INTO listing_media VALUES (?,0,'',?)", (oid, ph))
            # Both candidates share band 0, which is what a prefilter sees.
            db.execute("INSERT INTO listing_media_band VALUES (?,0,?)", (oid, "0:" + ph[:2]))
        db.commit()
        return db

    def test_a_shared_band_is_not_a_shared_photograph(self, tmp_path):
        """Measured on a real archive: 120 listings produced 2478 band
        candidates of which exactly ONE was within the distance threshold. The
        other 2477 were loaded and scored in full, and could out-score the real
        candidate for `best_result` - so this masked genuine duplicates as well
        as wasting the work."""
        near = ("84636cac6ec8093b", "84636cac6ec8093a")   # 1 bit apart
        far = "84ffffffffffffff"                          # same band, far away
        db = self._archive(tmp_path, near, far)
        got = find_candidates_by_phash(db, "a", "1", [near[0]])
        assert ("b", "2") in got, "the near-identical photograph must survive"
        assert ("b", "3") not in got, "a shared band alone is not a match"

    def test_a_candidate_with_no_hashes_is_dropped(self, tmp_path):
        near = ("84636cac6ec8093b", "84636cac6ec8093a")
        db = self._archive(tmp_path, near, "84ffffffffffffff")
        db.execute("UPDATE listing_media SET phash='' WHERE observation_id=2")
        db.commit()
        assert find_candidates_by_phash(db, "a", "1", [near[0]]) == []


class TestTunisianCrossPortalDedup:
    """Cross-portal dedup across the four Tunisian portals.

    Each case models a real listing pattern: the same apartment described
    differently on tayara, mubawab, tunisieannonce and houni.
    """

    def test_tayara_vs_tunisieannonce_same_property(self):
        """S+2 in Ariana listed on both — different title conventions."""
        a = rec("tayara", "ta1", price=285000, currency="TND",
                city="Ariana", governorate="Ariana", rooms=3, surface=120.0,
                bedrooms=2, seller="Ahmed")
        b = rec("tunisieannonce", "ann1", price=285000, currency="TND",
                city="Ariana", governorate="Ariana", rooms=3, surface=120.0,
                bedrooms=2)
        score, reasons, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence >= STRUCTURED_ONLY_MIN_EVIDENCE

    def test_tayara_vs_houni_same_property(self):
        """Same apartment on tayara and houni — houni often lacks surface."""
        a = rec("tayara", "ta2", price=350000, currency="TND",
                city="La Soukra", governorate="Ariana", rooms=4, bedrooms=3)
        b = rec("houni", "ho1", price=350000, currency="TND",
                city="La Soukra", governorate="Ariana", rooms=4, bedrooms=3)
        score, reasons, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence >= STRUCTURED_ONLY_MIN_EVIDENCE

    def test_mubawab_vs_tunisieannonce_same_property(self):
        a = rec("mubawab", "mu1", price=420000, currency="TND",
                city="La Marsa", governorate="Tunis", rooms=4, surface=150.0,
                bedrooms=3)
        b = rec("tunisieannonce", "ann2", price=420000, currency="TND",
                city="La Marsa", governorate="Tunis", rooms=4, surface=148.0,
                bedrooms=3)
        score, reasons, evidence = structured_similarity_detailed(a, b)
        assert score >= STRUCTURED_ONLY_THRESHOLD
        assert evidence >= STRUCTURED_ONLY_MIN_EVIDENCE

    def test_houni_vs_mubawab_different_property_same_price(self):
        """Same asking price in the same city — different bedroom count."""
        a = rec("houni", "ho2", price=250000, currency="TND",
                city="Ariana", governorate="Ariana", bedrooms=1)
        b = rec("mubawab", "mu2", price=250000, currency="TND",
                city="Ariana", governorate="Ariana", bedrooms=3)
        score, _, _ = structured_similarity_detailed(a, b)
        assert score < STRUCTURED_ONLY_THRESHOLD

    def test_tunisieannonce_vs_houni_different_city(self):
        """Same price and rooms but different cities are not duplicates."""
        a = rec("tunisieannonce", "ann3", price=300000, currency="TND",
                city="Sousse", governorate="Sousse", rooms=3)
        b = rec("houni", "ho3", price=300000, currency="TND",
                city="Sfax", governorate="Sfax", rooms=3)
        score, _, _ = structured_similarity_detailed(a, b)
        assert score < STRUCTURED_ONLY_THRESHOLD

    def test_four_portal_same_listing_with_photo(self):
        """The strongest signal: same photo across portals."""
        shared_phash = "84636cac6ec8093b"
        base = dict(price=500000, currency="TND", city="Carthage",
                    governorate="Tunis", rooms=5, surface=200.0, bedrooms=4,
                    phashes=[shared_phash])
        a = rec("tayara", "ta3", **base)
        b = rec("mubawab", "mu3", **base)
        c = rec("tunisieannonce", "ann4", **base)
        d = rec("houni", "ho4", **base)
        for pair in [(a, b), (a, c), (a, d), (b, c), (b, d), (c, d)]:
            score, reasons, evidence = structured_similarity_detailed(*pair)
            assert score >= STRUCTURED_ONLY_THRESHOLD
            assert any("photograph" in r for r in reasons)


class TestTunisianBlockingIntegration:
    """Blocking finds cross-portal candidates across Tunisian portals."""

    @pytest.fixture
    def tn_archive(self, tmp_path):
        db = sqlite3.connect(tmp_path / "tn.db")
        db.executescript("""
            CREATE TABLE listing (portal TEXT, listing_id TEXT, url TEXT,
                                  first_seen_at TEXT, last_seen_at TEXT);
            CREATE TABLE listing_observation (id INTEGER PRIMARY KEY, portal TEXT,
                                  listing_id TEXT, observed_at TEXT, run_id TEXT,
                                  content_hash TEXT, gallery_hash TEXT, price INTEGER,
                                  currency TEXT, status TEXT, payload TEXT);
            CREATE TABLE listing_media (observation_id INTEGER, position INTEGER,
                                  url TEXT, phash TEXT);
        """)
        def add(portal, lid, price, payload):
            db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
                       (portal, lid, f"https://{portal}.test/{lid}", "", ""))
            db.execute(
                "INSERT INTO listing_observation (portal, listing_id, price, currency, payload)"
                " VALUES (?,?,?,?,?)", (portal, lid, price, "TND", json.dumps(payload)))

        add("tayara", "ta1", 285000, {"city": "Ariana", "governorate": "Ariana"})
        add("tunisieannonce", "ann1", 285000, {"city": "Ariana", "governorate": "Ariana"})
        add("houni", "ho1", 285000, {"city": "Ariana", "governorate": "Ariana"})
        add("mubawab", "mu1", 285000, {"city": "La Marsa", "governorate": "Tunis"})
        db.commit()
        return db

    def test_tunisieannonce_found_as_candidate_for_tayara(self, tn_archive):
        target = rec("tayara", "ta1", price=285000, currency="TND",
                     city="Ariana", payload={"city": "Ariana"})
        got = find_candidates_by_blocking(tn_archive, "tayara", "ta1", target)
        assert ("tunisieannonce", "ann1") in got
        assert ("houni", "ho1") in got

    def test_different_city_not_blocked(self, tn_archive):
        target = rec("tayara", "ta1", price=285000, currency="TND",
                     city="Ariana", payload={"city": "Ariana"})
        got = find_candidates_by_blocking(tn_archive, "tayara", "ta1", target)
        assert ("mubawab", "mu1") not in got
