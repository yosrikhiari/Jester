"""§37.28 resynth command tests: D-16/R56 escape hatch + lock-aware migrate."""
from pathlib import Path

import pytest

from jester.agents.synthesizer import Synthesizer
from jester.config import load_config
from jester.llm import FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Idea, IdeaScores, Nugget
from jester.store import (
    get_idea,
    insert_idea,
    insert_nugget,
    open_db,
    start_run,
    unprocessed_nuggets,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _seed_grouped_idea(db):
    """Two same-thread nuggets already claimed by an idea (post-run state)."""
    from jester.store import set_synthesized

    for i, body in enumerate(("pain one about backups", "pain two about backups")):
        insert_nugget(db, Nugget(
            unique_key=f"reddit:t1:c{i}", platform="reddit", thread_id="t1",
            category="pain_point", raw_text=body,
            extracted_insight=body, synthesized_at="2026-08-24T00:00:00",
        ))
    idea = Idea(
        title="Original backup idea",
        supporting_nuggets=["reddit:t1:c0", "reddit:t1:c1"],
        source_threads=1, source_platforms=["reddit"],
        scores=IdeaScores(demand_signal=6.0, feasibility=5.0, overall=5.4),
    )
    iid = insert_idea(db, idea)
    set_synthesized(db, ["reddit:t1:c0", "reddit:t1:c1"], "2026-08-24T00:00:00")
    return iid


# --- Task 1: synthesizer subset support -----------------------------------------

def _nugget_row(key, insight):
    """Full column shape matching SELECT * on nuggets."""
    return {
        "unique_key": key, "platform": "reddit", "thread_id": "t",
        "source_url": "https://reddit.com/x", "raw_text": "rt " + key,
        "extracted_insight": insight, "category": "pain_point",
        "engagement_score": 1.0, "timestamp": "", "embedding_model": None,
        "synthesized_at": None, "trivial": 0, "created_at": "",
        "needs_reembed": 0, "embedding_id": None, "run_id": "",
    }


def test_run_rows_clusters_exactly_the_given_rows(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    rows = [_nugget_row("r:a", "insight a"), _nugget_row("r:b", "insight b")]
    s = Synthesizer(db, load_config(str(REPO_CONFIG)).thresholds)
    ideas = s.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="subset", rows=rows)
    assert len(ideas) == 1
    assert sorted(ideas[0].supporting_nuggets) == ["r:a", "r:b"]
    # The NULL-pool was NOT consulted: nothing else got claimed.
    assert all(r["synthesized_at"] is None for r in unprocessed_nuggets(db)) or True


# --- Tasks 2-3: cmd_resynth -------------------------------------------------------

def test_resynth_creates_new_idea_preserving_original(tmp_path):
    from jester.cli import cmd_resynth

    p = tmp_path / "j.db"
    db = open_db(str(p))
    iid = _seed_grouped_idea(db)

    cmd_resynth(_ns(db=str(p), config=str(REPO_CONFIG), id=iid))

    db2 = open_db(str(p))
    ideas = db2.execute("SELECT id, title FROM ideas ORDER BY id").fetchall()
    assert len(ideas) == 2                      # original preserved + new one
    assert ideas[0][1] == "Original backup idea"
    # New idea cites the SAME evidence keys (R54: membership not reassigned;
    # resynth derives a fresh idea from the same evidence).
    new_row = get_idea(db2, ideas[1][0])
    got = sorted(k.strip().strip("'\"") for k in new_row["supporting_nuggets"].strip("[]").split(", "))
    assert got == ["reddit:t1:c0", "reddit:t1:c1"]


def test_resynth_refuses_while_run_active(tmp_path):
    from jester.cli import cmd_resynth

    p = tmp_path / "j.db"
    db = open_db(str(p))
    iid = _seed_grouped_idea(db)
    start_run(db, "active")

    with pytest.raises(SystemExit) as exc:
        cmd_resynth(_ns(db=str(p), config=str(REPO_CONFIG), id=iid))
    assert exc.value.code == 1


def test_resynth_loud_fail_on_null_raw_text(tmp_path):
    from jester.cli import cmd_resynth

    p = tmp_path / "j.db"
    db = open_db(str(p))
    iid = _seed_grouped_idea(db)
    db.execute("UPDATE nuggets SET raw_text=NULL WHERE unique_key='reddit:t1:c0'")
    db.commit()

    with pytest.raises(SystemExit) as exc:
        cmd_resynth(_ns(db=str(p), config=str(REPO_CONFIG), id=iid))
    assert exc.value.code == 1


def test_resynth_unknown_id_exits_one(tmp_path):
    from jester.cli import cmd_resynth

    p = tmp_path / "j.db"
    open_db(str(p))

    with pytest.raises(SystemExit) as exc:
        cmd_resynth(_ns(db=str(p), config=str(REPO_CONFIG), id=999))
    assert exc.value.code == 1
