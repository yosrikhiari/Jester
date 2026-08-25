"""jester doctor tests (§37.11): health findings + exit-code contract."""
from pathlib import Path

import pytest

from jester.models import Idea, IdeaScores, Nugget
from jester.store import (
    enqueue_batch,
    finish_run,
    insert_idea,
    insert_nugget,
    mark_batch_failed,
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


def _seed_healthy(db):
    start_run(db, "h1")
    finish_run(db, "h1", status="completed", duration_actual_s=1.0)
    insert_nugget(db, Nugget(unique_key="reddit:a:c1", platform="reddit",
                             category="pain_point", extracted_insight="x",
                             needs_reembed=False))
    insert_idea(db, Idea(title="t", supporting_nuggets=["reddit:a:c1"],
                         source_platforms=[], scores=IdeaScores()))


# --- Task 1: healthy DBs --------------------------------------------------------

def test_fresh_db_is_healthy(tmp_path):
    from jester.cli import evaluate_doctor

    assert evaluate_doctor(open_db(str(tmp_path / "j.db"))) == []


def test_seeded_healthy_db_has_no_findings(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    _seed_healthy(db)
    assert evaluate_doctor(db) == []


# --- Task 2: each finding fires independently ------------------------------------

def test_stale_running_row_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "crashed")  # never finished
    findings = evaluate_doctor(db)
    assert any("running" in f for f in findings)


def test_failed_batch_detected_with_requeue_hint(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)
    findings = evaluate_doctor(db)
    assert any("failed batch" in f and "requeue" in f for f in findings)


def test_reembed_backlog_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(unique_key="k1", category="pain_point",
                             extracted_insight="x", needs_reembed=True))
    findings = evaluate_doctor(db)
    assert any("reembed_backlog" in f for f in findings)


def test_orphaned_citation_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    insert_idea(db, Idea(title="ghost", supporting_nuggets=["missing:key"],
                         source_platforms=[], scores=IdeaScores()))
    findings = evaluate_doctor(db)
    assert any("orphaned" in f for f in findings)


# --- Task 3: cmd_doctor CLI contract ----------------------------------------------

def test_cmd_doctor_clean_exits_zero_and_prints_ok(tmp_path, capsys):
    from jester.cli import cmd_doctor

    p = tmp_path / "j.db"
    db = open_db(str(p))
    _seed_healthy(db)

    cmd_doctor(_ns(db=str(p)))  # must not raise SystemExit
    assert "doctor OK" in capsys.readouterr().out


def test_cmd_doctor_unhealthy_exits_one_with_findings(tmp_path, capsys):
    from jester.cli import cmd_doctor

    p = tmp_path / "j.db"
    db = open_db(str(p))
    start_run(db, "stale-run")

    with pytest.raises(SystemExit) as exc:
        cmd_doctor(_ns(db=str(p)))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "running" in out
