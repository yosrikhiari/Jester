"""M2.3 — critic web step (D-1), R44 idea dedup, R49 re-score after merge."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from jester.agents.critic import Critic
from jester.agents.synthesizer import Synthesizer
from jester.competitors import (
    CompetitorChecker,
    CompetitorHit,
    DuckDuckGoSearch,
    FakeSearch,
    NullSearch,
    QUOTA_PLATFORM,
    build_query,
    competition_from_hits,
    needs_recheck,
    render_notes,
    select_competitor_search,
)
from jester.config import ConfigError, Thresholds
from jester.idea_dedup import (
    apply_verdict,
    find_duplicate,
    idea_text,
    index_idea,
    merge_supporting,
)
from jester.llm import FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Idea, IdeaScores, Nugget
from jester.quota import quota_used, day_key
from jester.scoring import compute_overall
from jester.store import get_idea, insert_idea, insert_nugget, open_db
from jester.vector import VectorStore


@pytest.fixture()
def db(tmp_path):
    return open_db(str(tmp_path / "m23.db"))


def cfg(**kw):
    return Thresholds(**kw).validate()


def an_idea(title="Config-safe auto-backup", problem="Updates clobber config every weekend"):
    return Idea(
        title=title,
        problem_statement=problem,
        proposed_solution="Snapshot before upgrade, one-click rollback",
        supporting_nuggets=["k1", "k2"],
        scores=IdeaScores(demand_signal=8.0, feasibility=7.0),
    )


# ---- the check itself ------------------------------------------------------


def test_disabled_by_default_keeps_m22_behaviour(db):
    checker = CompetitorChecker(db, cfg())
    assert checker.enabled is False
    v = checker.check(an_idea())
    assert (v.checked, v.competition, v.notes) == (False, None, None)
    assert quota_used(db, QUOTA_PLATFORM, day_key()) == 0  # nothing charged


def test_a_successful_search_populates_notes_and_competition(db):
    search = FakeSearch({"auto-backup": [
        CompetitorHit("Timeshift", "https://t.example"),
        CompetitorHit("Restic", "https://r.example"),
        CompetitorHit("Borg", "https://b.example"),
    ]})
    v = CompetitorChecker(db, cfg(critic_web_daily_budget=10), search).check(an_idea())
    assert v.checked is True
    assert v.competition == 6.0            # 3 hits -> crowded-ish
    assert "Timeshift" in v.notes and "https://t.example" in v.notes


def test_zero_hits_is_a_result_not_a_failure(db):
    v = CompetitorChecker(db, cfg(critic_web_daily_budget=10), FakeSearch({"x": []})).check(
        an_idea(title="x")
    )
    # A search that ran and found nothing is evidence of an open field.
    assert v.checked is True
    assert v.competition == 2.0
    assert "no competitors found" in v.notes


def test_search_failure_leaves_competition_unchecked(db):
    search = FakeSearch(fail_on="auto-backup")
    v = CompetitorChecker(db, cfg(critic_web_daily_budget=10), search).check(an_idea())
    assert (v.checked, v.competition, v.notes) == (False, None, None)
    assert "search failed" in v.reason


def test_daily_budget_is_enforced_and_persisted(db):
    search = FakeSearch({"": [CompetitorHit("A", "https://a.example")]})
    checker = CompetitorChecker(db, cfg(critic_web_daily_budget=2), search,
                                clock=lambda: 0.0, sleeper=lambda s: None)
    assert checker.check(an_idea()).checked is True
    assert checker.check(an_idea()).checked is True
    third = checker.check(an_idea())
    assert third.checked is False and "budget exhausted" in third.reason
    assert quota_used(db, QUOTA_PLATFORM, day_key()) == 2


def test_rate_limit_paces_calls(db):
    slept = []
    now = [0.0]
    checker = CompetitorChecker(
        db, cfg(critic_web_daily_budget=10, critic_web_rate_limit=6),
        FakeSearch({"": []}),
        clock=lambda: now[0], sleeper=lambda s: slept.append(s),
    )
    checker.check(an_idea())
    checker.check(an_idea())          # immediately after: must wait 60/6 = 10s
    assert slept and slept[0] == pytest.approx(10.0)


def test_competition_mapping_is_monotonic():
    counts = [0, 1, 2, 3, 5, 6, 9, 10, 40]
    scores = [competition_from_hits([CompetitorHit("t", "u")] * n) for n in counts]
    assert scores == sorted(scores)
    assert all(1.0 <= s <= 10.0 for s in scores)


def test_notes_never_invent_a_competitor():
    assert "no competitors found" in render_notes([], "q")
    notes = render_notes([CompetitorHit("Real Tool", "https://real.example")], "q")
    assert "Real Tool" in notes and "https://real.example" in notes


def test_query_is_built_from_the_idea():
    q = build_query(an_idea())
    assert "Config-safe auto-backup" in q


def test_provider_selection_and_validation():
    assert isinstance(select_competitor_search(cfg()), NullSearch)
    assert isinstance(select_competitor_search(cfg(competitor_search_provider="fake")), FakeSearch)
    assert isinstance(
        select_competitor_search(cfg(competitor_search_provider="duckduckgo")),
        DuckDuckGoSearch,
    )
    with pytest.raises(ConfigError, match="competitor_search_provider"):
        cfg(competitor_search_provider="bing")


def test_duckduckgo_parses_result_anchors_without_network():
    html = (
        '<div><a class="result__a" href="https://one.example">One <b>Tool</b></a></div>'
        '<div><a class="result__a" href="https://two.example">Two Tool</a></div>'
    )
    hits = DuckDuckGoSearch(opener=lambda url: html).search("anything")
    assert [h.title for h in hits] == ["One Tool", "Two Tool"]
    assert hits[0].url == "https://one.example"


def test_duckduckgo_markup_drift_yields_zero_hits_not_garbage():
    hits = DuckDuckGoSearch(opener=lambda url: "<html>redesigned</html>").search("q")
    assert hits == []


def test_recheck_ttl_r53():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    stale = (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    assert needs_recheck(fresh, 30, now) is False
    assert needs_recheck(stale, 30, now) is True
    assert needs_recheck(None, 30, now) is True
    assert needs_recheck("not a date", 30, now) is True


def test_apply_verdict_recomputes_overall_and_imputes_on_failure():
    from jester.competitors import CompetitorVerdict

    idea = an_idea()
    apply_verdict(idea, CompetitorVerdict(4.0, "notes", True, "2 hits"))
    assert idea.competition_checked is True
    assert idea.scores.overall == pytest.approx(compute_overall(8.0, 7.0, 4.0))

    idea2 = an_idea()
    apply_verdict(idea2, CompetitorVerdict(None, None, False, "search failed"))
    assert idea2.competition_checked is False
    assert idea2.competitor_notes is None
    # R29: unchecked imputes 5 -> 0.4*8 + 0.3*7 + 1.8
    assert idea2.scores.overall == pytest.approx(0.4 * 8.0 + 0.3 * 7.0 + 1.8)


# ---- R44 idea-level dedup --------------------------------------------------


def test_find_duplicate_matches_a_near_identical_idea():
    vector = VectorStore.in_memory(collection="ideas")
    stored = an_idea()
    stored.id = 7
    index_idea(vector, stored)

    same = an_idea()  # identical text -> cosine 1.0
    assert find_duplicate(vector, same, 0.87)[0] == 7
    # A high enough bar still rejects a different idea.
    other = an_idea(title="Kubernetes cost dashboard", problem="Nobody knows the spend")
    assert find_duplicate(vector, other, 0.99) is None


def test_merge_supporting_unions_and_reports_growth(db):
    idea = an_idea()
    idea.supporting_nuggets = ["a", "b"]
    idea.id = insert_idea(db, idea)

    grew, added = merge_supporting(db, idea.id, ["b", "c"])
    assert grew is True and added == ("c",)
    assert json.loads(get_idea(db, idea.id)["supporting_nuggets"]) == ["a", "b", "c"]

    # Re-merging the same keys is a no-op, so a repeated run does not churn.
    assert merge_supporting(db, idea.id, ["a", "b", "c"]) == (False, ())


def test_idea_text_uses_title_and_problem():
    assert "auto-backup" in idea_text(an_idea())
    assert "clobber" in idea_text(an_idea())


# ---- end to end through the synthesizer ------------------------------------


def _seed_cluster(db, thread, bodies, run_id="r1"):
    for i, body in enumerate(bodies):
        insert_nugget(db, Nugget(
            unique_key=f"{thread}:c{i}", platform="reddit", thread_id=thread,
            source_url=f"https://reddit.test/{thread}", raw_text=body,
            extracted_insight=body, category="pain_point", run_id=run_id,
        ))


def test_synthesizer_runs_the_web_check_before_scoring(db):
    _seed_cluster(db, "t1", [
        "My config is wiped by every weekend update and there is no rollback",
        "Same here, the upgrade blew away my volume mounts with no warning",
    ])
    checker = CompetitorChecker(
        db, cfg(critic_web_daily_budget=5, competitor_search_provider="fake"),
        FakeSearch({"": [CompetitorHit("Timeshift", "https://t.example")]}),
        clock=lambda: 0.0, sleeper=lambda s: None,
    )
    ideas = Synthesizer(db, cfg()).run(
        FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r1", checker=checker,
    )
    assert len(ideas) == 1
    stored = get_idea(db, ideas[0].id)
    assert stored["competition_checked"] == 1
    assert "Timeshift" in stored["competitor_notes"]
    assert stored["overall"] == pytest.approx(
        compute_overall(stored["demand_signal"], stored["feasibility"], stored["competition"])
    )


def test_r44_merges_instead_of_storing_a_second_idea(db):
    vector = VectorStore.in_memory(collection="ideas")
    synth = Synthesizer(db, cfg())

    _seed_cluster(db, "t1", [
        "My config is wiped by every weekend update and there is no rollback",
        "Same here, the upgrade blew away my volume mounts with no warning",
    ])
    first = synth.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r1", idea_vector=vector)
    assert len(first) == 1
    original_id = first[0].id
    before = get_idea(db, original_id)

    # A later run on a different thread saying the same thing.
    _seed_cluster(db, "t2", [
        "My config is wiped by every weekend update and there is no rollback",
        "Same here, the upgrade blew away my volume mounts with no warning",
    ], run_id="r2")
    second = synth.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r2", idea_vector=vector)

    assert second == []                                  # nothing new archived
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 1
    assert synth.merged and synth.merged[0].duplicate_of == original_id
    assert synth.merged[0].grew is True

    after = get_idea(db, original_id)
    merged_keys = json.loads(after["supporting_nuggets"])
    assert set(json.loads(before["supporting_nuggets"])) < set(merged_keys)
    # R49: an idea that gained evidence is re-scored, and the stamp advances.
    assert after["last_scored_at"] >= before["last_scored_at"]


def test_r44_leaves_a_genuinely_different_idea_alone(db):
    vector = VectorStore.in_memory(collection="ideas")
    synth = Synthesizer(db, cfg())
    _seed_cluster(db, "t1", [
        "My config is wiped by every weekend update and there is no rollback",
        "Same here, the upgrade blew away my volume mounts with no warning",
    ])
    synth.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r1", idea_vector=vector)

    _seed_cluster(db, "t2", [
        "I cannot tell which container is eating all my cloud spend each month",
        "Billing dashboards give a total but never a per-service breakdown",
    ], run_id="r2")
    second = synth.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r2", idea_vector=vector)

    assert len(second) == 1
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 2
    assert synth.merged == []


def test_dedup_off_when_no_vector_store_is_supplied(db):
    synth = Synthesizer(db, cfg())
    for run, thread in (("r1", "t1"), ("r2", "t2")):
        _seed_cluster(db, thread, [
            "My config is wiped by every weekend update and there is no rollback",
            "Same here, the upgrade blew away my volume mounts with no warning",
        ], run_id=run)
        synth.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id=run)
    # Without an idea vector R44 cannot run, and the M2.2 behaviour stands.
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 2
