"""schema_problem: the console's one actionable answer for a stale archive.

The console deliberately opens without migrating, so it needs a cheap read
that says what this build wants and the file does not have. A check that
cannot run because a writer holds the lock must not report the archive as
stale: the route's own lock handling is the right place for that answer.
"""
import sqlite3

from jester.store import open_db, schema_problem


def test_a_current_archive_has_no_schema_problem(tmp_path):
    db = open_db(str(tmp_path / "current.db"))
    try:
        assert schema_problem(db) is None
    finally:
        db.close()


def test_a_stale_archive_lists_what_it_is_missing(tmp_path):
    path = tmp_path / "stale.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id TEXT);")
    conn.close()

    db = sqlite3.connect(path)
    try:
        problem = schema_problem(db)
    finally:
        db.close()

    assert "ideas" in problem["tables"]
    assert "runs.env" in problem["columns"]
    assert "ideas" in problem["missing"] and "runs.env" in problem["missing"]


def test_a_locked_archive_is_not_reported_as_stale(tmp_path):
    path = tmp_path / "locked.db"
    open_db(str(path)).close()

    writer = sqlite3.connect(path)
    writer.execute("BEGIN EXCLUSIVE")
    reader = sqlite3.connect(path, timeout=0)
    reader.execute("PRAGMA busy_timeout=0")
    try:
        assert schema_problem(reader) is None
    finally:
        reader.close()
        writer.rollback()
        writer.close()
