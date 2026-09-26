"""A run claims a bounded number of comments, or it never finishes.

While extraction was the stub this did not matter: it returned instantly, so
claiming every pending batch cost nothing and a run's duration was
effectively zero. A real local model costs ~15s for a short comment and ~29s
for a long one, which turns "claim everything" into a run measured in hours.

Observed on the live archive the night the real extractor was switched on:

    22:09  claims 503 comments  ->  still running 8 hours later
    06:09  the next tick reaps it as dead and claims the same 503
    07:12  that run is 63 minutes old, already past the reap window

Nothing ever completed. Each oversized run was killed by a later tick, which
started its own oversized run, which was killed in turn -- a livelock, and
every model call made in those eight hours was discarded.

The cap and the reap window are a pair: the cap is a promise about how long a
run takes, and the window is how long the next tick waits before calling it
dead. Raise one without the other and the livelock comes back.
"""
import json
import types

from jester.cli import COMMENT_BUDGET, _comment_budget, bounded_bite


def _args(**kw):
    a = types.SimpleNamespace(max_comments=None, stale_after=60)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _batches(sizes):
    """Fake pending rows, each carrying `n` comments."""
    return [{"comments": json.dumps([{"body": "x"}] * n)} for n in sizes]


# The REAL function, not a copy of it. The first version of this file
# re-implemented the loop here and then asserted against its own
# re-implementation, which would have passed no matter what cmd_run shipped.
_take = bounded_bite


# ---- the budget itself -----------------------------------------------------

def test_the_default_budget_fits_inside_the_reap_window():
    """The regression, stated as arithmetic. At the measured worst case of
    ~29s per comment, the cap must leave a run finishing inside 60 minutes."""
    worst_case_seconds = COMMENT_BUDGET * 29
    assert worst_case_seconds < 60 * 60, (
        f"{COMMENT_BUDGET} comments is {worst_case_seconds/60:.0f} minutes at "
        f"the measured worst case — a run that long gets reaped mid-flight "
        f"and loses every extraction it made"
    )


def test_an_explicit_cap_wins():
    assert _comment_budget(_args(max_comments=25)) == 25


def test_the_default_applies_when_unset():
    assert _comment_budget(_args()) == COMMENT_BUDGET


def test_zero_means_no_cap():
    """Kept as an escape hatch for a one-off drain, not as a default."""
    assert _comment_budget(_args(max_comments=0)) == 0


# ---- the trim --------------------------------------------------------------

def test_an_oversized_queue_is_trimmed():
    """503 comments was the live number. It must not all be claimed."""
    rows = _batches([50] * 11)          # 550 queued
    kept, taken = _take(rows, 120)
    assert len(kept) < len(rows)
    assert taken <= 120 + 50, "overshoot is at most one batch"


def test_batches_are_never_split():
    """Half-extracting a batch would leave it neither done nor safely
    re-runnable, and the comments already paid for would be extracted twice."""
    rows = _batches([7, 7, 7])
    kept, taken = _take(rows, 10)
    assert taken % 7 == 0, "took part of a batch"


def test_a_small_queue_is_taken_whole():
    """Otherwise the cap would throttle a pipeline that is keeping up — the
    live comment queue was 11 when this landed."""
    rows = _batches([3, 4, 2])
    kept, taken = _take(rows, 120)
    assert len(kept) == 3
    assert taken == 9


def test_the_remainder_is_left_not_dropped():
    """A trimmed batch stays pending, so the next tick picks it up. Dropping
    it would lose evidence the scrape already paid for."""
    rows = _batches([100, 100, 100])
    kept, _ = _take(rows, 120)
    assert len(rows) - len(kept) == 1, "the untouched batch must remain"


def test_an_unreadable_batch_does_not_stop_the_bite():
    """A malformed payload counts as zero comments and is skipped downstream.
    Treating it as fatal would let one bad row starve a whole run."""
    rows = [{"comments": "not json"}, {"comments": json.dumps([{"b": 1}] * 3)}]
    kept, taken = bounded_bite(rows, 120)
    assert len(kept) == 2
    assert taken == 3


def test_zero_budget_takes_everything():
    rows = _batches([40, 40, 40])
    kept, taken = bounded_bite(rows, 0)
    assert len(kept) == 3
