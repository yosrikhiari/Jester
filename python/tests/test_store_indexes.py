"""F-03: hot-path indexes exist and the planner uses them.

Regression net for the missing-index finding: the console overview, the
queue claim, and the ideas list filter on these columns on every request.
If a future schema change drops an index, the EXPLAIN assertions fail
before users feel the full scan.
"""
import sqlite3

from jester.store import _ADD_INDEXES, open_db


def _indexes(db: sqlite3.Connection) -> set:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r[0] for r in rows}


def test_all_hot_path_indexes_are_created(tmp_path):
    db = open_db(str(tmp_path / "idx.db"))
    try:
        names = _indexes(db)
        for stmt in _ADD_INDEXES:
            # "CREATE INDEX IF NOT EXISTS <name> ON ..."
            name = stmt.split()[5]
            assert name in names, f"{name} was not created"
    finally:
        db.close()


def test_queue_claim_uses_the_status_index(tmp_path):
    """The claim query every treat runs must not full-scan ingest_batch.

    Asserts SEARCH (not the index's name): any covering index satisfies the
    requirement, so a future re-indexing does not break the test. Rows are
    inserted first — SQLite plans SCAN for empty tables no matter what
    indexes exist, which would make this test assert on nothing.
    """
    db = open_db(str(tmp_path / "idx.db"))
    try:
        for i in range(10):
            db.execute(
                "INSERT INTO ingest_batch(platform, source, thread_id, comments, "
                "status, kind) VALUES(?,?,?,?,?,?)",
                ("hn", "s", f"t{i}", "[]", "pending", "comment"))
        db.commit()
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM ingest_batch "
            "WHERE status='pending' AND kind='comment' LIMIT 1"
        ).fetchall()
        detail = " ".join(r[-1] for r in plan)
        assert "SEARCH" in detail and "SCAN" not in detail, \
            f"full scan: {detail}"
    finally:
        db.close()


def test_ideas_list_uses_the_status_index(tmp_path):
    db = open_db(str(tmp_path / "idx.db"))
    try:
        for i in range(10):
            db.execute(
                "INSERT INTO ideas(title, status, overall) VALUES(?,?,?)",
                (f"idea {i}", "new", float(i)))
        db.commit()
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM ideas "
            "WHERE status='new' ORDER BY overall DESC LIMIT 50"
        ).fetchall()
        detail = " ".join(r[-1] for r in plan)
        assert "SEARCH" in detail and "SCAN" not in detail, \
            f"full scan: {detail}"
    finally:
        db.close()


def test_indexes_are_idempotent_on_reopen(tmp_path):
    """Second open (migration path) must not raise and must keep the indexes."""
    path = str(tmp_path / "idx.db")
    open_db(path).close()
    db = open_db(path)
    try:
        names = _indexes(db)
        assert "idx_nuggets_platform" in names
        assert "idx_runs_status_started" in names
    finally:
        db.close()
