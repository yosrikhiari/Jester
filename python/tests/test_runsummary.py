"""Run-summary enrichment tests (§37.8): origin, n_posts, expected/actual duration."""
import sqlite3
from pathlib import Path

import pytest

from jester.store import get_run, get_runs, open_db, start_run

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: origin on start_run ---------------------------------------------

def test_start_run_defaults_to_manual(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "r1")
    assert get_run(db, rid)["origin"] == "manual"


def test_start_run_records_nightly_origin(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "r2", origin="nightly")
    assert get_run(db, rid)["origin"] == "nightly"


# --- Task 2: schema migration --------------------------------------------------

def test_new_columns_present_on_fresh_db(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(runs)").fetchall()}
    assert {"origin", "n_posts", "duration_expected_s", "duration_actual_s"} <= cols


_LEGACY_RUNS_DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
INSERT INTO meta VALUES('schema_version','1');
CREATE TABLE runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    models_used TEXT,
    n_comments INTEGER,
    n_batches INTEGER,
    n_nuggets_kept INTEGER,
    n_discarded_trivial INTEGER,
    n_ideas INTEGER,
    floor_flags TEXT,
    phase_checkpoints TEXT,
    error TEXT
);
"""


def test_legacy_db_migrates_via_idempotent_alters(tmp_path):
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(str(p))
    con.executescript(_LEGACY_RUNS_DDL)
    con.commit()
    con.close()

    db = open_db(str(p))  # must not raise SchemaVersionError or OperationalError
    cols = {r[1] for r in db.execute("PRAGMA table_info(runs)").fetchall()}
    assert {"trivial_share", "origin", "n_posts", "duration_expected_s", "duration_actual_s"} <= cols


# --- Task 3: cmd_run records posts + rolling-baseline durations ----------------

def test_cmd_run_stores_posts_and_first_run_duration(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="s1"))

    r = get_runs(open_db(str(p)))[0]
    assert r["origin"] == "manual"
    assert r["n_posts"] == 1  # one thread-scoped mock batch
    assert r["duration_actual_s"] is not None and r["duration_actual_s"] >= 0
    assert r["duration_expected_s"] is None  # no priors yet — never fabricate


def test_cmd_run_second_run_uses_rolling_baseline(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="a"))
    first = [r for r in get_runs(open_db(str(p))) if r["run_id"] == "a"][0]["duration_actual_s"]

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="b"))
    second = [r for r in get_runs(open_db(str(p))) if r["run_id"] == "b"][0]

    assert second["duration_expected_s"] == pytest.approx(first, rel=1e-6)


# --- Task 4: env wiring + rendering ---------------------------------------------

def test_jester_origin_env_wires_through(tmp_path, monkeypatch):
    from jester.cli import cmd_run

    monkeypatch.setenv("JESTER_ORIGIN", "nightly")
    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="env-run"))
    assert get_runs(open_db(str(p)))[0]["origin"] == "nightly"


def test_cmd_runs_renders_new_fields(tmp_path, capsys):
    from jester.cli import cmd_run, cmd_runs

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="disp"))

    cmd_runs(_ns(db=str(p)))
    out = capsys.readouterr().out
    assert "origin=" in out
    assert "posts=" in out
    assert "dur=" in out
