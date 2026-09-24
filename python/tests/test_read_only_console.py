"""Opening an archive without reshaping it.

A second console pointed at an archive somebody else is writing has two jobs
it must not do: migrate the schema, and perform maintenance. `open_db`'s
normal path reconciles the schema, runs BASE_SCHEMA and stamps
schema_version, and `executescript` opens a write transaction even when
nothing needs changing — so a viewing console writes to a file it is only
visiting, on every single request.

WHY THIS IS NOT `mode=ro`. That was the first implementation and it does not
survive a live database: reading a file whose other writers are active still
needs write access to the DIRECTORY, for lock and journal handling. Plain
SELECTs died intermittently with "attempt to write a readonly database",
which names the file and blames the wrong thing. It passed against a quiet
database, which is exactly how it would have shipped.

So the connection is ordinary and the restraint is in the caller.
"""
import sqlite3

import pytest

from jester.console.api import ConsoleAPI
from jester.store import open_db


def test_a_normal_open_creates_the_schema(tmp_path):
    db = open_db(str(tmp_path / "fresh.db"))
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"nuggets", "ideas", "runs"} <= tables


def test_migrate_false_does_not_touch_the_schema(tmp_path):
    """The point of the flag. Pointed at an empty file it leaves an empty
    file, rather than quietly deciding what shape somebody else's archive
    should be."""
    path = tmp_path / "untouched.db"
    sqlite3.connect(str(path)).close()          # exists, no schema
    db = open_db(str(path), migrate=False)
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == set(), f"a viewing open created {tables}"


def test_migrate_false_still_reads_a_real_archive(tmp_path):
    path = str(tmp_path / "a.db")
    writer = open_db(path)
    writer.execute("INSERT INTO meta(key,value) VALUES('probe','1')")
    writer.commit()
    writer.close()

    viewer = open_db(path, migrate=False)
    assert viewer.execute(
        "SELECT value FROM meta WHERE key='probe'").fetchone()[0] == "1"


def test_rows_come_back_addressable_by_name_either_way(tmp_path):
    """Both paths set row_factory. A viewer that returned bare tuples would
    fail deep inside code that reads columns by name, far from the flag."""
    path = str(tmp_path / "b.db")
    open_db(path).close()
    viewer = open_db(path, migrate=False)
    row = viewer.execute("SELECT 1 AS answer").fetchone()
    assert row["answer"] == 1


# ---- what a read-only console refuses to do --------------------------------

def test_a_read_only_console_does_not_reap_other_peoples_jobs(tmp_path,
                                                              offline_config):
    """Reaping declares a job dead. A console visiting a live archive cannot
    see the process that owns that job, so it is in no position to say."""
    path = str(tmp_path / "c.db")
    open_db(path).close()

    api = ConsoleAPI(db_path=path, config_dir=str(offline_config), read_only=True)
    assert api.read_only is True
    assert api.jobs()["reaped"] is False

    writer = ConsoleAPI(db_path=path, config_dir=str(offline_config))
    assert writer.read_only is False
    assert writer.jobs()["reaped"] is True


def test_the_flag_reaches_the_api_from_the_command_line(monkeypatch):
    """--read-only is only worth having if it survives argument parsing into
    the object that acts on it."""
    from jester.console import server as srv

    seen = {}
    monkeypatch.setattr(srv, "serve", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(srv, "load_env_once", lambda: None)

    srv.main([])
    assert seen["read_only"] is False

    seen.clear()
    srv.main(["--read-only"])
    assert seen["read_only"] is True


def test_an_in_memory_database_is_unaffected(tmp_path):
    """`:memory:` has no file to visit; the flag must not change it into an
    error path."""
    db = open_db(":memory:")
    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] == 0
