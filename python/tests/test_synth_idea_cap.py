"""The console's "stop at N ideas" goal.

Stopping mid-pool is only safe because of R50: a group's nuggets are stamped
`synthesized_at` when its idea is persisted, so anything the ceiling cut off
stays unclaimed and is the next run's first work. These tests pin that down —
a `max_ideas` that dropped the remainder on the floor would silently lose
evidence the archive already paid to fetch.
"""
from pathlib import Path

import pytest

from jester.agents.synthesizer import Synthesizer
from jester.config import load_config
from jester.llm import FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Nugget
from jester.store import insert_nugget, list_ideas, open_db, unprocessed_nuggets

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _db_with_thread_groups(tmp_path, groups=4, per_group=2):
    """One clusterable group per thread — R41 groups by (platform, thread_id)
    and R50 skips lone nuggets, so each thread needs at least two."""
    db = open_db(str(tmp_path / "j.db"))
    for g in range(groups):
        for i in range(per_group):
            insert_nugget(db, Nugget(
                unique_key=f"t{g}-n{i}", platform="reddit", thread_id=f"thread-{g}",
                category="pain_point", extracted_insight=f"insight {g}.{i}", run_id="r",
            ))
    return db


def _run(db, **kw):
    cfg = load_config(REPO_CONFIG)
    syn = Synthesizer(db, cfg.thresholds)
    ideas = syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r", **kw)
    return syn, ideas


def test_no_ceiling_clusters_every_group(tmp_path):
    db = _db_with_thread_groups(tmp_path, groups=4)
    syn, ideas = _run(db)
    assert len(ideas) == 4
    assert syn.stopped_early is False
    assert unprocessed_nuggets(db) == []


def test_ceiling_stops_at_exactly_n_ideas(tmp_path):
    db = _db_with_thread_groups(tmp_path, groups=4)
    syn, ideas = _run(db, max_ideas=2)
    assert len(ideas) == 2
    assert len(list_ideas(db)) == 2
    assert syn.stopped_early is True


def test_what_the_ceiling_cut_off_stays_queued(tmp_path):
    """The whole reason the early break is safe: the groups it did not reach
    are still unclaimed, so the next run picks them up."""
    db = _db_with_thread_groups(tmp_path, groups=4)
    _run(db, max_ideas=2)
    left = unprocessed_nuggets(db)
    assert len(left) == 4, "two untouched groups of two should remain"

    # A second pass with no ceiling drains the rest — nothing was lost.
    syn2, more = _run(db)
    assert len(more) == 2
    assert syn2.stopped_early is False
    assert unprocessed_nuggets(db) == []
    assert len(list_ideas(db)) == 4


def test_ceiling_larger_than_the_pool_is_not_an_early_stop(tmp_path):
    db = _db_with_thread_groups(tmp_path, groups=2)
    syn, ideas = _run(db, max_ideas=99)
    assert len(ideas) == 2
    assert syn.stopped_early is False


@pytest.mark.parametrize("value", [None, 0])
def test_absent_or_zero_ceiling_means_uncapped(tmp_path, value):
    """`None` is "no goal set"; 0 must not be read as "stop immediately" —
    the console refuses 0 upstream, and here it means the same as unset."""
    db = _db_with_thread_groups(tmp_path, groups=3)
    syn, ideas = _run(db, max_ideas=value)
    assert len(ideas) == 3
    assert syn.stopped_early is False


def test_stopped_early_resets_between_runs(tmp_path):
    """A stale flag would make an uncapped run report a goal it never had."""
    db = _db_with_thread_groups(tmp_path, groups=3)
    syn = Synthesizer(db, load_config(REPO_CONFIG).thresholds)
    syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r", max_ideas=1)
    assert syn.stopped_early is True
    syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r")
    assert syn.stopped_early is False


# --- max_nuggets_per_idea: oversized thread groups are chunked ---------------
#
# One Hacker News thread can yield 700 nuggets — 26k prompt tokens, over the
# 8k-per-request Groq free tier allows — so the whole group fell back every
# cycle and archived nothing. The cap slices such a group into consecutive
# chunks, each synthesized on its own; nothing is dropped and nothing stays
# unclaimed.

def _db_with_one_big_thread(tmp_path, n):
    db = open_db(str(tmp_path / "j.db"))
    for i in range(n):
        insert_nugget(db, Nugget(
            unique_key=f"big-n{i:03d}", platform="hackernews", thread_id="hn-1",
            category="pain_point", extracted_insight=f"insight {i}", run_id="r",
        ))
    return db


def _run_capped(db, cap, **kw):
    cfg = load_config(REPO_CONFIG)
    cfg.thresholds.max_nuggets_per_idea = cap
    syn = Synthesizer(db, cfg.thresholds)
    ideas = syn.run(FakeSynthesizerLLM(), FakeCriticLLM(), run_id="r", **kw)
    return syn, ideas


def test_oversized_group_is_split_into_chunks(tmp_path):
    db = _db_with_one_big_thread(tmp_path, 10)
    syn, ideas = _run_capped(db, cap=4)
    # 10 rows / cap 4 -> 4, 4, 2
    assert len(ideas) == 3
    assert syn.split_groups == 1
    assert sorted(len(i.supporting_nuggets) for i in ideas) == [2, 4, 4]
    assert unprocessed_nuggets(db) == []


def test_trailing_singleton_folds_into_previous_chunk(tmp_path):
    """9 rows / cap 4 would leave a 1-row tail that R50 skips forever; it is
    folded into the previous chunk instead (4, 5)."""
    db = _db_with_one_big_thread(tmp_path, 9)
    syn, ideas = _run_capped(db, cap=4)
    assert sorted(len(i.supporting_nuggets) for i in ideas) == [4, 5]
    assert unprocessed_nuggets(db) == []


def test_group_within_cap_is_untouched(tmp_path):
    db = _db_with_one_big_thread(tmp_path, 4)
    syn, ideas = _run_capped(db, cap=40)
    assert len(ideas) == 1
    assert syn.split_groups == 0


def test_chunks_are_contiguous_in_thread_order(tmp_path):
    """A chunk is a stretch of the discussion, not a random sample of it."""
    db = _db_with_one_big_thread(tmp_path, 6)
    _, ideas = _run_capped(db, cap=3)
    keys = [sorted(i.supporting_nuggets) for i in ideas]
    assert sorted(keys) == [
        ["big-n000", "big-n001", "big-n002"],
        ["big-n003", "big-n004", "big-n005"],
    ]
