"""When the provider's quota is gone, the run stops.

`cmd_treat`'s docstring has always promised this:

    the run stops the moment the limit is hit and leaves the rest queued

It did not. The check lived AFTER `cmd_run` returned, which made it a
post-mortem rather than a brake, and the loop carried on calling a provider
that had already said no.

What that cost, measured on the live archive: a treat run sat for three hours
with 27,914 batches queued and `done` unmoved for a day. It was not
deadlocked — py-spy showed it cycling between `chat`'s request line (llm.py:581)
and its retry sleep (llm.py:601). Every remaining group was spending up to two
minutes in backoff before falling back to the stand-in. Four scheduled runs in
a row ended aborted or still-running, and each blocked the next cycle tick
because its row still said 'running'.

One exhaustion is enough to stop on: `_obj` only increments `rate_limited`
after `chat` has already made four attempts honouring the server's own
Retry-After.

These drive the real `Synthesizer.run` over a real database, because the
failure was in that loop's control flow and a test that re-implemented the
loop would have agreed with itself.
"""
from pathlib import Path

from jester.agents.synthesizer import Synthesizer
from jester.config import load_config
from jester.llm import FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Nugget
from jester.store import insert_nugget, open_db, unprocessed_nuggets

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _db_with_thread_groups(tmp_path, groups=4, per_group=2):
    """One clusterable group per thread — R41 groups by (platform, thread_id)
    and R50 skips lone nuggets, so each thread needs at least two."""
    db = open_db(str(tmp_path / "j.db"))
    for g in range(groups):
        for i in range(per_group):
            insert_nugget(db, Nugget(
                unique_key=f"t{g}-n{i}", platform="reddit", thread_id=f"thread-{g}",
                category="pain_point", extracted_insight=f"insight {g}.{i}",
                run_id="r",
            ))
    return db


class _QuotaDies(FakeSynthesizerLLM):
    """A synthesizer whose quota runs out on the Nth call.

    It reports the exhaustion the way `_GroqAgent._obj` does — incrementing
    both `fallbacks` and `rate_limited`, and recording a reset — so the loop
    sees exactly the shape the real agent produces.
    """

    def __init__(self, dies_on=1):
        super().__init__()
        self._dies_on = dies_on
        self.calls = 0
        self.rate_limited = 0
        self.retry_after = None

    def synthesize(self, nuggets):
        self.calls += 1
        draft = super().synthesize(nuggets)
        if self.calls >= self._dies_on:
            self.rate_limited += 1
            self.retry_after = 3600.0
            self.fallbacks = int(getattr(self, "fallbacks", 0) or 0) + 1
        return draft


def _run(db, llm, **kw):
    syn = Synthesizer(db, load_config(REPO_CONFIG).thresholds)
    ideas = syn.run(llm, FakeCriticLLM(), run_id="r", **kw)
    return syn, ideas


# ---- the regression --------------------------------------------------------

def test_it_stops_at_the_first_exhaustion_instead_of_grinding(tmp_path):
    """Stated as a call count, which is what the three-hour hang was made of.

    Before: one provider call per group, each costing up to two minutes of
    backoff against a provider that had already refused. After: one call.
    """
    db = _db_with_thread_groups(tmp_path, groups=25)
    llm = _QuotaDies(dies_on=1)
    syn, ideas = _run(db, llm)

    assert llm.calls == 1, (
        f"called a refusing provider {llm.calls} times across 25 groups — "
        f"that is the hang"
    )
    assert syn.stopped_early is True
    assert syn.stopped_on_quota is True
    assert ideas == [], "nothing the stand-in wrote should reach the archive"


def test_the_groups_it_never_reached_stay_for_the_next_run(tmp_path):
    """Stopping is not discarding. R50 stamps a group only once its idea is
    persisted, so an untouched group is still unclaimed work."""
    db = _db_with_thread_groups(tmp_path, groups=8)
    _run(db, _QuotaDies(dies_on=1))

    left = unprocessed_nuggets(db)
    assert len(left) == 16, (
        f"only {len(left)} of 16 nuggets left unclaimed; a stop that consumes "
        f"the queue loses evidence the archive already paid to fetch"
    )


def test_running_out_partway_keeps_what_came_before(tmp_path):
    db = _db_with_thread_groups(tmp_path, groups=12)
    llm = _QuotaDies(dies_on=4)
    syn, ideas = _run(db, llm)

    assert llm.calls == 4, "three good groups, then the one that was refused"
    assert len(ideas) == 3, "the ideas written before the limit stand"
    assert syn.stopped_on_quota is True


# ---- the brake must not fire on its own ------------------------------------

def test_a_healthy_run_still_reaches_every_group(tmp_path):
    """Otherwise the brake is just a cap with a confusing name."""
    db = _db_with_thread_groups(tmp_path, groups=6)
    llm = _QuotaDies(dies_on=999)
    syn, ideas = _run(db, llm)

    assert llm.calls == 6
    assert len(ideas) == 6
    assert syn.stopped_early is False
    assert syn.stopped_on_quota is False


def test_a_ceiling_is_not_a_quota(tmp_path):
    """`max_ideas` and "we were cut off" both set stopped_early. A summary that
    cannot tell them apart reports a deliberate stop as an outage."""
    db = _db_with_thread_groups(tmp_path, groups=10)
    syn, ideas = _run(db, _QuotaDies(dies_on=999), max_ideas=2)

    assert syn.stopped_early is True
    assert syn.stopped_on_quota is False
    assert len(ideas) == 2


def test_the_flag_resets_between_runs(tmp_path):
    """A stale flag would make a healthy run look like it had been cut off."""
    db = _db_with_thread_groups(tmp_path, groups=4)
    syn = Synthesizer(db, load_config(REPO_CONFIG).thresholds)

    syn.run(_QuotaDies(dies_on=1), FakeCriticLLM(), run_id="r")
    assert syn.stopped_on_quota is True

    syn.run(_QuotaDies(dies_on=999), FakeCriticLLM(), run_id="r")
    assert syn.stopped_on_quota is False


# ---- what the operator is told ---------------------------------------------

def test_the_two_stops_do_not_share_a_sentence():
    """The first version of the brake printed "stopped at the None-idea goal"
    on a run with no goal set. It was caught by running against a live
    exhausted quota, which is not something to rely on."""
    from jester.cli import stopped_early_line

    class _Goal:
        stopped_on_quota = False

    class _Quota:
        stopped_on_quota = True

    goal = stopped_early_line(_Goal(), 5)
    quota = stopped_early_line(_Quota(), None)

    assert "5-idea goal" in goal
    assert "quota ran out" in quota
    assert "None" not in quota, "a run with no ceiling must not report one"
    assert "goal" not in quota, "being cut off is not reaching a goal"
    # Both must still say the work is not lost.
    for line in (goal, quota):
        assert "stay queued for the next run" in line
