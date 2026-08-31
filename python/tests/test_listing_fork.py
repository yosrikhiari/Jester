"""C2: listing batches must be invisible to the LLM side, permanently."""
import json
import sqlite3

from jester.store import open_db, pending_batches


def _enqueue(db, kind, **kw):
    db.execute(
        "INSERT INTO ingest_batch(run_id, platform, source, thread_id, batch_index,"
        " kind, comments, listings, status) VALUES (?,?,?,?,?,?,?,?, 'pending')",
        (kw.get("run_id", "r"), kw.get("platform", "p"), kw.get("source", "s"),
         kw.get("thread_id", "t"), kw.get("batch_index", 0), kind,
         kw.get("comments", "[]"), kw.get("listings")),
    )
    db.commit()


def test_listing_batches_are_never_claimed(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    _enqueue(db, "comment", thread_id="t1", comments=json.dumps([{"body": "hi"}]))
    _enqueue(db, "listing", thread_id="RM1",
             listings=json.dumps([{"listing_id": "RM1", "price": 450000}]))

    rows = pending_batches(db)
    ids = [r["thread_id"] for r in rows]
    assert ids == ["t1"], (
        "a listing batch reached the extractor — listings are already "
        "structured and must never cost tokens"
    )


def test_rows_written_before_the_fork_still_claimable(tmp_path):
    """The existing backlog must survive the migration.

    SQLite's ADD COLUMN ... NOT NULL DEFAULT 'comment' backfills every row that
    already existed, so a pre-fork batch becomes a comment batch rather than an
    unknown one. Worth asserting rather than assuming: if it backfilled to
    something else, or to NULL under a nullable column, the whole 20,589-comment
    backlog would quietly stop being claimable instead of failing loudly.
    """
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(str(p))
    con.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta VALUES('schema_version','1');"
        "CREATE TABLE ingest_batch("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,"
        " source TEXT, thread_id TEXT, comments TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO ingest_batch(platform, source, thread_id, comments)"
        " VALUES('reddit','s','old-thread','[]');"
    )
    con.commit()
    con.close()

    db = open_db(str(p))
    kind = db.execute(
        "SELECT kind FROM ingest_batch WHERE thread_id='old-thread'"
    ).fetchone()[0]
    assert kind == "comment", f"pre-fork row migrated to kind={kind!r}"

    rows = pending_batches(db)
    assert [r["thread_id"] for r in rows] == ["old-thread"]


def test_fork_columns_exist_on_a_python_created_db(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(ingest_batch)")}
    assert {"kind", "listings"} <= cols, (
        "the Go worker writes these; a Python-created database must be wide "
        "enough to receive a listing batch"
    )
