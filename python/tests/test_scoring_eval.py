"""Task 8 — scoring agreement (V3-13): human and model scores per idea.

The owner's real scores do not exist yet, so nothing here invents one: every
test writes its own JSONL into `tmp_path`, and an absent file is a pause with
instructions, not a failure.
"""
import json
from argparse import Namespace

import pytest

from jester.cli_review import cmd_eval_agreement
from jester.scoring_eval import evaluate_agreement, format_report, load_rows

AXES = ("overall", "demand", "feasibility", "competition")
HUMAN = {"overall": 7.0, "demand": 6.0, "feasibility": 8.0, "competition": 5.0}
MODEL = {"overall": 6.5, "demand": 6.0, "feasibility": 7.0, "competition": 5.5}


def _row(idea_id, human=None, model=None):
    return {"idea_id": idea_id, "human": dict(human or HUMAN),
            "model": dict(model or MODEL)}


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _ns(path, minimum=30):
    return Namespace(labels=str(path), min=minimum)


# ---- the aggregate: MAE per axis, each with its denominator -------------------

def test_mae_per_axis_uses_only_the_pairs_that_exist():
    report = evaluate_agreement([_row(1), _row(2)])

    assert report["n_compared"] == 2 and report["n_skipped"] == 0
    for axis in AXES:
        assert report["axes"][axis]["n"] == 2
    assert report["axes"]["overall"]["mae"] == pytest.approx(0.5)
    assert report["axes"]["feasibility"]["mae"] == pytest.approx(1.0)
    assert report["axes"]["demand"]["mae"] == pytest.approx(0.0)


def test_a_null_axis_value_is_skipped_not_imputed():
    rows = [_row(1), _row(2), _row(3)]
    rows[0]["model"]["competition"] = 6.0
    rows[1]["model"]["competition"] = 6.0
    rows[2]["model"]["competition"] = None

    report = evaluate_agreement(rows)

    assert report["axes"]["competition"]["n"] == 2
    assert report["axes"]["competition"]["mae"] == pytest.approx(1.0)
    assert report["axes"]["overall"]["n"] == 3
    assert report["axes"]["overall"]["mae"] == pytest.approx(0.5)
    assert report["n_compared"] == 3, "the row still compares on other axes"


def test_malformed_rows_are_skipped_and_unshared_axes_do_not_compare():
    rows = [_row(1), "not an object", {"idea_id": 2}, None,
            {"idea_id": 3, "human": {"overall": 7.0}, "model": {"demand": 6.0}}]

    report = evaluate_agreement(rows)

    assert report["n_skipped"] == 3
    assert report["n_compared"] == 1
    assert report["axes"]["overall"]["n"] == 1, "no shared axis on that row, " \
        "so it adds nothing to any denominator"


def test_the_report_prints_each_axis_with_its_denominator():
    text = format_report(evaluate_agreement([_row(1)]), labels_path="a.jsonl")

    assert "a.jsonl" in text
    for axis in AXES:
        assert f"{axis}: MAE" in text
    assert "(1 pairs)" in text


# ---- every read row lands in exactly one bucket -------------------------------

def test_every_read_row_is_compared_skipped_or_uncompared(tmp_path):
    # Five rows read: one comparable, one with no shared axis, one whose only
    # shared value is null on both sides, one with no mapping at all, and one
    # broken line. Before the fix the middle two were in no bucket, so the
    # printed totals did not add up (5 read / 1 compared / 2 skipped).
    path = tmp_path / "agreement.jsonl"
    path.write_text("\n".join([
        json.dumps(_row(1)),                                    # comparable
        json.dumps({"idea_id": 2, "human": {"overall": 7.0},
                    "model": {"demand": 6.0}}),                 # no shared axis
        json.dumps({"idea_id": 3, "human": {"overall": None},
                    "model": {"overall": None}}),               # null only
        json.dumps({"idea_id": 4}),                             # no mapping
        '{"idea_id": 5, "human": {"overall": 7.0',              # broken JSON
    ]) + "\n", encoding="utf-8")

    report = evaluate_agreement(load_rows(path))

    assert (report["n_rows"], report["n_compared"], report["n_skipped"],
            report["n_uncompared"]) == (5, 1, 2, 2)
    assert report["n_rows"] == (report["n_compared"] + report["n_skipped"]
                                + report["n_uncompared"]), \
        "the printed totals must reconcile"
    text = format_report(report, labels_path=path)
    assert "5 read, 1 compared, 2 skipped, 2 uncompared" in text


def test_a_file_of_string_scores_is_counted_as_unscored_not_compared():
    # A hand-written file of "7" strings: 30 rows read, nothing compared, and
    # the report must say why rather than stay silent. Strings are not coerced.
    rows = [{"idea_id": i, "human": {axis: "7" for axis in AXES},
             "model": {axis: "6" for axis in AXES}} for i in range(30)]

    report = evaluate_agreement(rows)

    assert (report["n_rows"], report["n_compared"], report["n_skipped"],
            report["n_uncompared"]) == (30, 0, 0, 30)
    assert report["n_unscored"] == 30 * len(AXES)
    for axis in AXES:
        assert report["axes"][axis]["mae"] is None
        assert report["axes"][axis]["n"] == 0
    text = format_report(report, labels_path="a.jsonl")
    assert "unscored = 120 shared axis pair(s)" in text
    assert "MAE = n/a (0 pairs)" in text


