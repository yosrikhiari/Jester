"""§37.24 jester reembed tests: the needs_reembed recovery loop."""
from pathlib import Path

import pytest

from jester.config import load_config
from jester.models import Nugget
from jester.store import insert_nugget, mark_reembedded, open_db, pending_reembeds, start_run

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _flagged(db, key):
    insert_nugget(db, Nugget(unique_key=key, platform="reddit", category="pain_point",
                             extracted_insight="x", raw_text="body text", needs_reembed=True))


def test_pending_reembeds_returns_only_flagged(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    _flagged(db, "k1")
    clean = Nugget(unique_key="k2", platform="reddit", category="pain_point",
                   extracted_insight="y", needs_reembed=False)
    insert_nugget(db, clean)

    rows = pending_reembeds(db)
    assert [r["unique_key"] for r in rows] == ["k1"]


def test_mark_reembedded_clears_flag_and_stamps_id(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    _flagged(db, "k1")

    mark_reembedded(db, "k1", "emb:xyz")

    row = db.execute("SELECT needs_reembed, embedding_id FROM nuggets WHERE unique_key='k1'").fetchone()
    assert row[0] == 0 and row[1] == "emb:xyz"


def test_cmd_reembed_clears_backlog_happy_path(tmp_path):
    from jester.cli import cmd_reembed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    _flagged(db, "a1")
    _flagged(db, "a2")
    db.close()

    cmd_reembed(_ns(db=str(db_path), config=str(REPO_CONFIG), pending=True))

    db2 = open_db(str(db_path))
    n = db2.execute("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1").fetchone()[0]
    assert n == 0


def test_cmd_reembed_failure_keeps_flags_and_exits_one(tmp_path, monkeypatch):
    from jester.cli import cmd_reembed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    _flagged(db, "keep-flagged")
    db.close()

    class BoomStore:
        def upsert(self, *a, **k):
            raise RuntimeError("qdrant down")

    monkeypatch.setattr("jester.cli._vector_for", lambda cfg, p: BoomStore())

    with pytest.raises(SystemExit) as exc:
        cmd_reembed(_ns(db=str(db_path), config=str(REPO_CONFIG), pending=True))
    assert exc.value.code == 1

    db2 = open_db(str(db_path))
    still = db2.execute("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1").fetchone()[0]
    assert still == 1


def test_cmd_reembed_refuses_while_run_active(tmp_path):
    from jester.cli import cmd_reembed

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    _flagged(db, "pending-thing")
    start_run(db, "active-run")  # someone mid-run
    db.close()

    with pytest.raises(SystemExit) as exc:
        cmd_reembed(_ns(db=str(db_path), config=str(REPO_CONFIG), pending=True))
    assert exc.value.code == 1

    # No mutation happened.
    db2 = open_db(str(db_path))
    still = db2.execute("SELECT needs_reembed FROM nuggets WHERE unique_key='pending-thing'").fetchone()[0]
    assert still == 1


def test_cmd_reembed_empty_backlog_ok(tmp_path, capsys):
    from jester.cli import cmd_reembed

    p = tmp_path / "j.db"
    open_db(str(p))

    cmd_reembed(_ns(db=str(p), config=str(REPO_CONFIG), pending=True))
    out = capsys.readouterr().out
    assert "backlog empty" in out or "OK" in out
