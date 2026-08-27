"""M2 tests: scoring (R29), synthesizer golden gate (R48), critic gate, config merge."""
import json
import pytest
from pathlib import Path

from jester.agents.synthesizer import Synthesizer
from jester.cli import cmd_run
from jester.config import load_config, load_thresholds
from jester.llm import FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Idea, IdeaScores
from jester.scoring import compute_overall, SCORE_MAX, SCORE_MIN
from jester.store import get_idea, list_ideas, open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
REPO_THRESHOLDS = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_r29_competition_imputed_when_unchecked():
    # competition None -> imputed as 5 -> overall = 0.4d + 0.3f + 1.8
    assert compute_overall(4, 5, None) == 0.4 * 4 + 0.3 * 5 + 1.8
    # explicit competition uses the locked rubric
    assert compute_overall(4, 5, 3) == 0.4 * 4 + 0.3 * 5 + 0.3 * (11 - 3)
    # imputed overall stays within the global score range
    for d, f in ((1, 1), (10, 10)):
        assert SCORE_MIN <= compute_overall(d, f, None) <= SCORE_MAX


def test_r48_golden_gate_structural(tmp_path, offline_config):
    db_path = tmp_path / "j.db"
    cfg = load_config(REPO_CONFIG)
    cmd_run(_ns(config=str(offline_config), db=str(db_path), run="gate-run"))

    db = open_db(db_path)
    ideas = list_ideas(db)
    assert ideas, "expected at least one synthesized idea"
    for row in ideas:
        supporting = __import__("json").loads(row["supporting_nuggets"])
        assert supporting, "every idea must cite >=1 real nugget"
        # all subscores in 1-10 (competition may be NULL when unchecked)
        assert SCORE_MIN <= row["demand_signal"] <= SCORE_MAX
        assert SCORE_MIN <= row["feasibility"] <= SCORE_MAX
        if row["competition_checked"]:
            assert SCORE_MIN <= row["competition"] <= SCORE_MAX
            assert row["competitor_notes"] is not None
        else:
            assert row["competition"] is None
            assert row["competitor_notes"] is None  # never fabricated (R29/R41)


def test_critic_gate_check_toggles_competition_checked():
    # Fake critic leaves competition unchecked
    fake = FakeCriticLLM()
    draft = fake.score(Idea(title="t"))
    assert draft.competition is None

    # An explicit critic that checks competition flips the gate
    from jester.llm import CriticDraft

    checked = CriticDraft(demand_signal=8, feasibility=7, competition=4)
    assert checked.competition is not None


def test_load_thresholds_merges_model_block():
    thr = load_thresholds(REPO_THRESHOLDS)
    # thresholds.yaml nested models: block must flow onto Thresholds
    assert thr.synthesizer_model
    assert thr.critic_model
    assert SCORE_MIN <= thr.good_idea_min <= SCORE_MAX


def test_cmd_mark_updates_status(tmp_path, offline_config):
    db_path = tmp_path / "j.db"
    cfg = load_config(REPO_CONFIG)
    cmd_run(_ns(config=str(offline_config), db=str(db_path), run="mark-run"))
    db = open_db(db_path)
    idea_id = list_ideas(db)[0]["id"]

    from jester.cli import cmd_mark

    cmd_mark(_ns(db=str(db_path), id=idea_id, status="validated"))
    row = get_idea(open_db(db_path), idea_id)
    assert row["status"] == "validated"


def test_r48_golden_gate_rejects_violations():
    # R48 structural gate, unit-tested directly on golden_gate().
    from jester.agents.synthesizer import golden_gate, GoldenGateError

    # Valid: >=1 real input nugget, no out-of-input, subscores in 1-10.
    ok = Idea(
        title="t",
        supporting_nuggets=["k1", "k2"],
        scores=IdeaScores(demand_signal=4.0, feasibility=5.0, competition=None, overall=0.0),
    )
    golden_gate(ok, {"k1", "k2"})  # must not raise

    # Empty support list -> R48 violation.
    with pytest.raises(GoldenGateError):
        golden_gate(Idea(title="t", supporting_nuggets=[],
                         scores=IdeaScores(5.0, 5.0)), {"k1"})

    # Cite a nugget outside the input set -> R48 violation.
    with pytest.raises(GoldenGateError):
        golden_gate(Idea(title="t", supporting_nuggets=["kx"],
                         scores=IdeaScores(5.0, 5.0)), {"k1", "k2"})

    # Subscore above 10 -> R48 violation.
    with pytest.raises(GoldenGateError):
        golden_gate(Idea(title="t", supporting_nuggets=["k1"],
                         scores=IdeaScores(11.0, 5.0)), {"k1"})

    # Subscore below 1 -> R48 violation.
    with pytest.raises(GoldenGateError):
        golden_gate(Idea(title="t", supporting_nuggets=["k1"],
                         scores=IdeaScores(5.0, 0.0)), {"k1"})

    # Competition present but out of 1-10 -> R48 violation.
    with pytest.raises(GoldenGateError):
        golden_gate(Idea(title="t", supporting_nuggets=["k1"],
                         scores=IdeaScores(5.0, 5.0, competition=12.0)), {"k1"})


