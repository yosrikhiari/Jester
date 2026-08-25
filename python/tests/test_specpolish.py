"""Spec-polish tests (§37.15): version tags, comparability banner, config keys."""
from pathlib import Path

import pytest

from jester.config import ConfigError, Thresholds
from jester.models import Idea, IdeaScores, Nugget
from jester.store import get_runs, insert_idea, insert_nugget, open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: config keys ---------------------------------------------------------

def test_new_threshold_defaults():
    thr = Thresholds()
    assert thr.competitor_ttl_days == 30      # R53 default per §3.3
    assert thr.critic_web_rate_limit == 6     # provisional until D-1 lands
    assert thr.critic_web_daily_budget == 50  # provisional until D-1 lands


@pytest.mark.parametrize("field,bad", [
    ("competitor_ttl_days", 0),
    ("competitor_ttl_days", 366),
    ("critic_web_rate_limit", 0),
    ("critic_web_daily_budget", -1),
])
def test_new_threshold_ranges_reject_bad_values(field, bad):
    with pytest.raises(ConfigError):
        Thresholds(**{field: bad}).validate()


# --- Task 2: version tags in runs.models_used --------------------------------------

def test_models_used_records_prompt_and_rubric_versions(tmp_path):
    from jester.cli import cmd_run
    from jester.llm import PROMPT_VERSIONS, RUBRIC_VERSION

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="ver-run"))

    import json
    r = get_runs(open_db(str(p)))[0]
    record = json.loads(r["models_used"])
    assert isinstance(record, dict)
    assert record["prompt_versions"] == PROMPT_VERSIONS
    assert set(record["prompt_versions"]) == {"extractor", "synthesizer", "critic"}
    assert record["rubric_version"] == RUBRIC_VERSION
    assert isinstance(record["rubric_version"], int)


# --- Task 3: critic-model comparability ----------------------------------------------

def _seed_idea(db, title, overall, critic_model):
    idea = Idea(
        title=title,
        supporting_nuggets=[],
        source_platforms=[],
        scores=IdeaScores(overall=overall),
        critic_model=critic_model,
    )
    return insert_idea(db, idea)


def test_list_banners_multi_model_archive(tmp_path, capsys):
    from jester.cli import cmd_list

    p = tmp_path / "j.db"
    db = open_db(str(p))
    _seed_idea(db, "old model", 8.0, "claude-opus-4-7")
    _seed_idea(db, "new model", 7.0, "qwen3-32b")

    cmd_list(_ns(db=str(p)))
    out = capsys.readouterr().out
    assert "archive spans 2 critic models" in out


def test_list_single_model_no_banner(tmp_path, capsys):
    from jester.cli import cmd_list

    p = tmp_path / "j.db"
    db = open_db(str(p))
    _seed_idea(db, "a", 8.0, "claude-opus-4-7")
    _seed_idea(db, "b", 6.5, "claude-opus-4-7")

    cmd_list(_ns(db=str(p)))
    out = capsys.readouterr().out
    assert "archive spans" not in out
    # Grouped ordering: within the single model, higher overall first.
    assert out.index("a") < out.index("b")


def test_seed_nugget_helper_unused_guard():
    # Keeps import surface honest; remove if unused later.
    assert Nugget is not None
