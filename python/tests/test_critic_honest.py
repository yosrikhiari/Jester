"""A critic that did not answer leaves no score behind.

Before this, a failed critic call (bad JSON, timeout, a 5xx) handed back the
deterministic stand-in's numbers, and the archive stamped them with the real
model's name. The live archive holds 602 such ideas under
`openai/gpt-oss-120b`, 430 of them over the good-idea bar on a formula of the
nugget count.

Now the stand-in's answer carries its own name, an idea scored by it is filed
`needs_score` with its scores absent (new) or untouched (existing), and a later
run re-scores it. These tests drive the real Synthesizer, Critic and store over
a real database, because the bug lived in how those pieces handed off.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from jester.agents.critic import Critic, apply_scores, pending_scores, rescore_pending
from jester.agents.synthesizer import Synthesizer
from jester.cli import cmd_list, cmd_show
from jester.config import load_config
from jester.llm import (
    FakeCriticLLM,
    FakeSynthesizerLLM,
    GroqCriticLLM,
    GroqRateLimited,
    critic_answered,
)
from jester.models import Idea, IdeaScores, Nugget
from jester.store import get_idea, insert_idea, insert_nugget, open_db, unprocessed_nuggets

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
GOOD = json.dumps({"demand_signal": 8, "feasibility": 7, "competition": 4})


class _Client:
    """Groq-shaped transport: a reply, or an exception, per call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}}


def _critic(*replies):
    return GroqCriticLLM(client=_Client(*replies))


def _cfg():
    return load_config(REPO_CONFIG).thresholds


def _db(tmp_path, groups=3):
    db = open_db(str(tmp_path / "j.db"))
    for g in range(groups):
        for i in range(2):
            insert_nugget(db, Nugget(
                unique_key=f"t{g}-n{i}", platform="reddit", thread_id=f"thread-{g}",
                category="pain_point", extracted_insight=f"insight {g}.{i}", run_id="r"))
    return db


def _idea(**kw):
    base = dict(title="Backup Sentinel", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["t0-n0", "t0-n1"])
    base.update(kw)
    return Idea(**base)


def _rows(db):
    return [dict(r) for r in db.execute("SELECT * FROM ideas ORDER BY id")]


# ---- who answered --------------------------------------------------------

def test_a_failed_groq_critic_says_the_stand_in_answered():
    draft = _critic("not json").score(_idea())
    assert draft.model == FakeCriticLLM.name


def test_a_working_groq_critic_names_its_model():
    critic = _critic(GOOD)
    draft = critic.score(_idea())
    assert draft.model == "openai/gpt-oss-120b"
    assert critic_answered(draft, critic)


def test_a_stand_in_chosen_on_purpose_counts_as_an_answer():
    """critic_provider: fake is a configuration, not a failure."""
    critic = FakeCriticLLM()
    assert critic_answered(critic.score(_idea()), critic)


# ---- a new idea -----------------------------------------------------------

def test_a_new_idea_is_filed_unscored_when_the_critic_fails(tmp_path):
    db = _db(tmp_path, groups=3)
    syn = Synthesizer(db, _cfg())
    ideas = syn.run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")

    assert len(ideas) == 3, "the ideas are real; only their scores are missing"
    assert syn.unscored == 3
    for row in _rows(db):
        assert row["needs_score"] == 1
        assert row["overall"] is None and row["demand_signal"] is None
        assert row["feasibility"] is None and row["competition"] is None
        assert not row["critic_model"], (
            "an unscored idea must not name a critic: the old bug was naming "
            "the real model next to the stand-in's numbers")


def test_the_stand_in_formula_never_reaches_the_archive(tmp_path):
    """The fingerprint that found the 602: demand = 2 + nuggets, feasibility 5."""
    db = _db(tmp_path, groups=2)
    Synthesizer(db, _cfg()).run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")
    for row in _rows(db):
        assert not (row["feasibility"] == 5.0 and row["demand_signal"] == 4.0)


def test_a_critic_rate_limit_stops_the_run_and_keeps_that_idea(tmp_path):
    """Bug B4: the quota brake used to watch only the synthesizer, so a critic
    out of quota filed stand-in scores for every remaining idea."""
    db = _db(tmp_path, groups=5)
    critic = _critic(GroqRateLimited("429", retry_after=3600))
    syn = Synthesizer(db, _cfg())
    ideas = syn.run(FakeSynthesizerLLM(), critic, run_id="r")

    assert syn.stopped_on_quota and syn.stopped_early
    assert len(ideas) == 1 and ideas[0].needs_score
    assert critic._client.calls == 1, "stopped at the first refusal"
    assert len(unprocessed_nuggets(db)) == 8, "the groups not reached stay queued"


def test_a_configured_stand_in_still_scores_normally(tmp_path):
    db = _db(tmp_path, groups=2)
    syn = Synthesizer(db, _cfg())
    syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r")
    assert syn.unscored == 0
    for row in _rows(db):
        assert row["needs_score"] == 0
        assert row["critic_model"] == FakeCriticLLM.name
        assert row["overall"] is not None


# ---- an existing idea -------------------------------------------------------

