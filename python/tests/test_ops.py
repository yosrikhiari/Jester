"""M2.4 tests: runs table, concurrent-run guard, floor flags, jester runs/retention/requeue."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jester.config import load_config
from jester.store import (
    ConcurrentRunError, enqueue_batch, insert_nugget, open_db, pending_batches,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: runs table + start_run / reap_running ---
def test_start_run_inserts_running_row():
    from jester.store import start_run, get_run

    db = open_db(":memory:")
    rid = start_run(db, "r1")
    assert isinstance(rid, int)
    row = get_run(db, rid)
    assert row["run_id"] == "r1"
    assert row["status"] == "running"
    assert row["started_at"]


def test_reap_running_flips_orphaned_to_aborted():
    from jester.store import start_run, reap_running, get_run

    db = open_db(":memory:")
    rid = start_run(db, "orphan")
    # simulate a crash: the row is still 'running'
    assert get_run(db, rid)["status"] == "running"
    reap_running(db)
    assert get_run(db, rid)["status"] == "aborted"


def test_reap_running_records_why_the_row_was_aborted():
    """An orphan row with error=NULL explains nothing.

    Four scheduled treats aborted this way and read as unexplained; the cause
    existed only in a log file. The reaper cannot know the real reason, but it
    can say that no exit was recorded, which is what points at the log.
    """
    from jester.store import start_run, reap_running, get_run

    db = open_db(":memory:")
    rid = start_run(db, "orphan")
    reap_running(db)

    row = get_run(db, rid)
    assert row["status"] == "aborted"
    assert row["error"], "an aborted run must say something about why"
    assert "no exit recorded" in row["error"]


def test_reap_running_keeps_an_error_the_process_recorded():
    """A real cause always beats the reaper's guess."""
    from jester.store import start_run, reap_running, get_run, update_run_summary

    db = open_db(":memory:")
    rid = start_run(db, "crashed")
    update_run_summary(db, "crashed", error="UnexpectedResponse: 408")
    reap_running(db)

    assert get_run(db, rid)["error"] == "UnexpectedResponse: 408"


def test_dedup_unavailable_flag():
    """A run that archived nothing because the vector store was down must not
    look like a run that simply found nothing."""
    from jester.ops import compute_floor_flags

    flags = compute_floor_flags(
        kept=0, ideas=0, all_trivial=False, failed_batches=0, dedup_deferred=12
    )
    assert "DEDUP_UNAVAILABLE" in flags

    clean = compute_floor_flags(
        kept=3, ideas=3, all_trivial=False, failed_batches=0, dedup_deferred=0
    )
    assert "DEDUP_UNAVAILABLE" not in clean


# --- Task 2: concurrent-run guard ---
def test_concurrent_run_guard_refuses_second_running():
    from jester.store import start_run

    db = open_db(":memory:")
    start_run(db, "a")
    with pytest.raises(ConcurrentRunError):
        start_run(db, "b")


def test_guard_allows_after_completion():
    from jester.store import start_run, finish_run

    db = open_db(":memory:")
    start_run(db, "a")
    finish_run(db, "a", status="completed")
    # a fresh run now succeeds
    rid = start_run(db, "b")
    assert rid


# --- Task 3: cmd_runs history ---
def test_get_runs_orders_newest_first():
    from jester.store import start_run, finish_run, get_runs

    db = open_db(":memory:")
    start_run(db, "r1"); finish_run(db, "r1", status="completed")
    start_run(db, "r2"); finish_run(db, "r2", status="completed")
    runs = get_runs(db)
    assert [r["run_id"] for r in runs] == ["r2", "r1"]


def test_cmd_runs_prints_history(capsys):
    from jester.cli import cmd_runs
    from jester.store import start_run, finish_run

    db = open_db(":memory:")
    start_run(db, "hist1"); finish_run(db, "hist1", status="completed")

    cmd_runs(_ns(db=":memory:"))  # wrong db handle; reopen below
    # Re-run against the persisted db via a file-backed path instead.
    # (the in-memory handle above is separate, so use a tmp file)
    import tempfile, os

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db2 = open_db(path)
    start_run(db2, "histA"); finish_run(db2, "histA", status="completed")
    cmd_runs(_ns(db=path))
    out = capsys.readouterr().out
    assert "histA" in out


# --- Task 4: floor flags ---
def test_compute_floor_flags_low_nuggets():
    from jester.ops import compute_floor_flags

    assert compute_floor_flags(kept=0, ideas=0, all_trivial=False, failed_batches=0) == ["LOW_NUGGETS"]


def test_compute_floor_flags_no_ideas():
    from jester.ops import compute_floor_flags

    assert compute_floor_flags(kept=5, ideas=0, all_trivial=False, failed_batches=0) == ["NO_IDEAS"]


