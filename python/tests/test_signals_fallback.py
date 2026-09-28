"""The jobs scraper falls back to another collector when it adds nothing new.

The hiring collector reads one monthly thread, so between the 1st of one month
and the next every run re-reads the same adverts and stores none. These pin
down when the second collector runs, and that the ledger says why.
"""
import sqlite3

import pytest

from jester.signals import run as sigrun


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sigrun.ensure_run_table(conn)
    return conn


@pytest.fixture()
def fake_collect(monkeypatch):
    """Stand-in for `collect`: returns a canned row per source and records calls."""
    calls, results = [], {}

    def collect(db, *, source_name="hackernews", **kw):
        calls.append({"source_name": source_name, **kw})
        row = {"run_id": f"r-{source_name}", "source": source_name, "mode": "live",
               "kind": kw.get("kind", "scheduled"), "started_utc": "t", "finished_utc": "t",
               "since": kw.get("since", "7d"), "queries": "[]", "collected": 5,
               "new": 0, "seen_again": 5, "edited": 0, "relevant": 0, "buyer": 0,
               "practitioner": 0, "errors": 0, "status": "ok", "note": "",
               **results.get(source_name, {})}
        sigrun._save_run(db, row)
        return row

    monkeypatch.setattr(sigrun, "collect", collect)
    collect.calls, collect.results = calls, results
    return collect


def _notes(db):
    return {r["source"]: r["note"] for r in db.execute("SELECT source, note FROM signal_run")}


def test_nothing_new_falls_back_and_says_so(db, fake_collect):
    fake_collect.results["hackernews"] = {"new": 41}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="hackernews", kind="manual")
    assert [r["source"] for r in rows] == ["hackernews-hiring", "hackernews"]
    assert rows[1]["new"] == 41
    notes = _notes(db)
    assert notes["hackernews-hiring"] == "nothing new; fell back to hackernews"
    assert notes["hackernews"] == "fallback: hackernews-hiring had nothing new"
    # The fallback is still the same person's run: it keeps their origin.
    assert fake_collect.calls[1]["kind"] == "manual"


def test_something_new_does_not_fall_back(db, fake_collect):
    fake_collect.results["hackernews-hiring"] = {"new": 3}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="hackernews")
    assert len(rows) == 1 and len(fake_collect.calls) == 1
    assert _notes(db)["hackernews-hiring"] == ""


def test_a_failed_run_is_not_nothing_new(db, fake_collect):
    """Could not look is not the same as found nothing; running something
    else would bury the failure."""
    fake_collect.results["hackernews-hiring"] = {"status": "failed", "errors": 1}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="hackernews")
    assert len(rows) == 1 and rows[0]["status"] == "failed"


def test_no_fallback_named_means_one_run(db, fake_collect):
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring")
    assert len(rows) == 1


def test_fallbacks_share_the_first_runs_rules_but_not_its_queries(db, fake_collect):
    hiring_rules = object()
    sigrun.collect_or_fall_back(db, source_name="hackernews-hiring", rules=hiring_rules,
                                queries=["2"], fallback="himalayas")
    second = fake_collect.calls[1]
    # Same kind of record, same question: the job boards are scored with the
    # hiring rules the first run was given.
    assert second["rules"] is hiring_rules
    # The hiring collector's "2" means two months; to a job board it would be
    # a search keyword. Each board brings its own.
    assert second["queries"] == ["developer", "engineer", "automation", "data"]


def test_the_chain_stops_at_the_first_board_that_adds_something(db, fake_collect):
    fake_collect.results["himalayas"] = {"new": 12}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="himalayas,remotive,jobicy")
    assert [r["source"] for r in rows] == ["hackernews-hiring", "himalayas"]


def test_the_chain_walks_on_while_nothing_is_new(db, fake_collect):
    fake_collect.results["jobicy"] = {"new": 2}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback=["himalayas", "remotive", "jobicy"])
    assert [r["source"] for r in rows] == ["hackernews-hiring", "himalayas",
                                          "remotive", "jobicy"]
    assert _notes(db)["remotive"].startswith("fallback: hackernews-hiring")


def test_a_failed_board_is_recorded_and_the_walk_goes_on(db, fake_collect):
    fake_collect.results["himalayas"] = {"status": "failed", "errors": 1}
    fake_collect.results["remotive"] = {"new": 4}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="himalayas,remotive,jobicy")
    assert [r["source"] for r in rows] == ["hackernews-hiring", "himalayas", "remotive"]
    assert rows[1]["status"] == "failed"
