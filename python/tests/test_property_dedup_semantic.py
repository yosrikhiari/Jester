"""`semantic_similarity` against the vector API that actually exists.

This function had never returned a non-zero score. It called
`vec_store.search(text, top_k=10)`, but `VectorStore.search` takes
`(text, threshold)` and has no `top_k`, so every call raised TypeError. The
`except Exception` around it turned that into `(0.0, "...")` and the caller
reads only the first half, so the dedup's semantic signal was silently dead
and looked exactly like "these listings are not similar".

A linter found it, which is the uncomfortable part: nothing here exercised
the function, so nothing could. These tests use a stub that answers with the
same shapes Qdrant does - ScoredPoint objects with `.score` and `.payload`,
not dicts - so a signature drift on either side fails loudly here instead of
being absorbed into a plausible zero.
"""
import pytest

from jester.agents.property_dedup import ListingRecord, semantic_similarity


class _Point:
    """What query_points actually hands back: an object, not a dict."""

    def __init__(self, score, payload):
        self.score = score
        self.payload = payload


class _Store:
    """Accepts only the real signature, `search(text, threshold)`."""

    def __init__(self, points=()):
        self.points = list(points)
        self.calls = []

    def search(self, text, threshold):
        self.calls.append((text, threshold))
        return self.points


def _rec(portal, listing_id, **payload):
    return ListingRecord(
        portal=portal, listing_id=listing_id, url="", price=None, currency="TND",
        surface=None, rooms=None, bedrooms=None, bathrooms=None,
        city=payload.pop("city", None), governorate=None, neighborhood=None,
        latitude=None, longitude=None, seller=None, seller_type=None,
        payload=payload, gallery_hash=None, phashes=[])


def test_a_matching_neighbour_returns_its_score():
    """The regression: this returned 0.0 for every input ever passed to it."""
    a = _rec("tayara", "1", title="Villa with a pool", city="Tunis")
    b = _rec("mubawab", "9", title="Villa with pool")
    store = _Store([_Point(0.91, {"portal": "mubawab", "listing_id": "9"})])

    score, error = semantic_similarity(store, a, b)

    assert error is None
    assert score == pytest.approx(0.91)


def test_it_calls_search_the_way_search_is_declared():
    """The bug was the call itself. A stub that refuses anything but the real
    signature is what makes that visible."""
    a = _rec("tayara", "1", title="Villa", description="x" * 900, city="Tunis")
    store = _Store()

    semantic_similarity(store, a, _rec("mubawab", "9"))

    assert len(store.calls) == 1
    text, threshold = store.calls[0]
    assert threshold == 0.0, "a threshold cut here makes 'not similar' and 'cut off' identical"
    assert "Villa" in text and "Tunis" in text
    assert len(text) < 900, "the description is truncated before embedding"


def test_no_match_among_the_neighbours_is_zero_and_not_an_error():
    a = _rec("tayara", "1", title="Villa")
    store = _Store([_Point(0.99, {"portal": "tayara", "listing_id": "77"})])

    score, error = semantic_similarity(store, a, _rec("mubawab", "9"))

    assert (score, error) == (0.0, None)


def test_a_broken_store_reports_why_instead_of_a_plausible_zero():
    """0.0 with an error is indistinguishable from 0.0 without one to the
    caller, which is how this stayed hidden. The message must at least be
    there for whoever goes looking."""

    class _Angry:
        def search(self, text, threshold):
            raise RuntimeError("qdrant is down")

    score, error = semantic_similarity(_Angry(), _rec("tayara", "1"),
                                       _rec("mubawab", "9"))
    assert score == 0.0
    assert "qdrant is down" in error


def test_the_real_vector_store_still_has_the_signature_this_relies_on():
    """If VectorStore.search grows a parameter, this test fails here rather
    than in a silent 0.0 six months later."""
    import inspect

    from jester.vector import VectorStore

    params = list(inspect.signature(VectorStore.search).parameters)
    assert params == ["self", "text", "threshold"], \
        "semantic_similarity calls search(text, threshold) positionally"
