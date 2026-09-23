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


def test_parity_probe_asks_about_this_databases_collection(tmp_path, monkeypatch):
    """Collections are namespaced per database. Probing the bare "nuggets"
    name reported 0 points against a healthy 1,771-point store and raised a
    mismatch finding for a system that was fine."""
    from jester.cli import collection_for, evaluate_doctor
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db_path = tmp_path / "probe.db"
    db = open_db(str(db_path))
    insert_nugget(db, Nugget(unique_key="k1", platform="reddit", raw_text="x"))

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://qdrant.test:6333")
    asked = {}

    def fake_count(url, collection=None):
        asked["collection"] = collection
        return 1  # matches the single row, so no finding is raised

    monkeypatch.setattr("jester.cli._qdrant_point_count", fake_count)
    findings = evaluate_doctor(db)

    assert asked["collection"] == collection_for(str(db_path)), asked
    assert not [f for f in findings if "parity" in f], findings


def test_parity_mismatch_names_the_collection(tmp_path, monkeypatch):
    """A mismatch must say WHICH store disagrees — with per-database
    namespacing there is more than one."""
    from jester.cli import evaluate_doctor
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(str(tmp_path / "probe.db"))
    insert_nugget(db, Nugget(unique_key="k1", platform="reddit", raw_text="x"))
    monkeypatch.setenv("JESTER_QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setattr("jester.cli._qdrant_point_count", lambda url, c=None: 0)

    findings = [f for f in evaluate_doctor(db) if "parity" in f]
    assert findings, "a real mismatch must still be reported"
    assert "nuggets__probe" in findings[0], findings[0]


# ---- a run that never starts never fails -----------------------------------

def _run_at(db, hours_ago, run_id="r1"):
    from datetime import datetime, timedelta, timezone

    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    db.execute("DELETE FROM runs")
    db.execute("INSERT INTO runs (run_id, status, started_at) VALUES (?, ?, ?)",
               (run_id, "completed", ts))
    db.commit()


def test_silence_is_a_finding(tmp_path):
    """Every other check reacts to something going wrong. This one reacts to
    nothing happening, which the notifier cannot see: JESTER_NOTIFY_CMD fires
    when a run fails, and a run that never started never fails.

    That is the exact shape Task Scheduler produces on a laptop — refuses to
    start on battery, never retries a slot missed while asleep. The symptom is
    a quiet gap that looks identical to a quiet week."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 30, "r-gone")
    found = _schedule_silence(db)
    assert found and "no run in 30h" in found[0]
    assert "r-gone" in found[0], "the finding must name the last run"


def test_a_run_that_drifted_an_hour_does_not_cry_wolf(tmp_path):
    """26 hours, not 24, so a run that starts late — or catches up after the
    machine woke — is not reported every morning."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 25, "r-late")
    assert _schedule_silence(db) == []


def test_a_fresh_checkout_is_not_scolded(tmp_path):
    """Never having run is a real state, but it is also what a clean clone
    looks like, and telling someone their schedule is silent before they have
    installed one is noise."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    assert _schedule_silence(open_db(str(tmp_path / "d.db"))) == []


def test_silence_reaches_the_doctor_findings(tmp_path):
    from jester.cli import evaluate_doctor
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 40, "r-old")
    res = evaluate_doctor(db)
    findings = res["findings"] if isinstance(res, dict) else res
    assert any("no run in" in str(f) for f in findings)
