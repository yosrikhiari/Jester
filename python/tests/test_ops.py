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
def test_cmd_run_records_summary_and_flags(tmp_path):
    from jester.cli import cmd_run
    from jester.store import get_runs

    db_path = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="ops-run"))

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
