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
import json
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


class _OneMatchVector:
    """A vector index that calls every idea a near-duplicate of one row."""

    def __init__(self, idea_id, score=0.95):
        self.idea_id = idea_id
        self.score = score
        self.upserts = []

    def search_scored(self, text, limit=5):
        return [(self.score, f"idea:{self.idea_id}")]

    def upsert(self, key, text, payload=None):
        self.upserts.append(key)


def _crashed_merge_idea(db, keys):
    """The residue the old two-commit merge path left: an idea already grown,
    its newly absorbed nuggets still unclaimed."""
    insert_idea(db, Idea(
        title="the idea the crashed merge grew",
        problem_statement="it grew, then the process died",
        proposed_solution="claim it on the next run",
        supporting_nuggets=keys,
        scores=IdeaScores(demand_signal=8, feasibility=7, overall=7.0)))


def test_a_group_already_inside_an_idea_is_absorbed_not_rearchived(tmp_path):
    """The merge path's crash residue, constructed directly.

    `_absorb` used to commit the merge and then commit the claim. A process
    that died between them left exactly this shape: an idea whose support
    already contains the group's keys, with those nuggets still unclaimed.
    The guard then compared supported sets for EQUALITY, so the group — a
    SUBSET of the union the idea now rests on — matched nothing and a second
    idea was archived over evidence the first one already held (measured:
    one idea in, three out). Containment closes it: an idea that contains
    every key of the group is the idea this group is about.
    """
    db = _db(tmp_path)
    _crashed_merge_idea(db, ["t0-n0", "t0-n1", "t9-n0"])
    assert len(list_ideas(db)) == 1, "the merge commit happened"
    assert len(unprocessed_nuggets(db)) == 2, "the claim commit never did"

    syn = _syn(db)
    new = syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="retry")

    assert new == [], "zero new ideas: the group is inside that idea"
    assert len(list_ideas(db)) == 1, "no second idea over the same evidence"
    assert unprocessed_nuggets(db) == [], "the merge repairs the missing claim"
    assert syn.merged and syn.merged[0].duplicate_of == 1
    assert syn.merged[0].grew is False, "the evidence was already there"
    assert syn.claimed_elsewhere == 0, "absorbed, not skipped"


def test_the_tightest_superset_is_the_one_absorbed(tmp_path):
    """Containment must not pick arbitrarily: the idea whose evidence the group
    is most specifically about wins, and the tie-break is stable (lowest id),
    so two runs over the same archive make the same decision."""
    db = _db(tmp_path)
    _crashed_merge_idea(db, ["t0-n0", "t0-n1", "t9-n0"])
    _crashed_merge_idea(db, ["t0-n0", "t0-n1", "t8-n0", "t8-n1"])

    syn = _syn(db)
    syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="retry")

    assert syn.merged[0].duplicate_of == 1, "the smaller superset, not the broader one"
    assert len(list_ideas(db)) == 2, "the two archived ideas are untouched"


def test_a_fault_between_the_merge_and_its_claim_rolls_both_back(tmp_path, monkeypatch):
    """Merge and claim are one write now.

    They were two commits, in that order. A crash between them left the idea
    grown (its support_key covering the union) with the absorbed nuggets
    unclaimed — the residue the containment guard above has to recognise.
    With both writes inside one BEGIN IMMEDIATE, a crash leaves neither.
    """
    db = _db(tmp_path)
    _crashed_merge_idea(db, ["t0-n0"])
    vector = _OneMatchVector(idea_id=1)

    def die(*args, **kwargs):
        raise RuntimeError("process died between the merge and its claim")

    with monkeypatch.context() as m:
        # Both names: the synthesizer calls its own imported reference, and
        # `store.set_synthesized` calls `store`'s. Patching both is what makes
        # the fault land wherever the write actually happens.
        m.setattr("jester.agents.synthesizer.set_synthesized_nocommit", die)
        m.setattr("jester.store.set_synthesized_nocommit", die)
        with pytest.raises(RuntimeError):
            _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(),
                         run_id="crashed", idea_vector=vector)

    row = get_idea(db, 1)
    assert json.loads(row["supporting_nuggets"]) == ["t0-n0"], "the merge rolled back"
    assert db.in_transaction is False, "no transaction left open"
    assert len(list_ideas(db)) == 1, "no idea was archived either"
    assert len(unprocessed_nuggets(db)) == 2, "the claim rolled back with the merge"

    retry = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(),
                         run_id="retry", idea_vector=vector)
    assert retry == [], "the retry is still a merge"
    assert json.loads(get_idea(db, 1)["supporting_nuggets"]) == ["t0-n0", "t0-n1"]
    assert unprocessed_nuggets(db) == [], "and this time the claim stuck"


def test_manual_resynth_still_creates_its_second_idea(tmp_path):
    """D-16/R56: the escape hatch asks for the evidence again on purpose.
    Containment must not swallow it — the guard is off for this path."""
    db = _db(tmp_path)
    _crashed_merge_idea(db, ["t0-n0", "t0-n1", "t9-n0"])
    rows = unprocessed_nuggets(db)

    new = _syn(db).run(FakeSynthesizerLLM(), FakeCriticLLM(),
                       run_id="resynth-1", rows=rows,
                       reuse_claimed_evidence=True)

    assert len(new) == 1, "the operator asked for a second idea"
    assert len(list_ideas(db)) == 2
    assert unprocessed_nuggets(db) == [], "the reused evidence is stamped"


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
