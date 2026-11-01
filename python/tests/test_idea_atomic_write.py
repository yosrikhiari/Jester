"""V3-01: an idea's evidence claim and its row are written together.

The synthesizer used to insert the idea (commit) and then stamp its nuggets
(commit) as two writes. A crash in between left the idea archived and its
nuggets unclaimed, so the next run rebuilt the same idea from the same
evidence and archived it a second time. And with Qdrant down, the R44 vector
compare is skipped entirely (`idea_vector is None`), so nothing at all stood
between a second run and a duplicate row.

Now the claim, the DB-level duplicate check and the insert are one
`BEGIN IMMEDIATE` unit: a fault rolls all three back, and a second writer
finds the supported set already archived and merges into it.
"""
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
    list_ideas,
    open_db,
    unprocessed_nuggets,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _db(tmp_path, groups=1):
    db = open_db(str(tmp_path / "j.db"))
    for g in range(groups):
        for i in range(2):
            insert_nugget(db, Nugget(
                unique_key=f"t{g}-n{i}", platform="reddit", thread_id=f"thread-{g}",
                category="pain_point", extracted_insight=f"insight {g}.{i}", run_id="r"))
    return db


def _syn(db):
    return Synthesizer(db, load_config(REPO_CONFIG).thresholds)


def test_a_fault_between_the_claim_and_the_insert_leaves_nothing_behind(
        tmp_path, monkeypatch):
    db = _db(tmp_path)

    def die(db_, idea):
        raise RuntimeError("process died between the claim and the insert")

    with monkeypatch.context() as m:
        m.setattr("jester.agents.synthesizer.insert_idea_nocommit", die)
        with pytest.raises(RuntimeError):
            _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="crashed")

    assert list_ideas(db) == [], "the idea was never committed"
    assert len(unprocessed_nuggets(db)) == 2, "the claim rolled back with it"

    ideas = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="retry")
    assert len(ideas) == 1
    assert len(list_ideas(db)) == 1, "the re-run produces exactly one idea"
    assert unprocessed_nuggets(db) == []


def test_a_legacy_half_write_is_repaired_not_duplicated(tmp_path):
    """The state the pre-V3-01 two-commit write left behind on a crash: a
    durable idea, its evidence still unclaimed.

    No fault injection into the new code can produce this — the claim now
    commits with the insert, which is the whole fix (a crash inside the unit
    rolls the claim back, so the re-run starts from an empty slate, as the
    test above shows). Only the old ordering reaches it, so the test builds it
    with the writer that ordering used: `insert_idea` commits on its own and
    is still what every other caller uses, and the test then stops exactly
    where the process died, before the nugget stamp.

    Every archive upgraded from v0.1.0 can hold this shape, and Qdrant being
    down is why it is not hypothetical: R44's vector compare is skipped
    entirely, so nothing but the archive itself can catch the duplicate.
    """
    db = _db(tmp_path)
    keys = ["t0-n0", "t0-n1"]
    insert_idea(db, Idea(
        title="the idea the crashed run committed",
        problem_statement="the problem it committed",
        proposed_solution="the solution it committed",
        supporting_nuggets=keys,
        scores=IdeaScores(demand_signal=8, feasibility=7, overall=7.0)))

    # The crash window: idea present, claim absent.
    assert len(list_ideas(db)) == 1, "commit 1 happened"
    assert len(unprocessed_nuggets(db)) == 2, "commit 2 never did"

    second_syn = _syn(db)
    second = second_syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="retry")
    assert second == [], "nothing new was archived"
    assert len(list_ideas(db)) == 1, "the same supported set is one idea"
    assert second_syn.merged and second_syn.merged[0].duplicate_of == 1
    assert second_syn.merged[0].grew is False, "the evidence was already there"
    assert second_syn.merged[0].score == 1.0, "identical evidence, not a cosine"
    assert unprocessed_nuggets(db) == [], "the claim is repaired by the merge"


def test_the_same_input_run_twice_archives_one_idea(tmp_path):
    """The brief's plain double-run: the second pass over the same evidence
    adds nothing. `rows` is captured first and handed to the second run on
    purpose — a second run over a stale pool is what the DB-level guard is
    for, and with no vector index the guard is the only thing standing."""
    db = _db(tmp_path)
    rows = unprocessed_nuggets(db)

    first = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="a")
    assert len(first) == 1

    second_syn = _syn(db)
    second = second_syn.run(FakeSynthesizerLLM(), FakeCriticLLM(),
                            run_id="b", rows=rows)
    assert second == [], "zero new ideas on the second run"
    assert len(list_ideas(db)) == 1
    assert second_syn.claimed_elsewhere == 0, "a merge, not a skipped claim"
    assert second_syn.merged[0].grew is False


def test_manual_resynth_may_reuse_claimed_evidence(tmp_path):
    """D-16/R56: the escape hatch derives a SECOND idea from evidence one
    already rests on. That is deliberate, so the duplicate guard is off for it
    — but its claim and insert are still one transaction."""
    db = _db(tmp_path)
    rows = unprocessed_nuggets(db)
    first = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="a")
    assert len(first) == 1

    again = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(),
                         run_id="resynth-1", rows=rows,
                         reuse_claimed_evidence=True)
    assert len(again) == 1
    assert len(list_ideas(db)) == 2


def test_insert_idea_still_commits_on_its_own(tmp_path):
    """The no-commit variant is the transaction's; the public entry point keeps
    the behaviour every other caller relies on."""
    db = _db(tmp_path)
    idea = Idea(title="standalone", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["t0-n0", "t0-n1"],
                scores=IdeaScores(demand_signal=8, feasibility=7, overall=7.0))
    idea_id = insert_idea(db, idea)
    assert db.in_transaction is False
    assert get_idea(db, idea_id) is not None


def test_the_supported_set_key_is_order_independent(tmp_path):
    from jester.store import find_idea_by_support_key, idea_support_key

    db = _db(tmp_path)
    assert idea_support_key(["b", "a", "c"]) == idea_support_key(["c", "a", "b"])

    idea = Idea(title="keyed", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["t0-n1", "t0-n0"])
    idea.id = insert_idea(db, idea)
    found = find_idea_by_support_key(db, idea_support_key(["t0-n0", "t0-n1"]))
    assert found == idea.id
    assert find_idea_by_support_key(db, idea_support_key(["elsewhere"])) is None
