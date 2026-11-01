"""V3-17: a crash closes the run THIS process opened, and nothing else.

`_record_run_crash` used to write to `active_run(db)` — the one row still
marked 'running', whoever opened it. A failing `export` while a live run
existed therefore stamped that run `error` with the export's traceback: the
console showed a run that had died of an exception it never raised, and the
real run kept working.

Only a command that opened a run row may close one. These tests drive the
real `main`, because the crash recorder is wired into its catch-all.
"""
import pytest

import jester.cli as cli
import jester.provenance as provenance
from jester.store import open_db, start_run


@pytest.fixture(autouse=True)
def _no_inherited_owned_run(monkeypatch):
    """Ownership is module state: no test may inherit another's run id."""
    monkeypatch.setattr(cli, "_OWNED_RUN_ID", None)


def _row(db_path):
    db = open_db(str(db_path))
    try:
        return db.execute("SELECT run_id, status, error FROM runs").fetchone()
    finally:
        db.close()


def test_a_failing_export_leaves_a_live_run_alone(tmp_path, offline_config,
                                                 monkeypatch):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    start_run(db, "someone-elses-run")
    db.close()

    def _boom(*_a, **_kw):
        raise RuntimeError("export exploded")

    monkeypatch.setattr(cli, "export_csv", _boom)
    with pytest.raises(RuntimeError, match="export exploded"):
        cli.main(["export", "--db", str(db_path), "--out", str(tmp_path / "out"),
                  "--config", str(offline_config)])

    run_id, status, error = _row(db_path)
    assert run_id == "someone-elses-run"
    assert status == "running", "this process did not open that run, so it cannot close it"
    assert error is None


def test_a_failing_run_marks_the_run_it_owns(tmp_path, offline_config, monkeypatch):
    db_path = tmp_path / "j.db"

    def _boom(_db):
        raise RuntimeError("prune exploded")

    # Right after start_run: the run row exists and this process owns it.
    monkeypatch.setattr(provenance, "prune_calls", _boom)
    with pytest.raises(RuntimeError, match="prune exploded"):
        cli.main(["run", "--config", str(offline_config), "--db", str(db_path),
                  "--run", "owned-run"])

    run_id, status, error = _row(db_path)
    assert run_id == "owned-run"
    assert status == "error"
    assert "RuntimeError: prune exploded" in error
    assert cli._OWNED_RUN_ID is None, "the marker is spent once the crash is recorded"


def test_a_failing_signals_command_leaves_an_unrelated_running_row_alone(tmp_path,
                                                                       monkeypatch):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    start_run(db, "live-run")
    db.close()

    from jester.signals import filters as sigf

    def _boom(_path=None):
        raise RuntimeError("rules exploded")

    monkeypatch.setattr(sigf, "load_rules", _boom)
    with pytest.raises(RuntimeError, match="rules exploded"):
        cli.main(["signals", "export", "--db", str(db_path)])

    run_id, status, error = _row(db_path)
    assert (run_id, status, error) == ("live-run", "running", None)