# ---- a broken line costs one row, not the file --------------------------------

def test_one_valid_line_and_one_broken_line_through_the_cli(tmp_path, capsys):
    path = tmp_path / "agreement.jsonl"
    path.write_text(json.dumps(_row(1)) + "\n" + '{"idea_id": 2, "human": {\n',
                    encoding="utf-8")

    res = cmd_eval_agreement(_ns(path, minimum=1))

    out = capsys.readouterr().out
    assert (res["n_rows"], res["n_compared"], res["n_skipped"],
            res["n_uncompared"]) == (2, 1, 1, 0)
    assert res["axes"]["overall"]["mae"] == pytest.approx(0.5)
    assert "2 read, 1 compared, 1 skipped, 0 uncompared" in out
    assert "measurable: 1 compared of 1 required" in out


def test_the_default_labels_path_is_used_when_no_flag_is_given(
        tmp_path, capsys, monkeypatch):
    path = _write(tmp_path / "agreement.jsonl", [_row(1), _row(2)])
    monkeypatch.setattr("jester.cli_review.DEFAULT_AGREEMENT_PATH", path)

    res = cmd_eval_agreement(Namespace(min=2))     # no `labels` attribute

    out = capsys.readouterr().out
    assert str(path) in out
    assert res["n_compared"] == 2 and res["measurable"] is True
    assert "measurable: 2 compared of 2 required" in out


# ---- the CLI: measurable, not yet measurable, and nothing invented -----------

def test_thirty_identical_rows_are_zero_mae_and_exit_zero(tmp_path, capsys):
    path = _write(tmp_path / "agreement.jsonl",
                  [_row(i, human=HUMAN, model=HUMAN) for i in range(30)])

    res = cmd_eval_agreement(_ns(path))

    out = capsys.readouterr().out
    assert res["ok"] is True and res["measurable"] is True
    assert res["measured"] is True, "scored rows are a measurement"
    assert res["n_compared"] == 30 and res["n_skipped"] == 0
    for axis in AXES:
        assert res["axes"][axis]["mae"] == pytest.approx(0.0)
        assert res["axes"][axis]["n"] == 30
    assert "not yet measurable" not in out
    assert "30 compared of 30 required" in out


def test_twenty_nine_rows_print_the_report_and_say_not_yet_measurable(
        tmp_path, capsys):
    path = _write(tmp_path / "agreement.jsonl", [_row(i) for i in range(29)])

    res = cmd_eval_agreement(_ns(path))

    out = capsys.readouterr().out
    assert res["ok"] is True and res["measurable"] is False
    assert "overall: MAE" in out
    assert "not yet measurable: 29 compared of 30 required" in out


def test_an_absent_file_prints_the_example_and_exits_zero(tmp_path, capsys):
    missing = tmp_path / "never" / "agreement.jsonl"

    res = cmd_eval_agreement(_ns(missing))

    out = capsys.readouterr().out
    assert res["ok"] is True and res["labels"] == 0 and res["measured"] is False
    assert "NOT MEASURED" in out and "scorer agreement" in out
    assert str(missing) in out and "(0 rows)" in out
    assert 'Exit 0 is "not taken", not "passed".' in out
    assert '"idea_id"' in out and '"human"' in out and '"model"' in out
    assert '"overall"' in out and '"competition"' in out
    assert "::warning::" not in out, "no GitHub annotation syntax from the CLI"
    assert not missing.exists(), "the command writes no file of its own"


def test_an_empty_file_is_the_same_pause_as_an_absent_one(tmp_path, capsys):
    path = _write(tmp_path / "agreement.jsonl", [])

    res = cmd_eval_agreement(_ns(path))

    out = capsys.readouterr().out
    assert res["ok"] is True and res["labels"] == 0 and res["measured"] is False
    assert "NOT MEASURED" in out and "the file is empty" in out
    assert '"idea_id"' in out


def test_a_single_null_axis_keeps_its_own_denominator_in_the_cli(
        tmp_path, capsys):
    rows = [_row(i) for i in range(3)]
    rows[2]["model"]["competition"] = "n/a"     # non-numeric: skipped, not zero

    res = cmd_eval_agreement(_ns(_write(tmp_path / "a.jsonl", rows), minimum=3))

    out = capsys.readouterr().out
    assert res["axes"]["competition"]["n"] == 2
    assert res["axes"]["overall"]["n"] == 3
    assert res["n_compared"] == 3 and res["measurable"] is True
    assert "competition: MAE" in out and "(2 pairs)" in out
