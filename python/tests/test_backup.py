"""Backing up the archive.

`data/jester.db` is the product: every scraped comment, nugget and idea, plus
the fingerprint skip-list that stops the whole corpus being re-ingested. It
exists once, on one disk, and it is gitignored.

The rule these pin down: a backup nobody has opened is a hope. A plain file
copy of a live SQLite database produces a file that opens fine and is corrupt,
and that is discovered at restore time.
"""

import gzip
import sqlite3
import threading
import time

import pytest

from jester.backup import backup_db, prune_backups, verify_backup
from jester.models import Nugget
from jester.store import insert_nugget, open_db


def _seed(path, n=25):
    db = open_db(path)
    for i in range(n):
        insert_nugget(db, Nugget(unique_key=f"k{i}", platform="reddit",
                                 raw_text=f"body {i}", extracted_insight=f"i {i}"))
    db.close()


def test_backup_is_readable_and_complete(tmp_path):
    src = str(tmp_path / "j.db")
    _seed(src, 25)

    res = backup_db(src, str(tmp_path / "b"))
    assert res["ok"] is True, res.get("error")

    check = verify_backup(res["path"])
    assert check["ok"] is True, check.get("error")
    assert check["integrity"] == "ok"
    assert check["counts"]["nuggets"] == 25


def test_backup_survives_concurrent_writes(tmp_path):
    """The failure a `cp` cannot avoid.

    Scheduled runs write every 30 minutes. A snapshot taken mid-write must be
    a consistent database, not a torn one — SQLite's online backup API
    guarantees that and a filesystem copy does not.
    """
    src = str(tmp_path / "j.db")
    _seed(src, 10)

    stop = {"go": False}

    def writer():
        db = open_db(src)
        i = 1000
        while not stop["go"]:
            try:
                insert_nugget(db, Nugget(unique_key=f"w{i}", platform="reddit",
                                         raw_text=f"concurrent {i}"))
            except sqlite3.Error:
                pass
            i += 1
            time.sleep(0.001)
        db.close()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        res = backup_db(src, str(tmp_path / "b"))
    finally:
        stop["go"] = True
        t.join(timeout=5)

    assert res["ok"] is True, res.get("error")
    check = verify_backup(res["path"])
    assert check["ok"] is True, check.get("error")
    assert check["integrity"] == "ok"


def test_a_corrupt_backup_is_reported_not_trusted(tmp_path):
    """The whole point of verifying: 'the file exists' is not 'the file
    contains the archive'."""
    bad = tmp_path / "torn.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)  # plausible, useless

    got = verify_backup(str(bad))
    assert got["ok"] is False
    assert "error" in got


def test_verifying_something_that_is_not_there(tmp_path):
    got = verify_backup(str(tmp_path / "nope.db.gz"))
    assert got["ok"] is False
    assert "no backup" in got["error"]


def test_backup_of_a_missing_database_fails_loudly(tmp_path):
    got = backup_db(str(tmp_path / "nope.db"))
    assert got["ok"] is False
    assert "no database" in got["error"]


def test_compression_is_real_and_reversible(tmp_path):
    src = str(tmp_path / "j.db")
    _seed(src, 200)

    res = backup_db(src, str(tmp_path / "b"), compress=True)
    assert res["path"].endswith(".gz")
    # It must actually be gzip, not a renamed copy.
    with gzip.open(res["path"], "rb") as fh:
        assert fh.read(16).startswith(b"SQLite format 3")


def test_pruning_keeps_the_newest(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z",
                  "20260104T000000Z"):
        (d / f"jester-{stamp}.db.gz").write_bytes(b"x")

    removed = prune_backups(str(d), keep=2, stem="jester")
    assert len(removed) == 2
    left = sorted(p.name for p in d.glob("*.gz"))
    # Newest two survive.
    assert left == ["jester-20260103T000000Z.db.gz", "jester-20260104T000000Z.db.gz"]


def test_pruning_never_empties_the_directory(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    (d / "jester-20260101T000000Z.db.gz").write_bytes(b"x")
    assert prune_backups(str(d), keep=14, stem="jester") == []
    assert len(list(d.glob("*.gz"))) == 1
