"""R53: a re-score honours a web-verified competition verdict; a merge
re-checks only past the TTL.

Two defects drove this. `apply_scores` unconditionally wrote
`competition = c.competition` and `competitor_notes = None`, so a later run
whose critic finally answered (asked for demand, not competition) erased the
one field a search had verified. And `needs_recheck(last_checked_at,
ttl_days)` shipped with zero production callers: `rescore_after_merge`
re-ran the web search on every merge, so `competitor_ttl_days` was
console-editable dead config.

These tests drive the real store — open_db's migrations included, so the
T-A2 `ideas.competition_checked_at` column participates in the round-trip
it exists to support — with a scripted Groq client (the shape
test_critic_honest uses) plus a counting search transport, so "the check did
not run" is measured on the wire, not inferred from a note string.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from jester.agents.critic import Critic, apply_scores, row_to_idea
from jester.competitors import CompetitorChecker, CompetitorHit
from jester.config import Thresholds
from jester.idea_dedup import rescore_after_merge
from jester.llm import GroqCriticLLM
from jester.models import Idea, IdeaScores
from jester.scoring import compute_overall
from jester.store import get_idea, insert_idea, open_db, update_idea


def cfg(**kw):
    return Thresholds(**kw).validate()


@pytest.fixture()
def db(tmp_path):
    return open_db(str(tmp_path / "r53.db"))


class _Client:
    """Groq-shaped transport: one JSON reply per idea scored."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        return {"message": {"content": json.dumps(self.reply)}}


def _critic_reply(reply):
    return GroqCriticLLM(client=_Client(reply))


class CountingSearch:
    """A search transport that records how many calls reached it."""

    def __init__(self, hits=None):
        self.hits = hits or [CompetitorHit("Timeshift", "https://t.example")]
        self.calls = 0

    def search(self, query):
        self.calls += 1
        return list(self.hits)


def _checker(db, search):
    return CompetitorChecker(
        db, cfg(critic_web_daily_budget=10, competitor_search_provider="fake"),
        search, clock=lambda: 0.0, sleeper=lambda s: None,
    )


def _now():
    return datetime.now(timezone.utc).isoformat()


_VERIFIED_NOTES = "2 possible competitor(s): Timeshift <https://t.example>"


def _checked_idea(db, stamp, competition=4.0, notes=_VERIFIED_NOTES):
    """An archived idea the web step verified, with a controllable stamp."""
    idea = Idea(
        title="Config-safe auto-backup",
        problem_statement="Updates clobber config every weekend",
        proposed_solution="Snapshot before upgrade, one-click rollback",
        supporting_nuggets=["a", "b"],
        scores=IdeaScores(
            demand_signal=8.0, feasibility=7.0, competition=competition,
            overall=compute_overall(8.0, 7.0, competition),
        ),
        competitor_notes=notes,
        competition_checked=True,
    )
    # models.Idea pins no field for the stamp; the store reads it with getattr
    # and row_to_idea re-loads it, so setting it here is the write half of the
    # round-trip this file claims to test.
    idea.competition_checked_at = stamp
    idea.id = insert_idea(db, idea)
    return idea


def _no_competition_critic():
    return _critic_reply({"demand_signal": 9, "feasibility": 8})


# ---- the stamp round-trips through the store --------------------------------

def test_check_stamp_round_trips_through_insert_and_reload(db):
    idea = _checked_idea(db, stamp=_now())
    assert get_idea(db, idea.id)["competition_checked_at"] == idea.competition_checked_at


def test_a_rewritten_row_keeps_the_stamp_it_was_loaded_with(db):
    idea = _checked_idea(db, stamp=_now())
    stamp = idea.competition_checked_at
    loaded = row_to_idea(get_idea(db, idea.id))
    update_idea(db, loaded)  # the rescore_pending write path, no re-check involved
    assert get_idea(db, idea.id)["competition_checked_at"] == stamp


def test_never_checked_ideas_carry_a_null_stamp(db):
    idea = Idea(title="t", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["a"],
                scores=IdeaScores(demand_signal=8.0, feasibility=7.0))
    idea.id = insert_idea(db, idea)
    assert get_idea(db, idea.id)["competition_checked_at"] is None


# ---- (a) re-score WITHOUT competition preserves the web verdict --------------

def test_re_score_without_competition_preserves_the_verdict(db):
    stamp = _now()
    idea = _checked_idea(db, stamp=stamp)
    before = get_idea(db, idea.id)
    # The critic answered demand/feasibility but supplied no competition
    # number — the exact reply the R29 rule handles.
    Critic(db, cfg()).score(idea, _no_competition_critic())
    row = get_idea(db, idea.id)
    assert row["competition"] == 4.0, "the verified number must survive"
    assert row["competitor_notes"] == before["competitor_notes"], (
        "notes describe a search that ran; a re-score must not pretend it did not")
    assert row["competition_checked"] == 1
    assert row["overall"] == pytest.approx(compute_overall(9.0, 8.0, 4.0))
    assert row["demand_signal"] == 9.0 and row["feasibility"] == 8.0, (
        "the fresh demand/feasibility still land")
    assert row["competition_checked_at"] == stamp, "no check happened; stamp frozen"
    assert row["needs_score"] == 0


