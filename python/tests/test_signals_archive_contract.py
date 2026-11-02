"""One archive per component, and one table per shape (R4-02, R4-01 code part).

Two languages shared one file. `data/jester.db` is the nuggets archive the Go
worker writes; the signals collector keeps `signal_run`, `problem_signal` and
the per-source streaks, and the scheduler pointed every task at the first one.
That is not a naming preference. The Go `source_state` and the Python
`source_state` share a name and disagree about every column, so `jester health`
raised `IndexError: No item with that key` on a Go-shaped archive and the streak
writer raised `no such column: consecutive_zero`.

The fix has three parts, and this file is the contract for all three:

* the scheduler resolves a database per command (`resolve_task_db`), with an
  optional per-task `db:` override in `config/schedule.yaml`;
* the Python streak table is `signals_source_state`, adopted once from a legacy
  Python-shaped `source_state` and never at the expense of the Go table;
* a Go-shaped archive can be read and written without being reshaped.

Every database here is a temporary file. Nothing opens `data/`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jester import schedule
from jester.signals import health as sighealth
from jester.signals import open_signals
from jester.signals import run as sigrun

# The Go store's table, verbatim (go/internal/store/store.go). Same name as the
# Python table, no column in common beyond the source key -- which is the whole
# problem this file exists for.
GO_SOURCE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS source_state (
  name TEXT PRIMARY KEY,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queued INTEGER NOT NULL DEFAULT 0,
  total_queued INTEGER NOT NULL DEFAULT 0,
  measured_visits INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0
);
"""

