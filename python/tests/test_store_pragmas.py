"""F-02: journal-mode opt-in + raised busy timeout, agreed on both sides.

Defaults are unchanged (DELETE, safe over the bind mount); WAL is opt-in via
JESTER_SQLITE_JOURNAL_MODE for local-disk / isolated-volume databases.
"""
from jester.store import BUSY_TIMEOUT_MS, JOURNAL_MODE_ENV, _journal_mode, open_db


def _pragmas(path):
    import sqlite3
    db = sqlite3.connect(path)
    try:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        return mode, timeout
    finally:
        db.close()


def test_default_journal_mode_is_delete(tmp_path, monkeypatch):
    monkeypatch.delenv(JOURNAL_MODE_ENV, raising=False)
    assert _journal_mode() == "DELETE"
    path = str(tmp_path / "pragma.db")
    open_db(path).close()
    mode, _ = _pragmas(path)
    assert mode.lower() == "delete"


def test_wal_opt_in_via_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(JOURNAL_MODE_ENV, "wal")
    assert _journal_mode() == "WAL"
    path = str(tmp_path / "pragma-wal.db")
    open_db(path).close()
    mode, _ = _pragmas(path)
    assert mode.lower() == "wal"


def test_invalid_journal_mode_falls_back_to_delete(monkeypatch):
    monkeypatch.setenv(JOURNAL_MODE_ENV, "off")
    assert _journal_mode() == "DELETE"


def test_busy_timeout_is_raised(tmp_path, monkeypatch):
    # busy_timeout is per-connection (not persisted like journal_mode), so
    # assert on the handle open_db returns, not on a fresh connection.
    monkeypatch.delenv(JOURNAL_MODE_ENV, raising=False)
    assert BUSY_TIMEOUT_MS == 15000
    db = open_db(str(tmp_path / "pragma-timeout.db"))
    try:
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        db.close()
