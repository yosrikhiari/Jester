"""T-C2: the pipeline run row records the environment it executed in.

The journal has always stamped its experiment runs with a fingerprint; the
pipeline `runs` row recorded what was ASKED to run (models_used) and what was
produced (counts) — but nothing about the machine, the interpreter or the
checkout that produced the numbers. These tests pin the new `env` column:
written once at start_run, readable from the row, migrated onto legacy
archives without giving their history a fabricated environment, and degraded
— never failing, never stalling — when the fingerprint cannot be collected.
"""
import json
import platform
import sqlite3
import sys
from pathlib import Path

from jester import store
from jester.store import (
    finish_run,
    get_run,
    get_runs,
    open_db,
    run_env_fingerprint,
    start_run,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- the column is on the row, and it is parseable ------------------------------


def test_a_run_row_carries_a_parseable_env(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "env-run", models_used={"models": ["fake-llm"]},
                    env=run_env_fingerprint())
    env = json.loads(get_run(db, rid)["env"])
    assert set(env) == {"os", "python", "machine", "cpu_count", "code"}
    assert env["python"] == sys.version.split()[0] and env["os"]
    # The tests run inside Jester's own checkout: the snapshot names it,
    # commit and dirty exactly the way the journal's code_state does.
    assert set(env["code"]) == {"commit", "dirty"}
    assert len(env["code"]["commit"]) == 40
    assert isinstance(env["code"]["dirty"], bool)


def test_a_run_started_without_an_env_reads_as_empty(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "no-env-run")
    assert json.loads(get_run(db, rid)["env"]) == {}


def test_env_is_written_once_not_merged(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "once", env={"code": {"commit": "a" * 40, "dirty": False}})
    # env is deliberately NOT a field of update_run_summary: a run's
    # environment cannot be back-dated after the row was created.
    try:
        store.update_run_summary(db, "once", env={"code": {"commit": "b" * 40}})
        raise AssertionError("update_run_summary accepted an env field")
    except ValueError:
        pass
    assert json.loads(get_run(db, rid)["env"])["code"]["commit"] == "a" * 40


# --- legacy archives ------------------------------------------------------------

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


def test_a_legacy_archive_migrates_and_keeps_its_history(tmp_path):
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(str(p))
    con.executescript(_LEGACY_RUNS_DDL)
    con.execute("INSERT INTO runs(run_id, status) VALUES('pre-env', 'completed')")
    con.commit()
    con.close()

    db = open_db(str(p))  # the idempotent ALTER fills env in; no rename aside
    old_pk = db.execute("SELECT id FROM runs WHERE run_id='pre-env'").fetchone()[0]
    # Old rows read '{}' — "no environment was recorded", which is TRUE of
    # them; the migration never fabricates a stamp onto kept history (R29).
    assert json.loads(get_run(db, old_pk)["env"]) == {}
    rid = start_run(db, "post-env", env={"code": {"commit": "a" * 40, "dirty": False}})
    assert json.loads(get_run(db, rid)["env"])["code"]["dirty"] is False


# --- every failure degrades; none stops a run -----------------------------------


def test_the_whole_snapshot_degrades_explicitly(tmp_path, monkeypatch):
    db = open_db(str(tmp_path / "j.db"))

    def boomed(argv, timeout=10.0):
        raise OSError("git is missing")

    monkeypatch.setattr(store, "_env_git", boomed)
    snapshot = run_env_fingerprint()
    # One explicit entry, not a half-record that would let a reader mistake a
    # failed collection for a clean checkout.
    assert set(snapshot) == {"unavailable"}
    # The run still starts, records, and finishes.
    rid = start_run(db, "degraded", env=snapshot)
    finish_run(db, "degraded", status="completed", n_ideas=3)
    row = get_run(db, rid)
    assert row["status"] == "completed" and json.loads(row["env"])["unavailable"]


def test_git_failure_marks_the_code_entry_unavailable(tmp_path):
    snapshot = run_env_fingerprint(code_dir=tmp_path / "nowhere")
    # os/python/machine survive; the code part is the degraded one, and it
    # SAYS so instead of looking clean.
    assert "python" in snapshot and "os" in snapshot
    assert "git rev-parse HEAD" in snapshot["code"]["unavailable"]


def test_a_platform_failure_degrades_the_whole_snapshot(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("os query exploded")

    monkeypatch.setattr(platform, "platform", boom)
    assert set(run_env_fingerprint()) == {"unavailable"}


# --- the pipeline sees it end to end --------------------------------------------


def test_cmd_run_writes_the_env_onto_the_row(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="env-tick"))
    env = json.loads(get_runs(open_db(str(p)))[0]["env"])
    assert "python" in env and "os" in env
    assert len(env["code"]["commit"]) == 40
    assert isinstance(env["code"]["dirty"], bool)


def test_cmd_run_survives_a_failing_fingerprint(tmp_path, monkeypatch):
    from jester.cli import cmd_run

    def boomed(argv, timeout=10.0):
        raise OSError("git is missing")

    monkeypatch.setattr(store, "_env_git", boomed)
    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="degraded-tick"))
    row = get_runs(open_db(str(p)))[0]
    assert row["status"] == "completed"
    assert "unavailable" in json.loads(row["env"])
