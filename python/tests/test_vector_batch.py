"""F-06: batch vector ops + offline threshold sweep."""
import json

from jester.dedup_sweep import cosine, default_thresholds, load_pairs, sweep
from jester.embed import FakeEmbedding
from jester.vector import UnavailableVectorStore, VectorStore


def _store():
    return VectorStore.in_memory(FakeEmbedding())


def test_upsert_many_matches_singles():
    store = _store()
    items = [(f"k{i}", f"deploy pain text number {i}", {"category": "pain_point"})
             for i in range(5)]
    assert store.upsert_many(items) == 5
    assert store.count() == 5
    # Same rows, same neighbours as single upserts.
    for key, text, _p in items:
        single = store.search_scored(text)
        assert single and single[0][1] == key


def test_upsert_many_empty_is_zero():
    assert _store().upsert_many([]) == 0


def test_upsert_many_force_fail_raises_for_caller_fallback():
    # force_fail covers writes only (reads never had it): the point is the
    # batch write fails exactly like the single write, so the caller's
    # per-item fallback triggers.
    store = VectorStore.in_memory(FakeEmbedding(), force_fail=True)
    for fn in (lambda: store.upsert("k", "t", {}),
               lambda: store.upsert_many([("k", "t", {})])):
        try:
            fn()
        except RuntimeError:
            pass
        else:
            raise AssertionError("batch writes must fail like single writes")


def test_unavailable_store_raises_on_batch_ops():
    store = UnavailableVectorStore(RuntimeError("down"))
    for fn in (lambda: store.upsert_many([("k", "t", {})]),
               lambda: store.search_scored_many(["t"])):
        try:
            fn()
        except RuntimeError as exc:
            assert str(exc) == "down"
        else:
            raise AssertionError("unavailable store must raise")


def test_search_scored_many_aligns_with_singles():
    store = _store()
    texts = [f"deploy pain text number {i}" for i in range(4)]
    store.upsert_many([(f"k{i}", t, {}) for i, t in enumerate(texts)])
    batched = store.search_scored_many(texts, limit=3)
    assert len(batched) == 4
    for text, neighbours in zip(texts, batched):
        single = store.search_scored(text, limit=3)
        assert [k for _s, k in neighbours] == [k for _s, k in single]
        assert [round(s, 6) for s, _k in neighbours] == \
               [round(s, 6) for s, _k in single]


def test_search_scored_many_empty_is_empty():
    assert _store().search_scored_many([]) == []


def _pair_file(tmp_path):
    # Identical texts (cosine 1.0, same=True) vs distinct texts (same=False).
    lines = [
        {"a": "deploy takes all weekend", "b": "deploy takes all weekend",
         "same": True},
        {"a": "deploy takes all weekend and breaks prod",
         "b": "deploy takes all weekend and breaks prod", "same": True},
        {"a": "deploy takes all weekend", "b": "quarterly tax filing deadlines",
         "same": False},
        {"a": "refund policy for late trains", "b": "best espresso grind size",
         "same": False},
        "not json",
        {"a": 1, "b": "x", "same": True},
    ]
    path = tmp_path / "pairs.jsonl"
    path.write_text("\n".join(m if isinstance(m, str) else json.dumps(m)
                              for m in lines), encoding="utf-8")
    return str(path)


def test_load_pairs_skips_bad_lines(tmp_path):
    pairs, skipped = load_pairs(_pair_file(tmp_path))
    assert len(pairs) == 4
    assert skipped == 2


def test_sweep_finds_perfect_threshold_for_separable_pairs(tmp_path):
    pairs, _ = load_pairs(_pair_file(tmp_path))
    result = sweep(pairs, FakeEmbedding(),
                   default_thresholds(configured=0.87))
    assert result["n_pairs"] == 4
    assert any(r["threshold"] == 0.87 for r in result["rows"])
    best = result["best"]
    # Identical texts score 1.0, distinct hash-vectors score far below, so a
    # perfect split exists and the sweep must find F1 == 1.0.
    assert best["f1"] == 1.0
    assert best["tp"] == 2 and best["fp"] == 0


def test_sweep_empty_pairs_is_honest():
    result = sweep([], FakeEmbedding())
    assert result == {"rows": [], "best": None, "n_pairs": 0,
                      "note": "no pairs"}


def test_cosine_known_values():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0  # zero-vector guard


def test_default_thresholds_include_off_grid_configured():
    grid = default_thresholds(configured=0.95)
    assert 0.95 in grid
    assert grid == sorted(grid)
    assert default_thresholds(configured=0.87)[0] == 0.80