def test_compute_floor_flags_thin_haul_and_trivial():
    from jester.ops import compute_floor_flags

    flags = compute_floor_flags(kept=3, ideas=2, all_trivial=True, failed_batches=0)
    assert "THIN_HAUL" in flags
    assert "ALL_TRIVIAL" in flags


def test_compute_floor_flags_failed_batches():
    from jester.ops import compute_floor_flags

    flags = compute_floor_flags(kept=5, ideas=3, all_trivial=False, failed_batches=2)
    assert flags == ["FAILED_BATCHES"]


# --- Task 5: cmd_run records summary + flags + guard exit on FAILED_BATCHES ---
def test_cmd_run_records_summary_and_flags(tmp_path, offline_config):
    from jester.cli import cmd_run
    from jester.store import get_runs

    db_path = tmp_path / "j.db"
    cmd_run(_ns(config=str(offline_config), db=str(db_path), run="ops-run"))

    runs = get_runs(open_db(str(db_path)))
    assert runs, "cmd_run must record a run"
    r = runs[0]
    assert r["status"] == "completed"
    assert r["n_nuggets_kept"] >= 0
    assert r["n_ideas"] >= 0
    # kept=3 -> 1 idea is a thin haul (§13.1). The nuggets.trivial column stays 0
    # until the R37 rule-based triviality judge lands, so ALL_TRIVIAL must NOT fire.
    flags = json.loads(r["floor_flags"] or "[]")
    assert "THIN_HAUL" in flags
    assert "ALL_TRIVIAL" not in flags


def test_cmd_run_exits_nonzero_on_failed_batches(tmp_path):
    from jester.cli import cmd_run
    from jester.store import mark_batch_failed, enqueue_batch

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    # A failed batch remains failed through the run (cmd_run only processes pending).
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)

    with pytest.raises(SystemExit):
        cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="fail-run"))


# --- ingest timeout is configuration, not a constant ---
def test_ingest_timeout_comes_from_config(tmp_path):
    """The worker's hour was a constant in worker.py, editable only in source.

    It stopped fitting once real estate joined the walk: a measured cycle
    spent ~23 minutes on the other sources and was killed 37 minutes into the
    real-estate portion with that portion unfinished. How long the walk needs
    depends on which sources a deployment enables, which is a config question.
    """
    import types

    from jester.cli import _ingest_timeout

    args = types.SimpleNamespace(config=str(REPO_CONFIG), timeout=None)
    assert _ingest_timeout(args) == 7200


def test_an_explicit_timeout_beats_the_config(tmp_path):
    import types

    from jester.cli import _ingest_timeout

    args = types.SimpleNamespace(config=str(REPO_CONFIG), timeout=45)
    assert _ingest_timeout(args) == 45


def test_an_unreadable_config_leaves_the_worker_its_own_default(tmp_path):
    """A broken config must not stop the fetch, and must not mean "no limit".

    None is what worker.ingest reads as "the caller has no opinion"; it
    answers with DEFAULT_INGEST_TIMEOUT rather than running forever.
    """
    import types

    from jester.cli import _ingest_timeout

    args = types.SimpleNamespace(config=str(tmp_path / "nope"), timeout=None)
    assert _ingest_timeout(args) is None


def test_zero_in_config_means_the_built_in_default(tmp_path):
    import types

    import jester.cli as cli

    cfg = types.SimpleNamespace(thresholds=types.SimpleNamespace(ingest_timeout_seconds=0))
    args = types.SimpleNamespace(config="unused", timeout=None)
    monkey = cli.load_config
    cli.load_config = lambda _c: cfg
    try:
        assert cli._ingest_timeout(args) is None
    finally:
        cli.load_config = monkey


# --- Task 6: retention sweep ---
def test_retention_sweep_purges_old_raw_text(tmp_path):
    from jester.store import retention_sweep, insert_nugget
    from jester.models import Nugget

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    n = Nugget(unique_key="old", platform="reddit", category="pain_point",
               extracted_insight="x", raw_text="SECRET")
    insert_nugget(db, n)
    db.execute("UPDATE nuggets SET created_at='2020-01-01 00:00:00' WHERE id=1")

    now = datetime(2099, 1, 1)
    purged = retention_sweep(db, ttl_days=30, now=now)
    assert purged["nuggets_raw"] >= 1
    row = db.execute("SELECT raw_text FROM nuggets WHERE id=1").fetchone()
    assert row[0] is None  # raw text wiped


def test_retention_sweep_drops_old_completed_batches(tmp_path):
    from jester.store import retention_sweep, enqueue_batch, mark_batch_processed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_processed(db, bid)
    db.execute("UPDATE ingest_batch SET created_at='2020-01-01 00:00:00' WHERE id=?", (bid,))

    now = datetime(2099, 1, 1)
    purged = retention_sweep(db, ttl_days=30, now=now)
    assert purged["batches"] >= 1


