"""Dedup eval set: classify 20 synthetic pairs at dedup_threshold=0.87.

Balances R28 (drop exact duplicates) against R32 (keep semantically distinct).
Classes:
  similarity >= threshold            -> DUPLICATE (merge/refuse)
  0.85 <= similarity < threshold      -> NEAR_MISS (human review)
  similarity < 0.85                    -> DISTINCT (keep)
"""
import math

from jester.agents.archivist import classify_similarity


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * y for x, y in zip(b, b)))
    return dot / (na * nb)


def test_dedup_eval_set():
    threshold = 0.87
    # build deterministic vectors
    base = [1.0, 0.5, -0.3, 0.2]
    exact = base
    near = [0.9160, 0.0696, 0.0617, 0.3905]  # cosine ~0.860 (NEAR_MISS)
    distinct = [0.1, -0.9, 0.4, 0.0]  # orthogonal-ish (DISTINCT, cos ~ -0.40)

    pairs = []
    # 8 exact duplicates
    for _ in range(8):
        pairs.append((exact, exact, "DUPLICATE"))
    # 7 near-miss paraphrases
    for _ in range(7):
        pairs.append((base, near, "NEAR_MISS"))
    # 5 distinct comments
    for _ in range(5):
        pairs.append((base, distinct, "DISTINCT"))

    assert len(pairs) == 20
    for a, b, expected in pairs:
        sim = _cos(a, b)
        got = classify_similarity(sim, threshold)
        assert got == expected, f"sim={sim:.3f} expected {expected} got {got}"
