"""Ingestion boundary tests (§37.14): fingerprint skip-list + Fetcher seam."""
import json
from pathlib import Path

import pytest

from jester.config import load_config
from jester.store import (
    enqueue_batch,
    get_runs,
    open_db,
    pending_batches,
    seen_fingerprints,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: skip-list primitives ------------------------------------------------

def test_enqueue_new_only_queues_unseen_and_skips_seen(tmp_path):
    from jester.store import enqueue_new_only, mark_comments_ingested

    db = open_db(str(tmp_path / "j.db"))
    first = enqueue_new_only(db, "reddit", "src", "tid", [
        {"body": "a", "fingerprint": "f1"},
        {"body": "b", "fingerprint": "f2"},
    ])
    assert first == 2
    mark_comments_ingested(db, "tid", ["f1", "f2"])

    # M1.2: re-fetch of an already-ingested thread produces zero new batches.
    queued_before = len(pending_batches(db))
    again = enqueue_new_only(db, "reddit", "src", "tid", [
        {"body": "a", "fingerprint": "f1"},
        {"body": "b", "fingerprint": "f2"},
    ])
    assert again == 0
    assert len(pending_batches(db)) == queued_before


def test_enqueue_new_only_admits_mixed_thread(tmp_path):
    from jester.store import enqueue_new_only, mark_comments_ingested

    db = open_db(str(tmp_path / "j.db"))
    mark_comments_ingested(db, "tid", ["seen1"])

    queued = enqueue_new_only(db, "reddit", "src", "tid", [
        {"body": "old", "fingerprint": "seen1"},
        {"body": "new", "fingerprint": "fresh9"},
    ])
    assert queued == 1
    prints = [json.loads(r["comments"])[0]["fingerprint"] for r in pending_batches(db)]
    assert prints == ["fresh9"]


def test_marks_persist_across_reopen(tmp_path):
    from jester.store import mark_comments_ingested

    p = tmp_path / "j.db"
    db = open_db(str(p))
    mark_comments_ingested(db, "t1", ["p1", "p2"])
    db.close()

    assert seen_fingerprints(open_db(str(p)), ["p1", "p2", "p3"]) == {"p1", "p2"}


def test_seen_fingerprints_empty_inputs():
    db = open_db(":memory:")
    assert seen_fingerprints(db, []) == set()


# --- Task 2: Fetcher protocol + fixture shape --------------------------------------

def test_fixture_fetcher_yields_contract_batches():
    from jester.fetchers import FixtureFetcher

    batches = FixtureFetcher().fetch()
    assert isinstance(batches, list) and batches
    for b in batches:
        assert {"platform", "source", "thread_id", "comments"} <= set(b)
        assert all("fingerprint" in c and "body" in c for c in b["comments"])


def test_fetcher_protocol_satisfied():
    from jester.fetchers import Fetcher, FixtureFetcher

    f: Fetcher = FixtureFetcher()
    assert callable(f.fetch)


# --- Task 3: cmd_run wiring ----------------------------------------------------------

def _seed_via_fetcher(db_path):
    """Simulate one prior completed ingestion of the fixture corpus."""
    from jester.cli import cmd_run

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="first-run"))
    return get_runs(open_db(str(db_path)))


def test_second_identical_run_ingests_nothing_new(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="run-1"))
    before = [r["id"] for r in pending_batches(open_db(str(p)))]

    # Second run: no pending batches exist, fetcher re-offers the same corpus,
    # and the skip-list must keep it from queueing anything new.
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="run-2"))

    db = open_db(str(p))
    r = get_runs(db)[0]
    assert r["run_id"] == "run-2"
    assert r["n_batches"] == 0
    assert r["n_comments"] == 0
    assert [x["id"] for x in pending_batches(db)] == before


def test_fingerprints_marked_after_successful_processing(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="mark-run"))
    db = open_db(str(p))
    # Fixture fingerprints are now skip-listed.
    from jester.fetchers import FixtureFetcher

    prints = [
        c["fingerprint"] for b in FixtureFetcher().fetch() for c in b["comments"]
    ]
    assert seen_fingerprints(db, prints) == set(prints)
