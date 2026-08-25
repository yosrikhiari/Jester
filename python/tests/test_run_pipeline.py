"""Integration test: ingest_batch queue + end-to-end `jester run` (mock mode)."""
from pathlib import Path

import pytest

from jester.cli import cmd_run
from jester.config import load_config
from jester.store import enqueue_batch, mark_batch_processed, open_db, pending_batches

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_ingest_batch_queue_lifecycle(tmp_path):
    db = open_db(tmp_path / "j.db")
    bid = enqueue_batch(
        db,
        "reddit",
        "https://reddit.com/r/x/comments/abc",
        "abc",
        [{"body": "sample", "fingerprint": "c1", "upvotes": 1}],
    )
    rows = pending_batches(db)
    assert len(rows) == 1
    assert rows[0]["id"] == bid
    assert rows[0]["thread_id"] == "abc"
    mark_batch_processed(db, bid)
    assert pending_batches(db) == []


def test_full_run_pipeline_seeds_and_keeps_nuggets(tmp_path):
    db_path = tmp_path / "j.db"
    cfg = load_config(REPO_CONFIG)
    args = _ns(config=str(REPO_CONFIG), db=str(db_path), run="test-run")
    cmd_run(args)
    db = open_db(db_path)
    nuggets = db.execute("SELECT unique_key FROM nuggets").fetchall()
    # M1.3 pre-filter (§37.12): the third mock comment ("This is fine, no
    # complaints here.", 33 chars) drops as too_short before extraction.
    assert len(nuggets) == 2
    # re-running should not duplicate (queued batches are marked done, dedup is exact-key)
    cmd_run(args)
    db2 = open_db(db_path)
    assert len(db2.execute("SELECT unique_key FROM nuggets").fetchall()) == 2


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns
