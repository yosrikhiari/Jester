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
    proportional_sizes,
    sample_from_db,
    strata_counts,
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


def test_the_draw_does_not_depend_on_the_order_rows_arrive_in():
    rows = ([_row(f"reddit:short:{i}") for i in range(30)]
            + [_row(f"reddit:long:{i}", chars=2000) for i in range(30)])
    sizes = {"reddit|pain_point|short": 10, "reddit|pain_point|long": 5}
    # A fixed permutation, so the test itself is deterministic.
    reordered = rows[::2][::-1] + rows[1::2]

    in_order = stratified_sample(rows, sizes, seed=7)
    shuffled = stratified_sample(reordered, sizes, seed=7)

    assert ([r["unique_key"] for r in in_order]
            == [r["unique_key"] for r in shuffled])


def test_one_added_row_displaces_at_most_one_drawn_row():
    # The documented consequence of ranking by (seed, key): the pool growing
    # cannot reshuffle a draw that was already made.
    rows = [_row(f"reddit:short:{i}") for i in range(40)]
    sizes = {"reddit|pain_point|short": 10}

    before = {r["unique_key"] for r in stratified_sample(rows, sizes, seed=7)}
    after = {r["unique_key"] for r in stratified_sample(
        rows + [_row("reddit:short:new")], sizes, seed=7)}

    assert len(before - after) <= 1


# ---- the sample design: 150 rows, allocated in proportion ---------------------

def test_proportional_sizes_sum_to_the_total_and_never_exceed_a_pool():
    counts = {"a": 100, "b": 50, "c": 2}

    sizes = proportional_sizes(counts, 20)

    assert sum(sizes.values()) == 20
    assert set(sizes) == {"a", "b", "c"}
    assert all(sizes[k] <= counts[k] for k in sizes)
    assert sizes["c"] == 1, "a small stratum still contributes a row"


def test_proportional_sizes_track_the_pool():
    assert proportional_sizes({"a": 90, "b": 10}, 10) == {"a": 9, "b": 1}


def test_proportional_sizes_with_fewer_rows_than_strata_takes_the_largest():
    sizes = proportional_sizes({"a": 5, "b": 3, "c": 2}, 2)

    assert sum(sizes.values()) == 2
    assert set(sizes) == {"a", "b"}
    assert sizes == {"a": 1, "b": 1}


def test_proportional_sizes_is_empty_for_an_empty_pool():
    assert proportional_sizes({}, 150) == {}
    assert proportional_sizes({"a": 0}, 150) == {}


# ---- the archive draw: same ids as the pure draw, text fetched only for them --

def test_sample_from_db_matches_the_pure_draw_and_carries_text():
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(":memory:")
    rows = ([_row(f"reddit:short:{i}") for i in range(30)]
            + [_row(f"reddit:long:{i}", chars=2000) for i in range(30)]
            + [_row(f"hn:short:{i}", platform="hackernews") for i in range(30)])
    texts = {}
    for row in rows:
        texts[row["unique_key"]] = row["text"]
        insert_nugget(db, Nugget(
            unique_key=row["unique_key"], platform=row["platform"],
            category=row["category"], extracted_insight="x",
            raw_text=row["text"], needs_reembed=False))
    sizes = {"reddit|pain_point|short": 10,
             "reddit|pain_point|long": 5,
             "hackernews|pain_point|short": 8}

    streamed = sample_from_db(db, sizes, seed=7)
    pure = stratified_sample(rows, sizes, seed=7)

    assert [r["unique_key"] for r in streamed] == [r["unique_key"] for r in pure]
    assert all(r["text"] == texts[r["unique_key"]] for r in streamed)
    assert Counter(r["stratum"] for r in streamed) == sizes


def test_eval_sample_writes_the_jsonl_and_design_and_leaves_the_archive_alone(
        tmp_path):
    from jester.cli import cmd_eval_sample
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    for i in range(10):
        insert_nugget(db, Nugget(
            unique_key=f"reddit:a:{i}", platform="reddit", category="pain_point",
            extracted_insight="x", raw_text="z" * (10 + i), needs_reembed=False))
    db.close()
    before = db_path.read_bytes()
    out = tmp_path / "sample.jsonl"

    class NS:
        pass

    ns = NS()
    ns.db, ns.out, ns.size, ns.seed = str(db_path), str(out), 5, 7
    res = cmd_eval_sample(ns)

    assert res["ok"] is True and res["drawn"] == 5
    assert db_path.read_bytes() == before, "the draw must not reshape the archive"
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5 and all(r["text"] for r in rows)
    design = json.loads(out.with_suffix(".design.json").read_text(encoding="utf-8"))
    assert design["drawn"] == 5 and design["seed"] == 7
    assert design["archive"] == "j.db", "the design file is committed; no local paths"
    assert design["allocated"] == {"reddit|pain_point|short": 5}


def test_eval_sample_refuses_a_missing_archive_and_creates_nothing(tmp_path, capsys):
    """`open_db` makes the file and its parent on connect. A draw pointed at a
    typo would otherwise leave a fresh empty archive behind and then die on
    the first SELECT, which reads as 'the archive is broken'."""
    from jester.cli import cmd_eval_sample

    missing = tmp_path / "never" / "there.db"

    class NS:
        pass

    ns = NS()
    ns.db, ns.out, ns.size, ns.seed = str(missing), str(tmp_path / "s.jsonl"), 5, 7
    with pytest.raises(SystemExit) as exc:
        cmd_eval_sample(ns)

    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().out
    assert not missing.exists() and not missing.parent.exists()
    assert not (tmp_path / "s.jsonl").exists()


