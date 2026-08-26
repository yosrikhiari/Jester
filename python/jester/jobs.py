"""Long operations that outlive an HTTP request.

Regrouping the full archive takes about nine and a half minutes; a deep ingest
and a full re-embed are the same shape. All three were reachable only through a
console button that held one request open for the duration, so the browser gave
up long before the work did — the job finished, and nobody saw it.

WHAT THIS IS NOT. Not a queue, not a scheduler, not a worker pool. One job at a
time, in a thread, with its state in SQLite so a poll can read it. Anything
more would be a second scheduling system next to the one `jester schedule`
already owns, and this repo has been bitten before by behaviour that lives in
two places.

WHY SQLITE AND NOT AN IN-MEMORY DICT. The console is restarted often — every
code change in this session restarted it — and an in-memory job table loses the
record of what ran the moment it does. A job that vanished is
indistinguishable from a job that never started, which is exactly the ambiguity
`runs` exists to prevent for the pipeline.
"""

import json
import threading
import traceback
from typing import Callable, Optional

from jester.store import _now

#: One job at a time, process-wide. A second concurrent regroup would fight the
#: first for the same Qdrant collection (qdrant_neighbours deletes and
#: recreates it), and a second deep ingest would double the outbound request
#: rate against platforms whose pacing is the thing keeping us welcome.
_LOCK = threading.Lock()
_CURRENT: Optional[threading.Thread] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',   -- running|done|error
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    -- Free-text line the job updates as it goes, so a poll can say WHERE it
    -- is rather than only that it is still going. "Still running" after nine
    -- minutes is indistinguishable from "hung".
    progress    TEXT,
    result      TEXT,                              -- JSON on success
    error       TEXT
);
"""


def ensure_schema(db) -> None:
    db.executescript(SCHEMA)
    db.commit()


def start_job(db_path: str, kind: str, fn: Callable[[Callable[[str], None]], dict]):
    """Run `fn` in a thread, recording its lifecycle in `jobs`.

    `fn` is handed a `progress(str)` callback and must return a JSON-safe dict.
    It gets its OWN database connection: sqlite3 connections are not safe to
    share across threads, and handing it the caller's would work in testing and
    fail under the console's request threads.

    Returns the job row id, or a dict describing why it could not start.
    """
    from jester.store import open_db

    global _CURRENT
    with _LOCK:
        if _CURRENT is not None and _CURRENT.is_alive():
            return {
                "ok": False,
                "error": "another job is still running",
                "detail": "one at a time: a second pass would fight the first "
                          "for the same vector collection",
            }

        db = open_db(db_path)
        ensure_schema(db)
        cur = db.execute(
            "INSERT INTO jobs(kind, status, progress) VALUES(?, 'running', ?)",
            (kind, "starting"),
        )
        job_id = int(cur.lastrowid)
        db.commit()
        db.close()

        def progress(msg: str) -> None:
            conn = open_db(db_path)
            try:
                conn.execute(
                    "UPDATE jobs SET progress=? WHERE id=?", (str(msg)[:500], job_id)
                )
                conn.commit()
            finally:
                conn.close()

        def run() -> None:
            conn = open_db(db_path)
            try:
                out = fn(progress) or {}
                conn.execute(
                    "UPDATE jobs SET status='done', finished_at=?, result=?, "
                    "progress=? WHERE id=?",
                    (_now(), json.dumps(out, default=str)[:20000], "finished", job_id),
                )
            except Exception as exc:  # noqa: BLE001
                # The traceback goes in the row, not just the console's stderr:
                # a job that failed at 03:00 is read at 09:00, and "error" with
                # no cause is a dead end.
                conn.execute(
                    "UPDATE jobs SET status='error', finished_at=?, error=? WHERE id=?",
                    (
                        _now(),
                        (f"{exc}\n\n{traceback.format_exc()}")[:8000],
                        job_id,
                    ),
                )
            finally:
                conn.commit()
                conn.close()

        t = threading.Thread(target=run, name=f"jester-job-{kind}", daemon=True)
        _CURRENT = t
        t.start()
        return {"ok": True, "job_id": job_id, "kind": kind}


def get_job(db, job_id: int):
    ensure_schema(db)
    db.row_factory = __import__("sqlite3").Row
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    d = {k: row[k] for k in row.keys()}
    if d.get("result"):
        try:
            d["result"] = json.loads(d["result"])
        except (TypeError, ValueError):
            pass
    return d


def recent_jobs(db, limit: int = 10):
    ensure_schema(db)
    db.row_factory = __import__("sqlite3").Row
    rows = db.execute(
        "SELECT id, kind, status, started_at, finished_at, progress, error "
        "FROM jobs ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def reap_stale_jobs(db, older_than_minutes: int = 120) -> int:
    """Mark jobs that were running when the process died.

    A daemon thread does not survive a restart, so a row left at 'running' is
    a job nobody is working on. Left alone it blocks nothing (the lock is
    in-memory and gone too) but it lies to the console forever.
    """
    ensure_schema(db)
    cur = db.execute(
        "UPDATE jobs SET status='error', finished_at=?, "
        "error='process exited while this job was running' "
        "WHERE status='running' AND started_at <= datetime('now', ?)",
        (_now(), f"-{int(older_than_minutes)} minutes"),
    )
    db.commit()
    return cur.rowcount
