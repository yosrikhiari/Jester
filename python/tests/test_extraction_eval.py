"""B2 — extraction quality (S-06): a deterministic sample, κ, P/R/F1, fabrication.

The harness is deliberately plain: labels are JSONL, the sample is reproducible
from a seed, and every aggregate carries its denominator. No model call and no
network — the labels and the fixture comments are the input.
"""
import json
from collections import Counter

import pytest

from jester.agents.extractor import extract_batch
from jester.extraction_eval import (
    DEFAULT_SEED,
    cohens_kappa,
    evaluate,
    gate_failures,
    format_report,
    stratified_sample,
)
from jester.llm import FakeLLM


def _row(key, platform="reddit", category="pain_point", chars=100):
    return {"unique_key": key, "platform": platform, "category": category,
            "text": "x" * chars}


def _labels(**overrides):
    base = {
        "unique_key": "reddit:a:1",
        "platform": "reddit",
        "stratum": "reddit|pain_point|short",
        "rater": "owner",
        "keep": True,
        "gold": ["the backup job fails silently"],
        "predicted": [{"insight": "backups fail silently", "verdict": "supported"}],
    }
    base.update(overrides)
    return base


# ---- the sample: same seed, same set; strata counts match the spec -------------

def test_sample_is_deterministic_and_stratified():
    rows = ([_row(f"reddit:short:{i}") for i in range(30)]
            + [_row(f"reddit:long:{i}", chars=2000) for i in range(30)]
            + [_row(f"hn:short:{i}", platform="hackernews") for i in range(30)])
    sizes = {"reddit|pain_point|short": 10,
             "reddit|pain_point|long": 5,
             "hackernews|pain_point|short": 8}

    first = stratified_sample(rows, sizes, seed=7)
    again = stratified_sample(rows, sizes, seed=7)
    other = stratified_sample(rows, sizes, seed=8)

    assert [r["unique_key"] for r in first] == [r["unique_key"] for r in again]
    assert Counter(r["stratum"] for r in first) == sizes
    assert [r["unique_key"] for r in first] != [r["unique_key"] for r in other]
    assert len({r["unique_key"] for r in first}) == 10 + 5 + 8


# ---- κ: perfect agreement and chance agreement, hand-computed ------------------

def test_kappa_perfect_and_chance_agreement():
    perfect = [(1, 1)] * 10 + [(0, 0)] * 10
    chance = ([(1, 1)] * 5 + [(1, 0)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5)

    assert cohens_kappa(perfect) == pytest.approx(1.0)
    # po = 0.5, pe = 0.5  ->  (0.5 - 0.5) / (1 - 0.5) = 0
    assert cohens_kappa(chance) == pytest.approx(0.0)


# ---- fabrication: an assertion the comment does not support is counted ---------

def test_fabrication_counts_unmatched_assertions():
    report = evaluate([
        _labels(predicted=[
            {"insight": "backups fail silently", "verdict": "supported"},
            {"insight": "restores were never tested", "verdict": "fabricated"},
        ]),
        _labels(unique_key="reddit:a:2",
                predicted=[{"insight": "restore drill failed", "verdict": "supported"}]),
    ])

    assert report["predicted"] == 3
    assert report["supported"] == 2
    assert report["fabricated"] == 1
    assert report["fabrication_rate"] == pytest.approx(1 / 3)
    assert report["precision"] == pytest.approx(2 / 3)
    assert report["recall"] == pytest.approx(2 / 2)
    assert report["f1"] == pytest.approx(2 * (2 / 3) * 1 / ((2 / 3) + 1))


# ---- unchecked labels stay visible, never imputed ------------------------------

def test_report_prints_unchecked_rates():
    report = evaluate([
        _labels(predicted=[
            {"insight": "supported", "verdict": "supported"},
            {"insight": "not reviewed", "verdict": "unchecked"},
            {"insight": "made up", "verdict": "fabricated"},
        ]),
    ])
    assert report["unchecked"] == 1
    assert report["unchecked_rate"] == pytest.approx(1 / 3)

    text = format_report(report, seed=DEFAULT_SEED, labels_path="labels.jsonl")
    assert "unchecked" in text
    assert "seed=20261031" in text
    assert "fabrication" in text.lower()
    assert "nuggets/comment" in text


# ---- the gate: a low-F1 label set fails ----------------------------------------

def test_ci_gate_fails_below_floor():
    report = evaluate([
        _labels(predicted=[
            {"insight": "made up", "verdict": "fabricated"},
            {"insight": "also made up", "verdict": "fabricated"},
        ], gold=["one", "two", "three"]),
    ])
    failures = gate_failures(report, min_f1=0.70, max_fabrication=0.05,
                             max_nuggets_per_comment=1.2)
    assert any("F1" in f for f in failures)
    assert any("fabrication" in f for f in failures)

    healthy = evaluate([_labels()])
    assert gate_failures(healthy, min_f1=0.70, max_fabrication=0.05,
                         max_nuggets_per_comment=1.2) == []


# ---- the batch cap holds in the pipeline's own calls ---------------------------

def test_nugget_cap_invariant():
    class Spy(FakeLLM):
        def __init__(self):
            self.batches = []

        def extract_batch(self, bodies):
            self.batches.append(len(bodies))
            return [super(Spy, self).extract(b) for b in bodies]

    llm = Spy()
    comments = [{"body": f"comment {i}", "fingerprint": f"c{i}"} for i in range(12)]
    extract_batch(comments, llm, platform="reddit", thread_id="t",
                  source_url="https://reddit.test/t", run_id="r", batch_size=5)

    assert llm.batches == [5, 5, 2]
    assert max(llm.batches) <= 5, "S-18 caps the extractor batch at five"


# ---- the CLI refuses to invent a result when no labels exist -------------------

def test_eval_extract_prints_how_to_label_when_the_file_is_missing(tmp_path, capsys):
    from argparse import Namespace
    from jester.cli_review import cmd_eval_extract

    cmd_eval_extract(Namespace(labels=str(tmp_path / "missing.jsonl"), seed=7,
                               floor=0.70, fabrication_ceiling=0.05,
                               max_nuggets_per_comment=1.2))
    out = capsys.readouterr().out
    assert "no labels" in out.lower()
    assert "jsonl" in out.lower()


def test_eval_extract_exits_nonzero_when_the_gate_fails(tmp_path):
    from argparse import Namespace
    from jester.cli_review import cmd_eval_extract

    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps(_labels(predicted=[
        {"insight": "made up", "verdict": "fabricated"}], gold=["a", "b", "c"])) + "\n",
        encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_extract(Namespace(labels=str(path), seed=7, floor=0.70,
                                   fabrication_ceiling=0.05,
                                   max_nuggets_per_comment=1.2))
    assert exc.value.code == 1
