"""Task 8 — scoring agreement (V3-13): human and model scores per idea.

The owner's real scores do not exist yet, so nothing here invents one: every
test writes its own JSONL into `tmp_path`, and an absent file is a pause with
instructions, not a failure.
"""
import json
from argparse import Namespace

import pytest

from jester.cli_review import cmd_eval_agreement
from jester.scoring_eval import evaluate_agreement, format_report

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


# ---- the CLI: measurable, not yet measurable, and nothing invented -----------

def test_thirty_identical_rows_are_zero_mae_and_exit_zero(tmp_path, capsys):
    path = _write(tmp_path / "agreement.jsonl",
                  [_row(i, human=HUMAN, model=HUMAN) for i in range(30)])

    res = cmd_eval_agreement(_ns(path))

    out = capsys.readouterr().out
    assert res["ok"] is True and res["measurable"] is True
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
    assert res == {"ok": True, "labels": 0}
    assert f"no agreement scores yet at {missing}" in out
    assert '"idea_id"' in out and '"human"' in out and '"model"' in out
    assert '"overall"' in out and '"competition"' in out
    assert not missing.exists(), "the command writes no file of its own"


def test_an_empty_file_is_the_same_pause_as_an_absent_one(tmp_path, capsys):
    path = _write(tmp_path / "agreement.jsonl", [])

    res = cmd_eval_agreement(_ns(path))

    out = capsys.readouterr().out
    assert res == {"ok": True, "labels": 0}
    assert "no agreement scores yet at" in out and "(the file is empty)" in out
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
