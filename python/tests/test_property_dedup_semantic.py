"""The semantic layer of property dedup, and what it answers when it cannot.

This function has been silent twice for two different reasons, and both times
the silence looked like an answer:

1. It called `search(text, top_k=10)` when `VectorStore.search` takes
   `(text, threshold)`. Every call raised TypeError, the `except` turned that
   into `(0.0, "...")`, and the caller read only the first half.
2. After the signature was fixed it still returned `0.0` for a candidate that
   was simply never embedded, because `search` only sees the points that exist
   and an absent point looks identical to a dissimilar one. Nothing in the
   codebase ever wrote a property vector, so the index was always empty and
   every pair was "confirmed dissimilar" at 0.0.

Both cases are the same failure: an unknown semantic answer reported as a known
zero. `check_duplicate` treats 0.0 as measured, feeds it to the combined rule
(`0.6*struct + 0.4*sem >= 0.85`), and a pair that would clear the structured-only
bar is called NOT a duplicate. A validation run over 1000 real listings reported
zero duplicates.

So the layer now answers three things, not two: a score, or `None` with the
reason it has no score. And it scores from the two vectors themselves rather
than from a top-5 neighbour window, which is what makes "not indexed" and
"indexed but far apart" distinguishable at all.
"""
import inspect

import pytest

from jester.agents.property_dedup import (
    ListingRecord,
    check_duplicate,
    listing_key,
    listing_text,
    semantic_similarity,
)


class _Store:
    """A property index that answers with vectors, the way a synced one does.

    Only the two methods this layer actually uses are present, so a drift in
    what the reader asks for fails loudly here instead of being absorbed into
    a plausible zero.
    """

    def __init__(self, index=None, vectors=None, error=None):
        self.index = index or {}      # listing key -> stored vector
        self.vectors = vectors or {}  # text -> vector
        self.error = error
        self.embedded = []

    def embed_text(self, text):
        if self.error:
            raise self.error
        self.embedded.append(text)
        return self.vectors[text]

    def vector_for(self, key):
        if self.error:
            raise self.error
        return self.index.get(key)


def _rec(portal, listing_id, **payload):
    return ListingRecord(
        portal=portal, listing_id=listing_id, url="", price=None, currency="TND",
        surface=None, rooms=None, bedrooms=None, bathrooms=None,
        city=payload.pop("city", None), governorate=None, neighborhood=None,
        latitude=None, longitude=None, seller=None, seller_type=None,
        payload=payload, gallery_hash=None, phashes=[])


NEAR = [1.0, 0.0]
FAR = [0.0, 1.0]


class TestTheScoreComesFromBothVectors:
    def test_an_indexed_candidate_is_scored_on_its_own_vector(self):
        text = listing_text(_rec("tayara", "1", title="Villa with a pool"))
        store = _Store(index={listing_key("mubawab", "9"): NEAR},
                       vectors={text: NEAR})

        score, why = semantic_similarity(
            store, _rec("tayara", "1", title="Villa with a pool"),
            _rec("mubawab", "9"), a_vector=store.embed_text(text))

        assert why is None
        assert score == pytest.approx(1.0)

    def test_who_else_is_in_the_index_does_not_change_the_score(self):
        """The old lookup asked for the five nearest and searched them for
        candidate b. A real index has thousands of listings and b can sit in
        position six for any pair that is genuinely the same property."""
        text = listing_text(_rec("tayara", "1", title="Villa"))
        index = {listing_key("x", str(i)): NEAR for i in range(20)}
        index[listing_key("mubawab", "9")] = FAR
        store = _Store(index=index, vectors={text: NEAR})

        score, why = semantic_similarity(
            store, _rec("tayara", "1", title="Villa"), _rec("mubawab", "9"),
            a_vector=store.embed_text(text))

        assert (score, why) == (0.0, None), \
            "a measured disagreement is a real answer, not unknown"


class TestUnknownIsNotZero:
    def test_a_candidate_that_was_never_embedded_is_unknown(self):
        """The bug DD-M4 is about: an empty index made every pair read as
        "these two are not similar"."""
        text = listing_text(_rec("tayara", "1", title="Villa"))
        store = _Store(index={}, vectors={text: NEAR})

        score, why = semantic_similarity(
            store, _rec("tayara", "1", title="Villa"), _rec("mubawab", "9"),
            a_vector=store.embed_text(text))

        assert score is None, "0.0 claims the two texts were compared"
        assert why and "mubawab/9" in why

    def test_a_target_with_nothing_to_say_is_unknown(self):
        """An empty text embeds to nothing meaningful; measuring it against a
        real vector would be a number with no fact behind it."""
        store = _Store(index={listing_key("mubawab", "9"): NEAR}, vectors={})

        score, why = semantic_similarity(store, _rec("tayara", "1"),
                                         _rec("mubawab", "9"))

        assert score is None
        assert why and "no text" in why

    def test_a_store_that_raises_reports_why(self):
        store = _Store(error=RuntimeError("qdrant is down"))

        score, why = semantic_similarity(store, _rec("tayara", "1", title="Villa"),
                                         _rec("mubawab", "9"))

        assert score is None
        assert "qdrant is down" in why

    def test_the_target_is_embedded_once_for_the_whole_check(self):
        """Per-candidate embedding was the cost half of the finding: the same
        text was sent to the backend once per candidate, so a blocked listing
        cost n embeds and n searches for one comparison."""
        archive = _Archive()
        archive.add("tayara", "ta1", 995000, {"title": "House", "city": "Zonnebloem"})
        for i in range(3):
            archive.add("mubawab", f"m{i}", 995000,
                        {"title": "House", "city": "Zonnebloem"})
        text = listing_text(archive.target())
        store = _Store(index={listing_key("mubawab", f"m{i}"): NEAR for i in range(3)},
                       vectors={text: NEAR})

        check_duplicate(archive.db, store, "tayara", "ta1")

        assert len(store.embedded) == 1, \
            f"the target was embedded {len(store.embedded)} times, once per candidate"


