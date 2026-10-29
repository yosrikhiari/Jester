"""Per-category dedup thresholds, the coupled near-miss band, and the
per-category dedup-sweep driver (F-06 second half).

The global 0.87 was never measured per category; the sweep now reports one
table per labelled-pair file, and the archivist clamps each nugget against
its category's bar when the config declares one. The near-miss band is
derived from whatever bar was APPLIED — a bare 0.85 would strand the strip
under a 0.80 bar and swallow the strip under a 0.95 one.

All tests here run on the offline fake embedding; note that its verdicts on
PARAPHRASE pairs are meaningless (a sha256 of the whole text is noise unless
the strings are literally identical) — that honesty is recorded in
docs/dedup-calibration.md, and the tables' printing does not depend on the
numbers they contain.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

from jester.agents import archivist as archivist_module
from jester.agents.archivist import (
    NEAR_MISS_WIDTH,
    Archivist,
    classify_similarity,
)
from jester.config import ConfigError, Thresholds, load_thresholds
from jester.embed import FakeEmbedding
from jester.models import Nugget
from jester.store import insert_nugget, open_db
from jester.vector import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO_ROOT / "config"
PAIRS_DIR = REPO_ROOT / "testdata" / "dedup" / "pairs"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _nugget(key: str, category: str = "pain_point",
            text: str = "self host email is painful") -> Nugget:
    return Nugget(unique_key=key, platform="reddit", category=category,
                  extracted_insight="x", raw_text=text)


class _ScriptedNeighbours:
    """search_scored returns what the test said, every time. upsert lands on
    a real in-memory store, so the rest of the loop still works."""

    def __init__(self, scored, inner=None):
        self._scored = scored
        self._inner = inner or VectorStore.in_memory(FakeEmbedding())

    def search_scored(self, text, limit=5):
        return self._scored

    def upsert(self, unique_key, text, payload):
        return self._inner.upsert(unique_key, text, payload)


# ---- config ----------------------------------------------------------------

def test_config_validates_per_category_dedup_values():
    # in-range entries pass validation and stay on the dataclass
    t = Thresholds(embedding_model="nomic-embed-text",
                   dedup_threshold_by_category={"pain_point": 0.85})
    assert t.validate() is t
    assert t.dedup_threshold_by_category == {"pain_point": 0.85}


def test_config_rejects_out_of_range_per_category_value():
    with pytest.raises(ConfigError) as exc:
        Thresholds(embedding_model="nomic-embed-text",
                   dedup_threshold_by_category={"pain_point": 1.5}).validate()
    assert "dedup_threshold_by_category.pain_point" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        Thresholds(embedding_model="nomic-embed-text",
                   dedup_threshold_by_category={"pain_point": "high"}).validate()
    assert "must be a number" in str(exc.value)
    # bool is an int that must not quietly stand for 1.0
    with pytest.raises(ConfigError):
        Thresholds(embedding_model="nomic-embed-text",
                   dedup_threshold_by_category={"pain_point": True}).validate()


def test_config_thresholds_yaml_declares_per_category_key():
    """The shipped file documents the shape and ships it empty: nothing
    changes until a table backs a number."""
    data = yaml.safe_load(
        (REPO_CONFIG / "thresholds.yaml").read_text(encoding="utf-8"))
    assert data.get("dedup_threshold_by_category") == {}
    text = (REPO_CONFIG / "thresholds.yaml").read_text(encoding="utf-8")
    assert "dedup_threshold_by_category:" in text
    assert "pain_point: 0.85" in text, "the commented example shows the shape"


# ---- the archivist applies the per-category bar ----------------------------

def test_archivist_refuses_per_category_duplicate_at_category_bar():
    """A neighbour at 0.81: above the question bar (0.80) → duplicate; below
    the pain_point global (0.87) → archived. Same score, same text, different
    category. The matched key must exist as a row, so the refusal is the
    REAL-duplicate branch and not the ghost fall-through."""
    db = open_db(":memory:")
    insert_nugget(db, _nugget("real-row-behind-match", category="question"))

    thr = Thresholds(embedding_model="nomic-embed-text",
                     dedup_threshold_by_category={"question": 0.80})
    questioned = _nugget("never-archived", category="question")
    arch = Archivist(db, _ScriptedNeighbours([(0.81, "real-row-behind-match")]), thr)
    assert arch.run([questioned]) == []
    assert not arch.deferred
    archie = Archivist(open_db(":memory:"),
                       _ScriptedNeighbours([(0.81, "real-row-behind-match")]),
                       thr)
    painful = _nugget("archived-under-global", category="pain_point")
    assert archie.run([painful]) == [painful], "the global bar still governs"


def test_archivist_dedup_threshold_for_picks_the_applied_threshold():
    thr = Thresholds(embedding_model="nomic-embed-text",
                     dedup_threshold_by_category={"question": 0.80})
    arch = Archivist(open_db(":memory:"),
                     _ScriptedNeighbours([], VectorStore.in_memory(FakeEmbedding())),
                     thr)
    pain = _nugget("k1", category="pain_point")
    ask = _nugget("k2", category="question")
    assert arch.dedup_threshold_for(pain) == 0.87  # global applies
    assert arch.dedup_threshold_for(ask) == 0.80   # category overrides


# ---- the near-miss band couples to the APPLIED bar -------------------------

def test_near_miss_band_couples_to_per_category_threshold():
    """At 0.87 the band is [0.85, 0.87) — today's numbers exactly. At a 0.80
    bar a fixed 0.85 floor would be unreachable (0.85 > bar means DUPLICATE),
    so the strip sits at [0.78, 0.80); at 0.94 the fixed floor would widen to
    "anything vaguely related", so again width 0.02 under the bar."""
    assert NEAR_MISS_WIDTH == 0.02
    # the default bar keeps every recorded classification
    assert classify_similarity(0.86, 0.87) == "NEAR_MISS"
    assert classify_similarity(0.85, 0.87) == "NEAR_MISS"
    assert classify_similarity(0.84, 0.87) == "DISTINCT"
    assert classify_similarity(0.87, 0.87) == "DUPLICATE"
    # a lower bar moves the strip down with it
    assert classify_similarity(0.79, 0.80) == "NEAR_MISS"
    assert classify_similarity(0.78, 0.80) == "NEAR_MISS"
    assert classify_similarity(0.77, 0.80) == "DISTINCT"
    # a higher bar moves it up, never wider
    assert classify_similarity(0.92, 0.94) == "NEAR_MISS"
    assert classify_similarity(0.91, 0.94) == "DISTINCT"


def test_archivist_tracks_near_misses_under_the_category_bar():
    """0.79 is a near miss at the question bar (0.80) and noise at the global
    (0.87): the tracking loop must use the same number the decision used."""
    db = open_db(":memory:")
    thr = Thresholds(embedding_model="nomic-embed-text",
                     dedup_threshold_by_category={"question": 0.80})
    arch = Archivist(db, _ScriptedNeighbours([(0.79, "some-neighbour")]), thr)
    kept = arch.run([_nugget("q1", category="question"),
                     _nugget("p1", category="pain_point")])
    assert len(kept) == 2
    assert arch.near_misses == [0.79], "seen once, at the question bar only"


# ---- the per-category sweep driver -----------------------------------------

@pytest.fixture()
def seeded_dir(tmp_path):
    """A copy of the seeded pairs dir plus a sub-floor file, so the committed
    corpus is never mutated by tests."""
    import shutil

    dst = tmp_path / "pairs"
    shutil.copytree(PAIRS_DIR, dst)
    return dst


def test_dedup_sweep_pairs_dir_yields_three_tables(seeded_dir, capsys,
                                                   offline_config):
    """Three seeded files -> three annotated tables, no refusals."""
    from jester.cli import cmd_dedup_sweep

    assert sorted(p.stem for p in seeded_dir.glob("*.jsonl")) == [
        "feature_request", "pain_point", "real_estate"]
    cmd_dedup_sweep(_ns(pairs=None, pairs_dir=str(seeded_dir), category=None,
                        config=str(offline_config), min_pairs=20))
    out = capsys.readouterr().out
    for category, npairs in (("pain_point", 24), ("feature_request", 20),
                             ("real_estate", 24)):
        assert f"== {category} ==" in out
        assert f"pairs: {npairs}" in out
    assert out.count(" thr |    P |    R |   F1 | tp fp fn tn") == 3
    assert "refusing to print a table this thin" not in out
    assert "embedding backend: FakeEmbedding (dim 32)" in out


def test_dedup_sweep_pairs_dir_refuses_a_sub_floor_file(seeded_dir, capsys,
                                                       offline_config):
    from jester.cli import cmd_dedup_sweep

    lines = [
        json.dumps({"a": "deploy takes all weekend",
                    "b": "deploy takes all weekend", "same": True}),
        json.dumps({"a": "deploy takes all weekend",
                    "b": "quarterly tax filing deadlines", "same": False}),
    ] * 6
    (seeded_dir / "question.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    cmd_dedup_sweep(_ns(pairs=None, pairs_dir=str(seeded_dir), category=None,
                        config=str(offline_config), min_pairs=20))
    out = capsys.readouterr().out
    assert ("dedup-sweep: question: 12 usable pair(s) (minimum 20) — "
            "refusing to print a table this thin") in out
    # the thin file printed no grid: the table count stays at three
    assert out.count(" thr |    P |    R |   F1 | tp fp fn tn") == 3


def test_dedup_sweep_pairs_dir_category_filter(seeded_dir, capsys,
                                               offline_config):
    from jester.cli import cmd_dedup_sweep

    cmd_dedup_sweep(_ns(pairs=None, pairs_dir=str(seeded_dir),
                        category="real_estate",
                        config=str(offline_config), min_pairs=20))
    out = capsys.readouterr().out
    assert "== real_estate ==" in out
    assert "pain_point" not in out and "feature_request" not in out


def test_dedup_sweep_pairs_dir_unknown_category_exits(seeded_dir, capsys,
                                                      offline_config):
    from jester.cli import cmd_dedup_sweep

    with pytest.raises(SystemExit):
        cmd_dedup_sweep(_ns(pairs=None, pairs_dir=str(seeded_dir),
                            category="bogus",
                            config=str(offline_config), min_pairs=20))
    out = capsys.readouterr().out
    assert "no pair file named bogus.jsonl" in out


def test_dedup_sweep_requires_pairs_or_pairs_dir(capsys, offline_config):
    from jester.cli import cmd_dedup_sweep

    with pytest.raises(SystemExit):
        cmd_dedup_sweep(_ns(pairs=None, pairs_dir=None, category=None,
                            config=str(offline_config), min_pairs=20))
    assert "--pairs FILE or --pairs-dir DIR" in capsys.readouterr().out


def test_sweep_over_pairs_dir_returns_flagged_results(seeded_dir):
    """The module-level shape: refused present on EVERY result, thin files
    refuse, above-floor files carry the sweep table."""
    from jester.dedup_sweep import sweep_over_pairs_dir

    results = sweep_over_pairs_dir(seeded_dir, FakeEmbedding(),
                                   min_pairs=20)
    assert sorted(results) == ["feature_request", "pain_point", "real_estate"]
    for res in results.values():
        assert res["refused"] is False
        assert res["rows"] and res["best"]


# ---- provenance: which pairs came from where -------------------------------

def test_dedup_sweep_real_estate_provenance():
    """The doc-derived six (4 true, 2 false) are named as coming from the
    validation document; the rest state they are synthetic. Honest labels:
    nothing in these files is silent."""
    from jester.dedup_sweep import load_pairs

    path = PAIRS_DIR / "real_estate.jsonl"
    rows = [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    from_doc = [r for r in rows if r["source"].startswith("docs/")]
    assert len(from_doc) == 6
    assert sum(1 for r in from_doc if r["same"]) == 4
    assert [r["a"] for r in from_doc if not r["same"]] == [
        "For sale through property24: 26 Polar Mews, 3 Thor Circle, asking "
        "R1 399 000.",
        "For sale through property24: 3512 Woodstock Quarter, asking "
        "R1 330 000.",
    ]
    synthetic = [r for r in rows if not r["source"].startswith("docs/")]
    assert len(synthetic) == 18 and len(rows) == 24
    assert all("hand-authored" in r["source"] for r in synthetic)


def test_dedup_sweep_seeded_counts_pin_the_files():
    """Guard the floors: each committed file sits at or above the sweep's
    pair floor, with the intended same/distinct split."""
    from jester.dedup_sweep import load_pairs

    for name, npairs, nsame in (("pain_point.jsonl", 24, 12),
                                ("feature_request.jsonl", 20, 10),
                                ("real_estate.jsonl", 24, 13)):
        pairs, skipped = load_pairs(PAIRS_DIR / name)
        assert len(pairs) == npairs and skipped == 0
        assert sum(1 for _a, _b, s in pairs if s) == nsame