def test_re_score_without_competition_when_no_verdict_was_ever_on_file(db):
    """The preserve rule's guard: a previously UNCHECKED idea is unchanged --
    competition stays open and R29 imputes 5 in the overall, as it always did."""
    idea = Idea(title="t", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["a"],
                scores=IdeaScores(demand_signal=8.0, feasibility=7.0),
                competitor_notes=None, competition_checked=False)
    idea.id = insert_idea(db, idea)
    Critic(db, cfg()).score(
        idea, _critic_reply({"demand_signal": 2, "feasibility": 3}))
    row = get_idea(db, idea.id)
    assert row["competition"] is None
    assert row["competition_checked"] == 0
    assert row["competitor_notes"] is None
    assert row["competition_checked_at"] is None


# ---- (b) re-score WITH competition keeps today's semantics -------------------

def test_re_score_with_competition_is_fresh_semantics(db):
    idea = _checked_idea(db, stamp=_now())
    Critic(db, cfg()).score(
        idea, _critic_reply({"demand_signal": 7, "feasibility": 6, "competition": 3}))
    row = get_idea(db, idea.id)
    assert row["competition"] == 3.0
    assert row["competition_checked"] == 1
    assert row["competitor_notes"] is None, (
        "a model's number is not a web verdict - R29/R41: never fabricated")
    assert row["overall"] == pytest.approx(compute_overall(7.0, 6.0, 3.0))


# ---- (c) merge inside the TTL: the search never leaves the dock ---------------

def test_fresh_verdict_skips_the_web_check_on_merge(db):
    stamp = _now()
    idea = _checked_idea(db, stamp=stamp)
    search = CountingSearch()
    rescore_after_merge(db, idea.id, Critic(db, cfg()), _no_competition_critic(),
                        _checker(db, search))
    assert search.calls == 0, "a verdict inside its TTL window owes no search"
    row = get_idea(db, idea.id)
    assert row["competition"] == 4.0 and row["competitor_notes"] == _VERIFIED_NOTES
    assert row["competition_checked_at"] == stamp


def test_a_never_checked_idea_is_still_checked_on_merge(db):
    """Stamp NULL means the web step never produced a verdict for this row
    (a pre-R53 archive shape, or a check that never came back). needs_recheck
    treats NULL as stale, so the R49 check fires exactly as it did before."""
    idea = _checked_idea(db, stamp=None)
    db.execute("UPDATE ideas SET competitor_notes=NULL, competition_checked_at=NULL"
               " WHERE id=?", (idea.id,))
    db.commit()
    search = CountingSearch()
    rescore_after_merge(db, idea.id, Critic(db, cfg()), _no_competition_critic(),
                        _checker(db, search))
    assert search.calls == 1, "never-checked ideas do not graduate from the TTL"
    row = get_idea(db, idea.id)
    assert row["competitor_notes"] is not None, "the fresh verdict wrote its notes"
    assert row["competition_checked_at"] is not None, "a real check stamps itself"
    assert row["competition"] == 4.0  # one hit -> competition_from_hits


# ---- (d) merge past the TTL: exactly one check --------------------------------

def test_stale_verdict_is_rechecked_exactly_once(db):
    stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    idea = _checked_idea(db, stamp=stale)
    before = get_idea(db, idea.id)
    search = CountingSearch()
    rescore_after_merge(db, idea.id, Critic(db, cfg()), _no_competition_critic(),
                        _checker(db, search))
    assert search.calls == 1
    row = get_idea(db, idea.id)
    assert row["competition_checked_at"] > before["competition_checked_at"], (
        "the fresh check stamps itself")
    assert row["competition"] == 4.0  # 1 hit -> competition_from_hits


def test_a_critic_object_without_config_falls_back_to_always_check(db):
    """The knob is unreadable -> pre-TTL R49 behaviour: re-check every merge,
    never skip. A caller whose critic loses its config loses no correctness."""
    idea = _checked_idea(db, stamp=_now())
    search = CountingSearch()

    class NoConfigCritic:
        def score(self, idea, critic_llm):
            apply_scores(idea, critic_llm, cfg())
            update_idea(db, idea)
            return idea

    rescore_after_merge(db, idea.id, NoConfigCritic(), _no_competition_critic(),
                        _checker(db, search))
    assert search.calls == 1
