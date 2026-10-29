"""T-A4 stretch: the needs_score residue gets a floor flag (SCORE_BACKLOG).

Before this, a backlog of unscored ideas was only visible at the moment each
run chose to mention it: the re-score line printed when re-scoring was on,
and said nothing at all when `rescore_per_run` was 0. With the default
budget (~150 scores a day) a week of refusals is a drain the nightly's
quality glance never saw — floor flags exist precisely so `runs` tables read
at that glance. Five is measured against the default budget: one bad night
(unanswered critic today) is noise under it, a repeated refusal is signal
above it. compute_floor_flags is keyword-only, so the parameter is a
no-risk addition for existing callers and in-repo tests.
"""
from jester.ops import SCORE_BACKLOG_THRESHOLD, compute_floor_flags


def _flags(**kw):
    base = dict(kept=10, ideas=4, all_trivial=False, failed_batches=0)
    base.update(kw)
    return compute_floor_flags(**base)


def test_healthy_run_no_flag():
    assert _flags() == []


def test_backlog_at_or_under_the_threshold_is_not_a_flag():
    assert _flags(pending_scores=SCORE_BACKLOG_THRESHOLD) == []
    assert _flags(pending_scores=1) == []


def test_backlog_beyond_the_threshold_flags():
    assert _flags(pending_scores=SCORE_BACKLOG_THRESHOLD + 1) == ["SCORE_BACKLOG"]
    assert _flags(pending_scores=40) == ["SCORE_BACKLOG"]


def test_backlog_flag_composes_with_other_flags():
    # kept>0 so ALL_TRIVIAL can fire; expected order matches the code's append
    # order (and an extra day of patience for a correct expectation).
    flags = _flags(ideas=0, all_trivial=True, failed_batches=3, pending_scores=9)
    assert flags == ["NO_IDEAS", "ALL_TRIVIAL", "FAILED_BATCHES", "SCORE_BACKLOG"]


def test_absent_parameter_defaults_to_zero():
    # existing callers (console, nightly, older row tools) keep their output
    assert _flags() == []