# What the Python module wrote before the rename.
PY_SOURCE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY,
  consecutive_zero INTEGER NOT NULL DEFAULT 0,
  consecutive_error INTEGER NOT NULL DEFAULT 0,
  last_ok_utc TEXT NOT NULL DEFAULT '',
  last_rows INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated_utc TEXT NOT NULL DEFAULT ''
);
"""


def _stamp(when):
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _add_run(db, source, *, at, collected=5, errors=0, status="ok", run_id=None):
    sigrun.ensure_run_table(db)
    db.execute(
        "INSERT INTO signal_run (run_id, source, mode, kind, started_utc, "
        "finished_utc, collected, new, errors, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id or f"r-{source}", source, "live", "scheduled", _stamp(at),
         _stamp(at), collected, collected, errors, status))
    db.commit()


def _table_info(db, table):
    return [tuple(r) for r in db.execute(f"PRAGMA table_info({table})").fetchall()]


def _tables(db):
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


# --- R4-01: the Go table and the Python table stop fighting ------------------

def test_a_go_shaped_archive_is_diagnosed_and_written_without_being_reshaped(tmp_path):
    """`jester health` over the nuggets archive used to raise before it printed
    a line: the run ledger gave it one source name, the Go `source_state` gave
    it rows keyed by `name`, and it asked them for `source`."""
    path = tmp_path / "go-shaped.db"
    now = datetime.now(timezone.utc)
    db = open_signals(str(path))
    try:
        _add_run(db, "hackernews", at=now - timedelta(hours=2))
        db.executescript(GO_SOURCE_STATE_DDL)
        db.execute("INSERT INTO source_state (name, visits) VALUES ('hackernews', 4)")
        db.commit()
        go_shape = _table_info(db, "source_state")

        res = sighealth.report(str(path), days=7, now=now)

        assert isinstance(res, dict)
        assert res["sources"], "the ledger row must still be reported"
        assert res["verdict"] != "barren", res
        assert all(s["status"] != sighealth.BARREN for s in res["sources"]), res["sources"]
        assert sighealth.SOURCE_STATE_TABLE not in _tables(db), (
            "the read path must not migrate the archive it reports on")

        state = sighealth.record_source_state(
            db, "hackernews",
            {"status": "ok", "errors": 0, "collected": 5, "finished_utc": _stamp(now)},
            at=_stamp(now))

        assert state["consecutive_zero"] == 0
        assert sighealth.SOURCE_STATE_TABLE in _tables(db), (
            "the writer creates the Python table next to the Go one")
        assert db.execute(
            f"SELECT COUNT(*) FROM {sighealth.SOURCE_STATE_TABLE}").fetchone()[0] == 1
        assert _table_info(db, "source_state") == go_shape, (
            "the Go table's shape is not the Python side's to change")
        assert db.execute("SELECT visits FROM source_state").fetchone()[0] == 4
    finally:
        db.close()


def test_a_legacy_python_table_is_adopted_once_and_kept(tmp_path):
    """An archive written before the rename carries real streaks. They move to
    the new name once; the old table stays exactly where it was, because
    dropping a table to rename it is a destructive act on live state."""
    path = tmp_path / "legacy-python.db"
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    try:
        db.executescript(PY_SOURCE_STATE_DDL)
        db.execute("INSERT INTO source_state VALUES "
                   "('hn', 3, 0, '2026-11-01T09:00:00+00:00', 0, '', '2026-11-01T09:00:00+00:00')")
        db.execute("INSERT INTO source_state VALUES "
                   "('himalayas', 0, 2, '', 0, 'boom', '2026-11-01T09:00:00+00:00')")
        db.commit()

        assert sighealth.ensure_source_state_table(db) == 2
        # Twice: a run that starts after the migration must not double the rows.
        assert sighealth.ensure_source_state_table(db) == 0

        rows = {r["source"]: r["consecutive_zero"]
                for r in db.execute(
                    f"SELECT source, consecutive_zero FROM {sighealth.SOURCE_STATE_TABLE}")}
        assert rows == {"hn": 3, "himalayas": 0}, rows
        assert db.execute("SELECT COUNT(*) FROM source_state").fetchone()[0] == 2, (
            "the legacy table is left in place")
        assert db.execute(
            "SELECT consecutive_error FROM source_state WHERE source = 'himalayas'"
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_a_legacy_go_shaped_table_yields_no_streaks(tmp_path):
    """A row named like one of our sources is not a streak: the columns are a
    different table's. Copying it would invent a verdict from an integer that
    means something else entirely."""
    path = tmp_path / "legacy-go.db"
    db = sqlite3.connect(str(path))
    try:
        db.executescript(GO_SOURCE_STATE_DDL)
        db.execute("INSERT INTO source_state (name, visits, last_queued) "
                   "VALUES ('hackernews', 9, 4)")
        db.commit()

        assert sighealth.ensure_source_state_table(db) == 0

        assert db.execute(
            f"SELECT COUNT(*) FROM {sighealth.SOURCE_STATE_TABLE}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM source_state").fetchone()[0] == 1
    finally:
        db.close()


# --- R4-02: one archive per component ---------------------------------------

def test_each_command_declares_the_archive_it_writes():
    """The role is a fact about the command, not a guess made by the launcher.
    Every entry must carry one: a spec that forgot it would inherit the `main`
    fallback and quietly point a collector at the nuggets archive again."""
    roles = {command: spec.get("db")
             for command, spec in schedule.COMMAND_SPEC.items()}
    missing = [c for c, role in roles.items() if role not in ("main", "signals")]
    assert not missing, f"commands with no valid db role: {missing}"

    for command in ("signals", "signals-hiring", "signals-sweep", "signals-brief",
                    "signals-retention", "signals-load", "health", "leads-reddit"):
        assert roles[command] == "signals", command
    for command in ("cycle", "ingest", "treat", "retention", "backup",
                    "backup-drill"):
        assert roles[command] == "main", command


def test_the_resolver_gives_the_signals_tasks_the_signals_archive():
    main = "data/jester.db"
    for command in ("signals", "health", "signals-hiring", "leads-reddit"):
        assert schedule.resolve_task_db({"command": command}, main_db=main) == (
            "data/signals-live.db"), command
    assert schedule.resolve_task_db({"command": "ingest"}, main_db=main) == main


def test_the_default_signals_archive_is_the_sibling_of_the_main_one():
    """A host that moved its archive with --db moves this one with it."""
    got = schedule.resolve_task_db({"command": "signals"}, main_db="/srv/x/archive.db")
    assert Path(got) == Path("/srv/x/signals-live.db")


def test_leads_reddit_reads_the_nuggets_archive_and_writes_the_signals_one():
    """It scores rows the Go worker stored, so the two roles are both real: the
    reads come from `--archive`, the verdicts go to `--db`."""
    argv = schedule.command_argv("leads-reddit")
    assert "--archive" in argv and "data/jester.db" in argv
    assert schedule.command_db_role("leads-reddit") == "signals"


def test_a_relative_override_resolves_against_the_repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)
    got = schedule.resolve_task_db({"command": "ingest", "db": "data/mine.db"},
                                   main_db="data/jester.db")
    assert Path(got) == tmp_path / "data" / "mine.db"


def test_an_absolute_override_wins_over_the_role(tmp_path):
    custom = tmp_path / "custom.db"
    for command in ("signals", "ingest"):
        assert schedule.resolve_task_db(
            {"command": command, "db": str(custom)},
            main_db="data/jester.db") == str(custom), command


def test_a_declaration_can_name_the_archive_for_one_task(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        "tasks:\n"
        "  - task: T\n"
        "    command: signals\n"
        "    cadence: {at: '09:00'}\n"
        "    db: data/other.db\n",
        encoding="utf-8")

    spec = schedule.load_desired(path)[0]

    assert spec["db"] == "data/other.db"
    assert Path(schedule.resolve_task_db(spec, main_db="data/jester.db")) == (
        schedule.repo_root() / "data" / "other.db")


def test_the_signals_launcher_is_written_with_the_signals_archive():
    """The launcher is what the scheduler actually runs, so the resolved path
    has to reach the file and not just the declaration."""
    resolved = schedule.resolve_task_db({"command": "signals"}, main_db="data/jester.db")
    script = schedule.launcher_script(resolved, "config", "py.exe", "signals")

    assert f'--db "{resolved}"' in script
    assert '--db "data/jester.db"' not in script


def test_the_diagnostics_default_to_the_signals_archive(monkeypatch):
    """`jester health` and `jester signals` are both reads of the collector's
    own archive; defaulting them at the nuggets file is how the two schemas
    met in the first place."""
    from jester import cli

    seen = {}
    monkeypatch.setattr(cli, "cmd_health", lambda args: seen.update(health=args.db))
    monkeypatch.setattr(cli, "cmd_signals", lambda args: seen.update(signals=args.db))

    cli.main(["health"])
    cli.main(["signals"])

    assert seen["health"] == "data/signals-live.db"
    assert seen["signals"] == "data/signals-live.db"


def test_the_reddit_archive_default_is_unchanged(monkeypatch):
    """`--archive` names the nuggets database, whatever `--db` became.

    Asserted on the parsed arguments, not the source text: a string grep passes
    even if the option is registered on the wrong parser or the handler reads a
    different attribute.
    """
    from jester import cli

    seen = {}
    monkeypatch.setattr(
        cli, "cmd_signals",
        lambda args: seen.update(archive=args.archive, db=args.db))

    cli.main(["signals"])

    assert seen["archive"] == "data/jester.db"
    assert seen["db"] == "data/signals-live.db"
