"""jester show tests (§37.6): R26 review-loop detail rendering."""
from pathlib import Path

import pytest

from jester.models import Idea, IdeaScores, Nugget
from jester.store import insert_idea, insert_nugget, open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _fixture(db):
    """Two archived nuggets + one idea citing both; competition unchecked."""
    insert_nugget(db, Nugget(unique_key="reddit:abc:c1", platform="reddit",
                             category="pain_point", raw_text="raw one",
                             extracted_insight="Backups vanish on update",
                             source_url="https://reddit.com/r/selfhosted/comments/abc"))
    insert_nugget(db, Nugget(unique_key="reddit:abc:c2", platform="reddit",
                             category="tool_request", raw_text="raw two",
                             extracted_insight="Wants auto-backup before updates",
                             source_url="https://reddit.com/r/selfhosted/comments/abc"))
    idea = Idea(
        title="Scheduled pre-update backups",
        problem_statement="Configs are lost when updates crash",
        proposed_solution="Snapshot before every package update",
        supporting_nuggets=["reddit:abc:c1", "reddit:abc:c2"],
        source_threads=1,
        source_platforms=["reddit"],
        scores=IdeaScores(demand_signal=7.0, feasibility=8.0, competition=None, overall=6.8),
        status="new",
    )
    return insert_idea(db, idea)


def test_cmd_show_renders_idea_and_citations(tmp_path, capsys):
    from jester.cli import cmd_show

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    iid = _fixture(db)

    cmd_show(_ns(db=str(db_path), id=iid))
    out = capsys.readouterr().out

    assert f"IDEA #{iid}" in out
    assert "Scheduled pre-update backups" in out
    assert "[new]" in out
    assert "overall=6.8" in out
    assert "demand=7.0" in out
    assert "feasibility=8.0" in out
    assert "competition=unchecked" in out  # R29: distinct rendering, never fabricated
    # Both citations resolved with insight + resolvable source URL (R26).
    assert "reddit:abc:c1" in out
    assert "Backups vanish on update" in out
    assert "reddit:abc:c2" in out
    assert "Wants auto-backup before updates" in out
    assert "https://reddit.com/r/selfhosted/comments/abc" in out


def test_cmd_show_renders_competitor_notes_when_present(tmp_path, capsys):
    from jester.cli import cmd_show

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    insert_nugget(db, Nugget(unique_key="k1", extracted_insight="x"))
    idea = Idea(title="Checked idea", supporting_nuggets=["k1"], source_platforms=[],
                scores=IdeaScores(demand_signal=8.0, feasibility=7.0, competition=6.0, overall=7.4),
                competition_checked=True, competitor_notes="BackupPro covers half of this")
    iid = insert_idea(db, idea)

    cmd_show(_ns(db=str(db_path), id=iid))
    out = capsys.readouterr().out

    assert "BackupPro covers half of this" in out
    assert "competition=6.0" in out


def test_cmd_show_exits_nonzero_on_unknown_id(tmp_path, capsys):
    from jester.cli import cmd_show

    db_path = tmp_path / "j.db"
    open_db(str(db_path))

    with pytest.raises(SystemExit) as exc:
        cmd_show(_ns(db=str(db_path), id=999))
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().out


def test_cmd_show_marks_broken_citation_without_crashing(tmp_path, capsys):
    from jester.cli import cmd_show

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    idea = Idea(title="Ghost cite", supporting_nuggets=["ghost:key"], source_platforms=[],
                scores=IdeaScores(overall=5.0))
    iid = insert_idea(db, idea)

    cmd_show(_ns(db=str(db_path), id=iid))
    out = capsys.readouterr().out

    assert "MISSING citation" in out
    assert "ghost:key" in out