class TestTheContractHolds:
    def test_the_writer_and_the_reader_agree_on_the_key(self):
        """The sync pass and the lookup are separate functions on separate
        days; if they disagree on the key every pair becomes unknown again and
        the index silently stops being used."""
        assert listing_key("tayara", "ta1") == "tayara/ta1"

    def test_the_listing_text_is_module_level_and_truncates(self):
        """It used to be a closure inside semantic_similarity, which is why a
        caller that needed the target's vector before the loop could not get
        it. The description cap is behaviour predating the move."""
        a = _rec("tayara", "1", title="Villa", description="x" * 900,
                 city="Tunis", governorate="Tunis", property_type="House")
        text = listing_text(a)
        assert "Villa" in text and "Tunis" in text and "House" in text
        assert len(text) < 900

    def test_the_real_vector_store_has_the_two_methods(self):
        from jester.vector import VectorStore

        assert list(inspect.signature(VectorStore.embed_text).parameters) == \
            ["self", "text"]
        assert list(inspect.signature(VectorStore.vector_for).parameters) == \
            ["self", "unique_key"]


class _Archive:
    """The three listing tables check_duplicate reads, with nothing else."""

    def __init__(self):
        import sqlite3
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            CREATE TABLE listing (portal TEXT, listing_id TEXT, url TEXT,
                                  first_seen_at TEXT, last_seen_at TEXT);
            CREATE TABLE listing_observation (id INTEGER PRIMARY KEY, portal TEXT,
                                  listing_id TEXT, observed_at TEXT, run_id TEXT,
                                  content_hash TEXT, gallery_hash TEXT, price INTEGER,
                                  currency TEXT, status TEXT, payload TEXT);
            CREATE TABLE listing_media (observation_id INTEGER, position INTEGER,
                                  url TEXT, phash TEXT);
            """)

    def add(self, portal, lid, price, payload):
        import json
        self.db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
                        (portal, lid, f"https://{portal}.test/{lid}", "", ""))
        self.db.execute(
            "INSERT INTO listing_observation (portal, listing_id, price, currency, payload)"
            " VALUES (?,?,?,?,?)", (portal, lid, price, "TND", json.dumps(payload)))
        self.db.commit()

    def target(self):
        from jester.agents.property_dedup import load_listing_from_obs
        return load_listing_from_obs(self.db, "tayara", "ta1")


#: Same price, same suburb, same bedrooms, different portals - the structured
#: shape of a real duplicate, and the reason the structured-only rule exists.
def _dup_archive():
    archive = _Archive()
    archive.add("tayara", "ta1", 995000,
                {"title": "1 bed apartment", "city": "Zonnebloem", "bedrooms": "1"})
    archive.add("mubawab", "mu1", 995000,
                {"title": "One bedroom flat", "city": "Zonnebloem", "bedrooms": "1"})
    return archive


class TestCheckDuplicateUsesTheAnswer:
    def test_an_unindexed_pair_is_judged_by_the_structured_rule(self):
        """The end-to-end M4 bug. With the phantom 0.0 this returned
        is_duplicate=False: 0.6*1.0 + 0.4*0.0 = 0.6 never reaches 0.85, and the
        structured-only branch - which would have caught it - was only reached
        when the store RAISED."""
        archive = _dup_archive()
        store = _Store(index={}, vectors={})

        result = check_duplicate(archive.db, store, "tayara", "ta1")

        assert result.is_duplicate is True
        assert result.semantic_score is None
        assert any("structured-only" in r for r in result.reasons)

    def test_a_measured_low_semantic_score_does_not_open_the_structured_bar(self):
        """The other direction: when the index answers, the combined rule
        applies and a strong-looking structured match on its own is not enough.
        Without this, "unknown" would be the easy verdict for everything."""
        archive = _dup_archive()
        text = listing_text(_rec("tayara", "ta1", title="1 bed apartment",
                                 city="Zonnebloem"))
        store = _Store(index={listing_key("mubawab", "mu1"): FAR},
                       vectors={text: NEAR})

        result = check_duplicate(archive.db, store, "tayara", "ta1")

        assert result.is_duplicate is False
        assert result.semantic_score == pytest.approx(0.0)
        assert not any("structured-only" in r for r in result.reasons)

    def test_a_measured_high_semantic_score_carries_a_weak_pair_over(self):
        """The semantic half must still be worth something once it can answer.
        Same price, same suburb, rooms 3 against 5: structured 0.80, which fails
        the 0.95 structured-only bar. With the vectors compared it is
        0.6*0.80 + 0.4*1.00 = 0.88, and the pair is a duplicate only because the
        descriptions were measured against each other."""
        archive = _Archive()
        archive.add("tayara", "ta1", 995000,
                    {"title": "Apartment", "city": "Zonnebloem", "rooms": "3"})
        archive.add("mubawab", "mu1", 995000,
                    {"title": "Apartment", "city": "Zonnebloem", "rooms": "5"})
        text = listing_text(_rec("tayara", "ta1", title="Apartment",
                                 city="Zonnebloem"))
        store = _Store(index={listing_key("mubawab", "mu1"): NEAR},
                       vectors={text: NEAR})

        result = check_duplicate(archive.db, store, "tayara", "ta1")

        assert 0.75 <= result.structured_score < 0.95
        assert result.semantic_score == pytest.approx(1.0)
        assert result.is_duplicate is True
