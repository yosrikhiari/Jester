"""M1.6 smoke tests (§37.9): E2E mock run + self-verifying gates."""
import shutil
import subprocess
from pathlib import Path

import pytest

from jester.models import Nugget
from jester.store import finish_run, get_run, insert_nugget, open_db, start_run

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _seed_green_smoke(db):
    """A completed, duration-stamped run + one non-trivial pain-point nugget."""
    rid = start_run(db, "smoke-green", origin="manual")
    finish_run(db, "smoke-green", status="completed", duration_actual_s=1.5)
    insert_nugget(db, Nugget(unique_key="reddit:x:c1", platform="reddit",
                             category="pain_point",
                             extracted_insight="40GB CSV export freezes the app",
                             trivial=False))
    return rid


# --- Task 1: evaluate_smoke ----------------------------------------------------

def test_evaluate_smoke_reports_all_gates_on_virgin_db(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    failures = evaluate_smoke(db)
    assert any("run summary" in f for f in failures)
    assert any("non-trivial" in f for f in failures)


def test_evaluate_smoke_clean_on_seeded_db(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_green_smoke(db)
    assert evaluate_smoke(db) == []


def test_evaluate_smoke_flags_missing_duration(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "no-dur")
    finish_run(db, "no-dur", status="completed")  # no duration fields
    insert_nugget(db, Nugget(unique_key="k1", category="pain_point",
                             extracted_insight="x", trivial=False))
    failures = evaluate_smoke(db)
    assert any("duration" in f for f in failures)


# --- Task 2: happy path ---------------------------------------------------------

def test_cmd_smoke_passes_end_to_end(tmp_path, capsys):
    from jester.cli import cmd_smoke

    db_path = tmp_path / "smoke.db"
    cmd_smoke(_ns(db=str(db_path), config=str(REPO_CONFIG), live=False))

    out = capsys.readouterr().out
    assert "SMOKE PASS" in out
    # Both M1.6 gates are actually evaluated, not just claimed:
    db = open_db(str(db_path))
    runs = get_run_rows(db_path)
    assert runs, "run summary must be recorded"
    assert runs[0]["duration_actual_s"] is not None
    n_nontrivial = db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE trivial=0 AND category='pain_point'"
    ).fetchone()[0]
    assert n_nontrivial >= 1


def get_run_rows(db_path):
    from jester.store import get_runs
    return get_runs(open_db(str(db_path)))


# --- Task 3: --live refusal ------------------------------------------------------

def test_cmd_smoke_refuses_live_mode(tmp_path, capsys):
    from jester.cli import cmd_smoke

    with pytest.raises(SystemExit) as exc:
        cmd_smoke(_ns(db=str(tmp_path / "unused.db"), config=str(REPO_CONFIG), live=True))
    assert exc.value.code == 1
    assert "--live" in capsys.readouterr().out


# --- Task 4: scripts/smoke.sh -----------------------------------------------------

def _working_bash():
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        subprocess.run([bash, "-c", "true"], timeout=5, check=True)
        return bash
    except Exception:
        return None


def test_smoke_script_syntax_valid():
    bash = _working_bash()
    if not bash:
        pytest.skip("no working bash on this machine (WSL stub)")
    script = Path(__file__).resolve().parents[2] / "scripts" / "smoke.sh"
    proc = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
