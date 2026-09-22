"""the 20-case fixture gate, and the machinery that grades it.

The bar: twenty labeled cases spanning relevant/irrelevant, ambiguous,
edits/deletions and failures, every one checked deterministically.

Two things are tested here, and the second matters as much as the first:

1. **The cases pass.** `jester signals check` grades all twenty and exits
   non-zero on any miss.
2. **The harness cannot be fooled.** A gate that reports 100% on a set that
   quietly lost its failure cases, or on a set of three, is not a gate. So the
   coverage rules are tested by handing the checker a deliberately broken set
   and asserting it complains.
"""
import json
from pathlib import Path

import pytest

from jester.signals import fixtures as fx
from jester.signals.filters import load_rules

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "config" / "signal_rules.yaml"


@pytest.fixture()
def rules():
    return load_rules(RULES)


# ---- the gate itself -------------------------------------------------------

def test_the_shipped_set_meets_the_gate(rules):
    res = fx.check(rules)
    assert res["failed"] == [], f"cases missed their label: {res['failed']}"
    assert res["passed"] == res["total"] == fx.REQUIRED_TOTAL
    assert res["missing_categories"] == []
    assert res["short_by"] == 0


def test_all_five_named_categories_are_exercised(rules):
    res = fx.check(rules)
    assert sorted(res["categories"]) == sorted(fx.REQUIRED_CATEGORIES)
    per = {}
    for c in res["cases"]:
        per[c["category"]] = per.get(c["category"], 0) + 1
    assert all(n >= 4 for n in per.values()), f"a category is thin: {per}"


def test_both_audiences_and_the_inherited_voice_are_covered(rules):
    """The set has to exercise what the classifier can actually decide:
    the scope owner's buyers, the content feed's practitioners, and the thread-inherited voice that
    the measured evidence made necessary."""
    got = {c["got"] for c in fx.check(rules)["cases"] if c["kind"] == "classify"}
    assert any(g.startswith("buyer") for g in got)
    assert any(g.startswith("practitioner") for g in got)
    assert any("inherited" in g for g in got)


def test_every_case_says_why_it_exists():
    """A case is a claim about behaviour. A claim with no reason attached is
    a number someone will later change to make the suite green."""
    for path in sorted(fx.CASES_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("why"), f"{path.name}: the file has no `why`"
        for case in doc["cases"]:
            assert case["id"] and (case.get("why") or doc["why"]), \
                f"{case.get('id')}: no reason recorded"


# ---- the harness cannot be fooled -----------------------------------------

def _write(tmp_path, name, category, cases):
    (tmp_path / name).write_text(json.dumps(
        {"category": category, "why": ["test"], "cases": cases}), encoding="utf-8")


def test_a_missing_category_fails_the_gate(tmp_path, rules):
    _write(tmp_path, "a.json", "relevant", [{
        "id": "x", "expect": {"audience": "none"},
        "record": {"source_id": "t3_x", "text": "nothing here"}}])
    res = fx.check(rules, tmp_path)
    assert res["failed"] == []          # the one case passes …
    assert res["missing_categories"]    # … and the set is still not a gate
    assert res["short_by"] == fx.REQUIRED_TOTAL - 1


def test_an_empty_set_is_an_error_not_a_pass(tmp_path, rules):
    with pytest.raises(fx.FixtureError, match="no cases"):
        fx.check(rules, tmp_path)


def test_a_missing_directory_is_an_error(rules, tmp_path):
    with pytest.raises(fx.FixtureError, match="no fixture directory"):
        fx.check(rules, tmp_path / "nope")


def test_a_malformed_case_file_names_itself(tmp_path, rules):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(fx.FixtureError, match="bad.json"):
        fx.check(rules, tmp_path)


def test_a_case_without_a_record_or_replay_is_rejected(tmp_path, rules):
    _write(tmp_path, "a.json", "relevant", [{"id": "x", "expect": {"audience": "none"}}])
    with pytest.raises(fx.FixtureError, match="needs a `record` or a `replay`"):
        fx.check(rules, tmp_path)


def test_the_checker_reports_a_real_miss(tmp_path, rules):
    """A case whose label is wrong must fail, or the gate proves nothing."""
    _write(tmp_path, "a.json", "relevant", [{
        "id": "wrong-label", "expect": {"audience": "buyer"},
        "record": {"source_id": "t3_x", "text": "Finally finished cable management on my homelab."}}])
    res = fx.check(rules, tmp_path)
    assert res["failed"] == ["wrong-label"]
    assert "audience none != buyer" in res["cases"][0]["misses"][0]


# ---- replay cases are checked against a real table -------------------------

def test_a_replay_case_that_lies_about_the_table_fails(tmp_path, rules):
    _write(tmp_path, "r.json", "edits", [{
        "id": "bad-replay",
        "replay": [{"seen_at": "2026-09-20T00:00:00+00:00", "text": "one"},
                   {"seen_at": "2026-09-21T00:00:00+00:00", "text": "two"}],
        "expect": {"rows": 2, "revisions": 0}}])
    res = fx.check(rules, tmp_path)
    assert res["failed"] == ["bad-replay"]
    misses = " ".join(res["cases"][0]["misses"])
    assert "rows 1 != 2" in misses and "revisions 1 != 0" in misses


def test_each_replay_case_gets_its_own_database(rules):
    """Replay cases assert on total row count, so one leaking into another
    would make the whole category pass or fail together."""
    res = fx.check(rules)
    replays = [c for c in res["cases"] if c["kind"] == "replay"]
    assert len(replays) >= 4
    assert all(c["pass"] for c in replays)
    assert all("1 row" in c["got"] for c in replays)
