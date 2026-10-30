"""M1.6 smoke tests (§37.9): E2E mock run + self-verifying gates."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jester.models import Idea, IdeaScores, Nugget
from jester.store import (
    finish_run,
    get_run,
    insert_idea,
    insert_nugget,
    open_db,
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


def _seed_run_and_nugget(db):
    """A completed, duration-stamped run + one non-trivial pain-point nugget."""
    rid = start_run(db, "smoke-green", origin="manual")
    finish_run(db, "smoke-green", status="completed", duration_actual_s=1.5)
    insert_nugget(db, Nugget(unique_key="reddit:x:c1", platform="reddit",
                             category="pain_point",
                             extracted_insight="40GB CSV export freezes the app",
                             trivial=False))
    return rid


def _seed_green_smoke(db):
    """The three things a clean smoke has to show: the run, the nugget, and a
    scored idea that cites it. The idea is not decoration — it is the one
    place the pipeline proves an output it produced can be traced back to the
    evidence it came from."""
    rid = _seed_run_and_nugget(db)
    insert_idea(db, Idea(
        title="Streaming CSV export",
        supporting_nuggets=["reddit:x:c1"],
        run_id=rid,
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=4.9),
    ))
    return rid


# --- Task 1: evaluate_smoke ----------------------------------------------------

def test_evaluate_smoke_reports_all_gates_on_virgin_db(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    failures = evaluate_smoke(db)
    assert any("run summary" in f for f in failures)
    assert any("non-trivial" in f for f in failures)
    assert any("scored idea" in f for f in failures)


def test_evaluate_smoke_clean_on_seeded_db(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_green_smoke(db)
    assert evaluate_smoke(db) == []


def test_evaluate_smoke_counts_a_non_trivial_nugget_of_any_category(tmp_path):
    """M1.6 asks for a non-trivial nugget, not a pain_point. The category
    requirement made the same command pass or fail on which extractor answered:
    the canned fixture is a `complaint` under qwen3 and a pain_point under the
    deterministic stand-in."""
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    rid = start_run(db, "smoke-green", origin="manual")
    finish_run(db, "smoke-green", status="completed", duration_actual_s=1.5)
    insert_nugget(db, Nugget(unique_key="reddit:x:c1", platform="reddit",
                             category="complaint",
                             extracted_insight="weekend deploys drop every config",
                             trivial=False))
    insert_idea(db, Idea(
        title="Deploy-safe config",
        supporting_nuggets=["reddit:x:c1"],
        run_id=rid,
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=4.9),
    ))
    assert evaluate_smoke(db) == []


def test_evaluate_smoke_flags_a_run_that_left_no_scored_idea(tmp_path):
    """A run can archive nuggets and still produce nothing a person can read.
    The smoke's job is the end-to-end promise, not "some rows exist"."""
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_run_and_nugget(db)
    failures = evaluate_smoke(db)
    assert any("scored idea" in f for f in failures)


def test_evaluate_smoke_flags_an_idea_the_critic_never_scored(tmp_path):
    """needs_score=1 is the honest "asked, got nothing back" state. It is a
    valid archive row, but it is not a scored idea."""
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_run_and_nugget(db)
    insert_idea(db, Idea(
        title="Unscored",
        supporting_nuggets=["reddit:x:c1"],
        needs_score=True,
        scores=IdeaScores(overall=None),
    ))
    failures = evaluate_smoke(db)
    assert any("scored idea" in f for f in failures)


def test_evaluate_smoke_flags_an_idea_citing_a_nugget_that_is_not_there(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_run_and_nugget(db)
    insert_idea(db, Idea(
        title="Dangling citation",
        supporting_nuggets=["reddit:x:c1", "reddit:x:gone"],
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=4.9),
    ))
    failures = evaluate_smoke(db)
    assert any("reddit:x:gone" in f for f in failures)


def test_evaluate_smoke_flags_an_idea_with_no_citations(tmp_path):
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_run_and_nugget(db)
    insert_idea(db, Idea(
        title="Unsourced",
        supporting_nuggets=[],
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=4.9),
    ))
    failures = evaluate_smoke(db)
    assert any("cites no nugget" in f for f in failures)


def test_evaluate_smoke_flags_unreadable_supporting_nuggets(tmp_path):
    """The column is JSON written by us, so unreadable is a bug, not input.
    It must fail the check rather than be skipped over as "no citations"."""
    from jester.cli import evaluate_smoke

    db = open_db(str(tmp_path / "j.db"))
    _seed_run_and_nugget(db)
    insert_idea(db, Idea(
        title="Corrupt citations",
        supporting_nuggets=["reddit:x:c1"],
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=4.9),
    ))
    db.execute("UPDATE ideas SET supporting_nuggets='not json'")
    db.commit()
    failures = evaluate_smoke(db)
    assert any("unreadable" in f for f in failures)


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
        "SELECT COUNT(*) FROM nuggets WHERE trivial=0"
    ).fetchone()[0]
    assert n_nontrivial >= 1
    # ...and the run ended in something a person can read, citing evidence
    # that is still there. This is the quickstart's promise, tested.
    ideas = db.execute(
        "SELECT id, supporting_nuggets, overall FROM ideas WHERE needs_score=0"
    ).fetchall()
    assert ideas, "the run must leave at least one scored idea"
    for idea_id, supporting, overall in ideas:
        assert overall is not None
        keys = json.loads(supporting)
        assert keys, f"idea #{idea_id} cites no nugget"
        for key in keys:
            assert db.execute(
                "SELECT 1 FROM nuggets WHERE unique_key=?", (key,)
            ).fetchone(), f"idea #{idea_id} cites missing nugget {key}"


def get_run_rows(db_path):
    from jester.store import get_runs
    return get_runs(open_db(str(db_path)))


def test_cmd_smoke_pins_the_stand_ins_and_the_local_store(tmp_path, monkeypatch, capsys):
    """A self-check must not be a connectivity probe. With a dead Qdrant URL
    in the environment and a role override pointing at a model server, the
    verdict still has to be about the code. Before this was pinned, the dead
    URL deferred both nuggets and the smoke failed with "no scored idea"."""
    from jester.cli import cmd_smoke

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("JESTER_ROLE_EXTRACTOR", "ollama:qwen3:8b")
    db_path = tmp_path / "smoke.db"

    cmd_smoke(_ns(db=str(db_path), config=str(REPO_CONFIG), live=False))

    assert "SMOKE PASS" in capsys.readouterr().out
    db = open_db(str(db_path))
    used = json.loads(db.execute(
        "SELECT models_used FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()[0])
    assert used["overrides"]["extractor"] == "fake"


def test_cmd_smoke_replaces_its_own_temporary_archive(tmp_path, monkeypatch, capsys):
    """The default path is disposable. A half-deleted archive left there must
    not turn the next check into a database error."""
    import jester.cli as cli

    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "jester-smoke.db").write_bytes(b"not a database")

    cli.cmd_smoke(_ns(db=None, config=str(REPO_CONFIG), live=False))

    assert "SMOKE PASS" in capsys.readouterr().out


def test_a_disposable_smoke_database_takes_its_vector_stores_with_it(tmp_path):
    from jester.cli import _reset_smoke_store, _smoke_store_paths

    db_path = tmp_path / "smoke.db"
    db_path.write_bytes(b"x")
    for derived in _smoke_store_paths(str(db_path))[1:]:
        Path(derived).mkdir()
        (Path(derived) / "meta.json").write_text("{}", encoding="utf-8")

    _reset_smoke_store(str(db_path))

    assert not any(Path(p).exists() for p in _smoke_store_paths(str(db_path)))


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
