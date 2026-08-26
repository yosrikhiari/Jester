"""Background jobs: work that outlives the request that started it.

Regrouping the archive takes ~577s on 1,763 nuggets. Reachable only through a
console button that held one HTTP request for the duration, the browser gave up
long before the work did — the job finished and nobody saw it.
"""

import time

import pytest

from jester.jobs import (
    get_job,
    reap_stale_jobs,
    recent_jobs,
    start_job,
)
from jester.store import open_db


def _wait(db_path, job_id, want=("done", "error"), timeout=10.0):
    """Poll until the job leaves 'running'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = open_db(db_path)
        try:
            job = get_job(db, job_id)
        finally:
            db.close()
        if job and job["status"] in want:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def test_a_job_runs_and_records_its_result(tmp_path):
    path = str(tmp_path / "j.db")
    open_db(path).close()

    res = start_job(path, "demo", lambda progress: {"answer": 42})
    assert res["ok"] is True
    job = _wait(path, res["job_id"])
    assert job["status"] == "done"
    assert job["result"] == {"answer": 42}
    assert job["finished_at"]


def test_a_failing_job_keeps_the_cause(tmp_path):
    """A job that failed at 03:00 is read at 09:00; 'error' with no cause is a
    dead end, so the traceback lands on the row."""
    path = str(tmp_path / "j.db")
    open_db(path).close()

    def boom(progress):
        raise ValueError("the embedding backend was unreachable")

    res = start_job(path, "demo", boom)
    job = _wait(path, res["job_id"])
    assert job["status"] == "error"
    assert "unreachable" in job["error"]
    assert "Traceback" in job["error"], "the cause must survive, not just the message"


def test_progress_is_visible_while_the_job_runs(tmp_path):
    """"Still running" after nine minutes is indistinguishable from "hung"."""
    path = str(tmp_path / "j.db")
    open_db(path).close()
    release = {"go": False}

    def slow(progress):
        progress("halfway through the archive")
        while not release["go"]:
            time.sleep(0.02)
        return {}

    res = start_job(path, "demo", slow)
    seen = ""
    deadline = time.time() + 5
    while time.time() < deadline and "halfway" not in seen:
        db = open_db(path)
        try:
            seen = (get_job(db, res["job_id"]) or {}).get("progress") or ""
        finally:
            db.close()
        time.sleep(0.05)
    release["go"] = True
    assert "halfway" in seen, f"progress never surfaced: {seen!r}"
    _wait(path, res["job_id"])


def test_only_one_job_runs_at_a_time(tmp_path):
    """A second regroup would fight the first for the same Qdrant collection,
    which qdrant_neighbours deletes and recreates."""
    path = str(tmp_path / "j.db")
    open_db(path).close()
    release = {"go": False}

    def blocker(progress):
        while not release["go"]:
            time.sleep(0.02)
        return {}

    first = start_job(path, "demo", blocker)
    assert first["ok"] is True
    second = start_job(path, "demo", lambda progress: {})
    assert second["ok"] is False
    assert "still running" in second["error"]
    release["go"] = True
    _wait(path, first["job_id"])
    # …and the slot frees up once it finishes.
    third = start_job(path, "demo", lambda progress: {})
    assert third["ok"] is True
    _wait(path, third["job_id"])


def test_a_job_orphaned_by_a_restart_is_reaped(tmp_path):
    """A daemon thread does not survive a restart, so a row left at 'running'
    is a job nobody is working on — and it lies to the console forever."""
    path = str(tmp_path / "j.db")
    db = open_db(path)
    from jester.jobs import ensure_schema

    ensure_schema(db)
    db.execute(
        "INSERT INTO jobs(kind, status, started_at) "
        "VALUES('cluster','running', datetime('now','-300 minutes'))"
    )
    db.commit()

    assert reap_stale_jobs(db, older_than_minutes=120) == 1
    jobs = recent_jobs(db)
    assert jobs[0]["status"] == "error"
    assert "process exited" in jobs[0]["error"]


def test_a_recent_running_job_is_not_reaped(tmp_path):
    path = str(tmp_path / "j.db")
    db = open_db(path)
    from jester.jobs import ensure_schema

    ensure_schema(db)
    db.execute("INSERT INTO jobs(kind, status) VALUES('cluster','running')")
    db.commit()
    assert reap_stale_jobs(db, older_than_minutes=120) == 0


def test_a_job_gets_its_own_database_connection(tmp_path):
    """sqlite3 connections belong to the thread that opened them. The first
    async regroup died instantly with "SQLite objects created in a thread can
    only be used in that same thread" because the job reused the request
    thread's connection."""
    path = str(tmp_path / "j.db")
    main_conn = open_db(path)  # opened HERE, on the test thread

    def touches_the_db(progress):
        # Opening inside the job is the contract; using `main_conn` would
        # raise, which is exactly the bug this guards.
        db = open_db(path)
        try:
            n = db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0]
        finally:
            db.close()
        return {"nuggets": n}

    res = start_job(path, "demo", touches_the_db)
    job = _wait(path, res["job_id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"] == {"nuggets": 0}
    main_conn.close()