def test_eval_sample_exits_nonzero_when_there_is_nothing_to_draw(
        tmp_path, capsys):
    """The sample gates the retention sweep. A zero exit with no file would
    let the sweep proceed believing the substrate had been preserved."""
    from jester.cli import cmd_eval_sample
    from jester.store import open_db

    db_path = tmp_path / "empty.db"
    open_db(str(db_path)).close()

    class NS:
        pass

    ns = NS()
    ns.db, ns.out, ns.size, ns.seed = str(db_path), str(tmp_path / "s.jsonl"), 5, 7
    with pytest.raises(SystemExit) as exc:
        cmd_eval_sample(ns)

    assert exc.value.code == 1
    assert "nothing to draw" in capsys.readouterr().out
    assert not (tmp_path / "s.jsonl").exists()


def test_padding_does_not_move_a_comment_between_strata():
    """The archive draw buckets on SQL `length(raw_text)`; a pure draw used to
    bucket on the stripped text, so padding could disagree across the two
    paths that are documented to produce the same ids."""
    from jester.extraction_eval import stratum_of

    padded = "   " + "z" * 278          # 281 raw, 278 stripped: short/medium
    assert stratum_of({"raw_text": padded}).endswith("|medium")
    assert stratum_of({"chars": 281}).endswith("|medium")
    assert stratum_of({"raw_text": "z" * 278}).endswith("|short")


def test_strata_counts_reads_the_same_strata_the_draw_uses():
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(":memory:")
    for key, platform, chars in (
            ("a", "reddit", 10), ("b", "reddit", 500), ("c", "hackernews", 2000)):
        insert_nugget(db, Nugget(
            unique_key=key, platform=platform, category="pain_point",
            extracted_insight="x", raw_text="z" * chars, needs_reembed=False))

    assert strata_counts(db) == {
        "reddit|pain_point|short": 1,
        "reddit|pain_point|medium": 1,
        "hackernews|pain_point|long": 1,
    }


# ---- κ: perfect agreement and chance agreement, hand-computed ------------------

def test_kappa_perfect_and_chance_agreement():
    perfect = [(1, 1)] * 10 + [(0, 0)] * 10
    chance = ([(1, 1)] * 5 + [(1, 0)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5)

    assert cohens_kappa(perfect) == pytest.approx(1.0)
    # po = 0.5, pe = 0.5  ->  (0.5 - 0.5) / (1 - 0.5) = 0
    assert cohens_kappa(chance) == pytest.approx(0.0)


def _doubled(pairs):
    """Two rater rows per comment, one per verdict pair, so κ is measurable.

    The first row of each comment carries the gold and the prediction, which
    `evaluate` counts once per `unique_key`: the repeat is only the second
    opinion on `keep`.
    """
    rows = []
    for i, (a, b) in enumerate(pairs):
        key = f"reddit:b:{i}"
        for rater, keep in (("owner", bool(a)), ("second", bool(b))):
            rows.append(_labels(unique_key=key, rater=rater, keep=keep))
    return rows


# ---- κ is a published floor, and only when it is measurable -------------------

def test_perfect_double_labelling_gives_kappa_one_and_passes_every_floor():
    report = evaluate(_doubled([(1, 1)] * 10 + [(0, 0)] * 10))

    assert report["double_labelled"] == 20
    assert report["kappa"] == pytest.approx(1.0)
    assert gate_failures(report) == []


def test_a_zero_kappa_label_set_fails_the_kappa_floor_and_nothing_else():
    # po = 0.5, pe = 0.5 -> κ = 0 while every other aggregate is perfect;
    # the failure must name κ alone, with its denominator.
    report = evaluate(
        _doubled([(1, 1)] * 5 + [(1, 0)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5))
    assert report["kappa"] == pytest.approx(0.0)

    assert gate_failures(report) == [
        "kappa 0.000 is below the 0.60 floor (20 double-labelled)"]


def test_single_labelled_labels_raise_no_kappa_failure():
    # No doubled rows means κ is unavailable, not zero: the floor cannot apply.
    report = evaluate([_labels()])

    assert report["kappa"] is None and report["double_labelled"] == 0
    assert not any("kappa" in f for f in gate_failures(report))


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


def test_eval_extract_exits_nonzero_on_kappa_alone_and_prints_its_denominator(
        tmp_path, capsys):
    from argparse import Namespace
    from jester.cli_review import cmd_eval_extract

    path = tmp_path / "labels.jsonl"
    rows = _doubled([(1, 1)] * 5 + [(1, 0)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    # No `min_kappa` on this Namespace: the command's default (0.6) applies.
    with pytest.raises(SystemExit) as exc:
        cmd_eval_extract(Namespace(labels=str(path), seed=7, floor=0.70,
                                   fabrication_ceiling=0.05,
                                   max_nuggets_per_comment=1.2))

    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "kappa = 0.000 (20 double-labelled)" in out
    assert "GATE: kappa 0.000 is below the 0.60 floor (20 double-labelled)" in out
    assert "GATE: F1" not in out and "GATE: fabrication" not in out


def test_eval_extract_honours_a_lowered_kappa_floor(tmp_path, capsys):
    from argparse import Namespace
    from jester.cli_review import cmd_eval_extract

    path = tmp_path / "labels.jsonl"
    rows = _doubled([(1, 1)] * 5 + [(1, 0)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    res = cmd_eval_extract(Namespace(labels=str(path), seed=7, floor=0.70,
                                     fabrication_ceiling=0.05,
                                     max_nuggets_per_comment=1.2,
                                     min_kappa=-1.0))

    assert res["ok"] is True
    assert "GATE" not in capsys.readouterr().out
