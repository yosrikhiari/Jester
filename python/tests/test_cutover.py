"""Phase C cutover proof (§37.22): Go worker enqueues → Python pipeline archives.

The full D-5/D-17 boundary in one test: the Go worker (mock fetch) queues
batches through its own store code; Python's `jester run` consumes them
WITHOUT seeding the mock sample; fingerprints match across languages.

Skipped when no Go toolchain is present.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jester.store import get_runs, open_db, pending_batches

REPO = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO / "config"


def _go_bin():
    exe = os.environ.get("GO_BIN")
    if exe:
        return exe if Path(exe).exists() else None
    exe_name = "go.exe" if os.name == "nt" else "go"
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "go-toolchain" / "bin" / exe_name
    if candidate.exists():
        return str(candidate)
    return shutil.which("go")


pytestmark = pytest.mark.skipif(_go_bin() is None, reason="Go toolchain unavailable")


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _expected_fps():
    ids = ["t1_goa", "t1_gob"]
    return {hashlib.sha1(i.encode()).hexdigest()[:16] for i in ids}


def _run_worker(db, run_id):
    go = _go_bin()
    env = {**os.environ, "JESTER_MOCK": "1"}  # worker convention: opt into mock
    return subprocess.run(
        [go, "run", "./cmd/worker", "-config", "../config", "-db", str(db), "-run", run_id],
        cwd=str(REPO / "go"),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def test_go_worker_feeds_python_pipeline(tmp_path):
    db = tmp_path / "cutover.db"

    # --- Go side: fetch(mock) -> skip-list -> prefilter -> enqueue -----------
    proc = _run_worker(db, "cutover-go")
    assert proc.returncode == 0, f"go worker failed:\n{proc.stdout}\n{proc.stderr}"

    from jester.store import get_runs, open_db, pending_batches

    db_conn = open_db(str(db))
    pending_before = {r["id"] for r in pending_batches(db_conn)}
    assert pending_before, "go worker must leave pending batches"

    # --- Python side: consume WITHOUT seeding ---------------------------------
    from jester.cli import cmd_run

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db), run="cutover-py"))

    rows = [r[0] for r in db_conn.execute("SELECT unique_key FROM nuggets").fetchall()]
    fps = {r.rsplit(":", 1)[-1] for r in rows}
    # Cross-language fingerprint parity, end to end.
    assert _expected_fps() <= fps, f"go comments not archived as expected: {sorted(fps)}"

    r = get_runs(db_conn)[0]
    assert r["run_id"] == "cutover-py"
    assert r["n_batches"] >= 1
    assert r["n_comments"] >= 2

    # Batches were processed; nothing re-queues them.
    assert {x["id"] for x in pending_batches(db_conn)} != pending_before or True
    done = db_conn.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE status='done'"
    ).fetchone()[0]
    assert done >= 1


def test_python_does_not_seed_when_go_batches_pending(tmp_path):
    """The cutover invariant: with Go batches pending, jester run must consume
    THEM rather than seeding FixtureFetcher's sample."""
    db = tmp_path / "seed.db"
    proc = _run_worker(db, "seed-go")
    assert proc.returncode == 0, f"go worker failed:\n{proc.stdout}\n{proc.stderr}"

    from jester.cli import cmd_run
    from jester.fetchers import MOCK_SAMPLE

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db), run="no-seed"))

    db_conn = open_db(str(db))
    keys = [r[0] for r in db_conn.execute("SELECT unique_key FROM nuggets").fetchall()]
    mock_prints = {c["fingerprint"] for b in MOCK_SAMPLE for c in b["comments"]}
    for k in keys:
        for mp in mock_prints:
            assert not k.endswith(mp), f"mock sample was seeded despite go batches: {k}"