def test_r48_golden_gate_allows_unchecked_competition():
    from jester.agents.synthesizer import golden_gate

    # competition=None (critic unchecked, R29) is allowed by the gate.
    idea = Idea(title="t", supporting_nuggets=["k1"],
                scores=IdeaScores(demand_signal=5.0, feasibility=5.0, competition=None))
    golden_gate(idea, {"k1"})  # must not raise


def test_r48_golden_gate_enforced_in_run(tmp_path):
    # Integration: a critic returning out-of-range subscores must be blocked
    # before persistence (the gate is structural, not advisory).
    from jester.agents.synthesizer import Synthesizer, GoldenGateError
    from jester.store import insert_nugget
    from jester.models import Nugget

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    for fpk in ("f1", "f2"):
        insert_nugget(db, Nugget(
            unique_key=fpk, platform="reddit", thread_id="abc",
            category="pain_point", extracted_insight="x", run_id="r",
        ))

    cfg = load_config(REPO_CONFIG)

    class BadCritic:
        def score(self, idea):
            from jester.llm import CriticDraft
            return CriticDraft(demand_signal=99.0, feasibility=5.0, competition=None)

    syn = Synthesizer(db, cfg.thresholds)
    with pytest.raises(GoldenGateError):
        syn.run(FakeSynthesizerLLM(), BadCritic(), run_id="r")

    # Nothing was persisted when the gate rejected the idea.
    assert list_ideas(db) == []


# ---- degraded synthesis must not reach the archive -----------------------


def test_a_fallback_group_is_not_archived():
    """A stand-in "idea" is a truncated comment with a score attached.

    Seen live: a Groq daily token limit turned one night's run into 26
    archived ideas titled things like "[hackernews] bro how is this different
    from just asking Claude?". The run summary DID report degrading 36 times;
    the ideas were written anyway, and the summary is not what anyone reads a
    week later.
    """
    from jester.agents.synthesizer import Synthesizer
    from jester.llm import FakeCriticLLM, IdeaDraft
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(":memory:")
    for i in range(3):
        insert_nugget(db, Nugget(unique_key=f"k{i}", platform="reddit",
                                 thread_id="t1", raw_text=f"pain {i}",
                                 extracted_insight=f"pain {i}"))

    class AlwaysFallsBack:
        name = "model-that-is-rate-limited"

        def __init__(self):
            self.fallbacks = 0

        def synthesize(self, nuggets):
            # Exactly what _GroqAgent does on a 429: bump the counter and
            # return the deterministic stand-in's output.
            self.fallbacks += 1
            return IdeaDraft(title="[reddit] pain 0", problem_statement="p",
                             proposed_solution="s")

    synth = Synthesizer(db, load_thresholds(REPO_THRESHOLDS))
    llm = AlwaysFallsBack()
    ideas = synth.run(llm, FakeCriticLLM(), run_id="degraded")

    assert ideas == [], "a stand-in idea must not be archived"
    assert synth.skipped_fallback == 1, "the skip must be counted, not silent"
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0

    # …and the nuggets stay unclaimed, so a later run with a working model
    # picks them up rather than losing them.
    unclaimed = db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE synthesized_at IS NULL"
    ).fetchone()[0]
    assert unclaimed == 3, "skipped nuggets must stay queued"


def test_a_healthy_group_is_still_archived():
    """The guard must not swallow good output: only a group whose OWN call
    fell back is skipped."""
    from jester.agents.synthesizer import Synthesizer
    from jester.llm import FakeCriticLLM, IdeaDraft
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(":memory:")
    for i in range(3):
        insert_nugget(db, Nugget(unique_key=f"k{i}", platform="reddit",
                                 thread_id="t1", raw_text=f"pain {i}",
                                 extracted_insight=f"pain {i}"))

    class Healthy:
        name = "a-real-model"
        # A counter that exists but never increments — the shape a working
        # _GroqAgent has after a successful call.
        fallbacks = 0

        def synthesize(self, nuggets):
            return IdeaDraft(title="Backup Sentinel",
                             problem_statement="backups fail silently",
                             proposed_solution="watch and alert")

    synth = Synthesizer(db, load_thresholds(REPO_THRESHOLDS))
    ideas = synth.run(Healthy(), FakeCriticLLM(), run_id="healthy")

    assert len(ideas) == 1
    assert synth.skipped_fallback == 0
    assert ideas[0].title == "Backup Sentinel"