def test_a_failed_re_score_keeps_the_old_scores_and_flags_it(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    idea = _idea(scores=IdeaScores(demand_signal=8, feasibility=7, competition=4,
                                   overall=6.7),
                 critic_model="openai/gpt-oss-120b", status="reviewed")
    idea.id = insert_idea(db, idea)

    Critic(db, _cfg()).score_by_id(idea.id, _critic("not json"))
    row = get_idea(db, idea.id)
    assert (row["demand_signal"], row["feasibility"], row["overall"]) == (8, 7, 6.7)
    assert row["critic_model"] == "openai/gpt-oss-120b"
    assert row["needs_score"] == 1
    assert row["status"] == "reviewed", "the operator's marking survives"


def test_rescore_pending_scores_the_oldest_and_clears_the_flag(tmp_path):
    db = _db(tmp_path, groups=3)
    Synthesizer(db, _cfg()).run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")
    assert pending_scores(db) == 3

    res = rescore_pending(db, _cfg(), _critic(GOOD), limit=2)
    assert res == {"scored": 2, "still_waiting": 1, "stopped_on_quota": False}
    first = _rows(db)[0]
    assert first["needs_score"] == 0
    assert first["critic_model"] == "openai/gpt-oss-120b"
    assert first["overall"] is not None


def test_rescore_pending_stops_on_the_critics_quota(tmp_path):
    db = _db(tmp_path, groups=3)
    Synthesizer(db, _cfg()).run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")
    critic = _critic(GroqRateLimited("429", retry_after=60))
    res = rescore_pending(db, _cfg(), critic, limit=10)
    assert res["stopped_on_quota"] and res["scored"] == 0
    assert critic._client.calls == 1
    assert res["still_waiting"] == 3


def test_rescore_limit_zero_does_nothing(tmp_path):
    db = _db(tmp_path, groups=1)
    Synthesizer(db, _cfg()).run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")
    critic = _critic(GOOD)
    assert rescore_pending(db, _cfg(), critic, limit=0)["still_waiting"] == 1
    assert critic._client.calls == 0


def test_apply_scores_on_a_new_idea_reports_the_failure():
    idea = _idea()
    assert apply_scores(idea, _critic("not json"), _cfg()) is False
    assert idea.needs_score and idea.scores.overall is None


# ---- readers do not crash on an unscored idea -----------------------------

def test_list_and_show_print_unscored_ideas(tmp_path, capsys):
    path = str(tmp_path / "j.db")
    db = _db(tmp_path, groups=1)
    Synthesizer(db, _cfg()).run(FakeSynthesizerLLM(), _critic("not json"), run_id="r")
    cmd_list(SimpleNamespace(db=path))
    cmd_show(SimpleNamespace(db=path, id=1))
    out = capsys.readouterr().out
    assert "overall=unscored" in out
    assert "needs_score" in out


# ---- the console's draft button ---------------------------------------------

def test_console_draft_refuses_a_stand_in_score(tmp_path, monkeypatch):
    """Bug B5: the button saved whatever came back. Now it says so instead."""
    import jester.llm as llm_mod
    from jester.console.api import ConsoleAPI
    from jester.store import list_clusters, replace_clusters

    path = str(tmp_path / "c.db")
    db = open_db(path)
    for k in ("n1", "n2"):
        insert_nugget(db, Nugget(unique_key=k, platform="reddit", thread_id="t1",
                                 raw_text=f"body {k}", extracted_insight=f"i {k}"))
    replace_clusters(db, "r", [{
        "label": "l", "problem_statement": "ps", "size": 2, "n_nuggets": 2,
        "n_authors": 2, "authors_unknown": 0, "n_threads": 1,
        "platforms": '["reddit"]', "nugget_keys": json.dumps(["n1", "n2"]),
        "coherence": 0.9, "embedding_model": "nomic-embed-text",
        "single_author": False, "single_thread": False,
        "members": [{"nugget_key": "n1", "chunk_index": 0, "text": "x", "similarity": 0.9}],
    }])
    cid = list_clusters(db)[0]["id"]

    monkeypatch.setattr(llm_mod, "select_synthesizer_llm", lambda cfg: FakeSynthesizerLLM())
    monkeypatch.setattr(llm_mod, "select_critic_llm", lambda cfg: _critic("not json"))
    res = ConsoleAPI(path, str(REPO_CONFIG)).cluster_generate_idea(cid)

    assert res["ok"] is False
    assert "did not answer" in res["error"]
    assert db.execute("SELECT COUNT(*) FROM cluster_ideas").fetchone()[0] == 0


def test_a_merge_after_the_critic_ran_out_flags_without_asking_again(tmp_path, monkeypatch):
    """The merge path re-scores the older idea. With the critic already out
    of quota that call would be refused too, so the idea is only flagged."""
    from jester.agents import synthesizer as synth_mod

    db = _db(tmp_path, groups=1)
    old = _idea(supporting_nuggets=["x"], scores=IdeaScores(demand_signal=8, feasibility=7,
                                                             competition=4, overall=6.7),
                critic_model="openai/gpt-oss-120b")
    old.id = insert_idea(db, old)
    critic = _critic(GroqRateLimited("429", retry_after=60))
    monkeypatch.setattr(synth_mod, "find_duplicate", lambda vec, idea, thr: (old.id, 0.99))
    syn = Synthesizer(db, _cfg())
    syn.run(FakeSynthesizerLLM(), critic, run_id="r", idea_vector=object())
    row = get_idea(db, old.id)
    assert critic._client.calls == 1, "asked once for the new idea, not again for the merge"
    assert row["needs_score"] == 1
    assert row["overall"] == 6.7, "the old score is kept until a real re-score"
    assert syn.stopped_on_quota