# Mirrors go/internal/store/store.go. The listing tables are created by the GO
# worker, not by Python's open_db, so a Python-opened archive has no such table
# until a scrape has run against it - which is exactly the case _table_exists
# guards, and why these tests build the table themselves.
_LISTING_OBS_DDL = """
CREATE TABLE IF NOT EXISTS listing_observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  portal TEXT NOT NULL,
  listing_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  run_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  gallery_hash TEXT,
  price INTEGER,
  currency TEXT,
  status TEXT,
  payload TEXT NOT NULL
)"""


def _obs(db, oid, observed_at, payload):
    """One listing observation, written straight to the table the Go worker
    writes. There is no Python-side insert helper - the scrape path is Go - so
    the test writes the same columns store.go declares."""
    db.execute(_LISTING_OBS_DDL)
    db.execute(
        "INSERT INTO listing_observation"
        " (id, portal, listing_id, observed_at, run_id, content_hash, price,"
        "  currency, status, payload)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (oid, "property24", "L%d" % oid, observed_at, "r1", "h%d" % oid,
         995000, "ZAR", "active", json.dumps(payload)),
    )


def test_retention_sweep_ages_text_and_seller_out_of_old_listings(tmp_path):
    """§14 reaches the real-estate tables, which it did not before: the sweep
    named nuggets and ingest_batch only, so a listing description sat in the
    archive forever."""
    from jester.store import retention_sweep

    db = open_db(str(tmp_path / "j.db"))
    _obs(db, 1, "2020-01-01T00:00:00Z", {
        "title": "2 bed in Zonnebloem", "description": "Agent prose, at length.",
        "seller": "Jane Smith", "location": "Zonnebloem", "bedrooms": 2,
    })
    db.commit()

    purged = retention_sweep(db, ttl_days=30, now=datetime(2099, 1, 1))
    assert purged["listings"] == 1

    row = db.execute("SELECT payload, price FROM listing_observation WHERE id=1").fetchone()
    doc = json.loads(row[0])
    assert "description" not in doc
    assert "seller" not in doc
    # The point of the table survives the sweep.
    assert row[1] == 995000
    assert doc["title"] == "2 bed in Zonnebloem"
    assert doc["bedrooms"] == 2
    assert doc["location"] == "Zonnebloem"


def test_retention_sweep_leaves_recent_listings_alone(tmp_path):
    from jester.store import retention_sweep

    db = open_db(str(tmp_path / "j.db"))
    _obs(db, 1, "2098-12-25T00:00:00Z", {"description": "still fresh", "seller": "Jane"})
    db.commit()

    purged = retention_sweep(db, ttl_days=30, now=datetime(2099, 1, 1))
    assert purged["listings"] == 0
    doc = json.loads(
        db.execute("SELECT payload FROM listing_observation WHERE id=1").fetchone()[0]
    )
    assert doc["description"] == "still fresh"


def test_retention_sweep_counts_only_rows_it_changed(tmp_path):
    """A row that never carried the keys is not rewritten, so the number the
    sweep reports is a number of rows actually minimized rather than a count
    of rows it looked at."""
    from jester.store import retention_sweep

    db = open_db(str(tmp_path / "j.db"))
    _obs(db, 1, "2020-01-01T00:00:00Z", {"title": "no prose, no seller"})
    _obs(db, 2, "2020-01-01T00:00:00Z", {"description": "prose"})
    db.commit()

    assert retention_sweep(db, ttl_days=30, now=datetime(2099, 1, 1))["listings"] == 1


def test_retention_sweep_survives_an_archive_with_no_listing_tables(tmp_path):
    """An archive written before real estate existed must sweep, not crash."""
    from jester.store import retention_sweep

    # Nothing creates the listing tables here, which is precisely the archive
    # in question: Python's open_db does not know them.
    db = open_db(str(tmp_path / "j.db"))

    assert retention_sweep(db, ttl_days=30, now=datetime(2099, 1, 1))["listings"] == 0


def test_cmd_retention_invokes_sweep(tmp_path, capsys):
    from jester.cli import cmd_retention
    from jester.store import enqueue_batch, mark_batch_processed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_processed(db, bid)
    db.execute("UPDATE ingest_batch SET created_at='2020-01-01 00:00:00' WHERE id=?", (bid,))
    db.commit()  # release the write lock before cmd_retention opens its own connection

    cmd_retention(_ns(db=str(db_path)))
    out = capsys.readouterr().out
    assert "retention" in out.lower()


# --- Task 7: requeue failed ---
def test_requeue_failed_resets_to_pending(tmp_path):
    from jester.store import mark_batch_failed, requeue_failed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)

    count = requeue_failed(db)
    assert count == 1
    pending = pending_batches(db)
    assert any(b["id"] == bid for b in pending)


def test_cmd_requeue_invokes(tmp_path, capsys):
    from jester.cli import cmd_requeue
    from jester.store import mark_batch_failed, enqueue_batch

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)

    cmd_requeue(_ns(db=str(db_path)))
    out = capsys.readouterr().out
    assert "requeue" in out.lower()
