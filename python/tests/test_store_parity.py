"""§37.20 Phase A: ingest_batch column parity with the Go store owner (D-17)."""
from pathlib import Path

from jester.store import open_db

# The exact columns Go's EnqueueBatch/worker code expects on a shared database.
GO_INGEST_COLUMNS = {
    "id", "run_id", "platform", "source", "thread_id", "batch_index",
    "comments", "status", "claimed_at", "attempts", "error", "created_at",
}


def _columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_python_db_carries_go_ingest_columns(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    assert GO_INGEST_COLUMNS <= _columns(db, "ingest_batch")


_LEGACY_INGEST_DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
INSERT INTO meta VALUES('schema_version','1');
CREATE TABLE ingest_batch(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    source TEXT,
    thread_id TEXT,
    comments TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_legacy_ingest_batch_migrates_to_full_shape(tmp_path):
    p = tmp_path / "legacy.db"
    import sqlite3

    con = sqlite3.connect(str(p))
    con.executescript(_LEGACY_INGEST_DDL)
    con.commit()
    con.close()

    db = open_db(str(p))  # idempotent ALTERs must fill the gap
    assert GO_INGEST_COLUMNS <= _columns(db, "ingest_batch")


def test_go_enqueue_writes_into_python_created_db(tmp_path):
    """The D-17 handoff in miniature: the column set must be wide enough that
    Go's exact INSERT (run_id..batch_index..comments,status) succeeds here."""
    import sqlite3

    p = tmp_path / "j.db"
    open_db(str(p))
    con = sqlite3.connect(str(p))
    cur = con.execute(
        "INSERT INTO ingest_batch(run_id,platform,source,thread_id,batch_index,"
        "comments,status) VALUES(?,?,?,?,?,?,'pending')",
        ("go-run", "reddit", "src", "tid", 0, '[{"body":"b","fingerprint":"f1"}]'),
    )
    con.commit()
    row = con.execute(
        "SELECT run_id, batch_index, status FROM ingest_batch WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()
    assert row == ("go-run", 0, "pending")


def test_r31_wal_and_busy_timeout_on_file_dbs(tmp_path):
    """R31: both sides open the DB in WAL mode with a busy_timeout so short
    helper commands wait instead of failing on writer contention."""
    p = tmp_path / "j.db"
    db = open_db(str(p))
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_retention_skips_and_logs_when_write_lock_held(tmp_path, capsys):
    """R31: retention skips-and-logs rather than fighting an active run."""
    import sqlite3

    from jester.cli import cmd_retention

    p = tmp_path / "j.db"
    open_db(str(p))

    blocker = sqlite3.connect(str(p))
    blocker.execute("BEGIN EXCLUSIVE")

    class _NS:
        db = str(p)

    try:
        cmd_retention(_NS())
    finally:
        blocker.rollback()
        blocker.close()

    out = capsys.readouterr().out.lower()
    assert "skipped" in out or "write lock" in out
