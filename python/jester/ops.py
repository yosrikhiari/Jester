"""M2.4 operational helpers: floor-flag computation (jester-setup.md 13.1).

Floor flags are quality gates stamped on each run row so a nightly run can be
judged at a glance; FAILED_BATCHES is the only flag that fails the CLI exit code.
"""
from typing import List

#: T-A4: how many `needs_score` ideas a run may leave behind before the
#: residue stops being per-demand noise and becomes a backlog. With the
#: default `rescore_per_run: 1` (~150 a day) a whole run's answer only moves
#: the line by one, so a few refusals in a row — quota dry spell, model
#: rolled away — takes days to tell anyone. Five is past that noise floor:
#: one refuses-to-answer critic week is visible, one bad night is not.
SCORE_BACKLOG_THRESHOLD = 5

#: EX-G3: how many refused re-score attempts an idea may accumulate before the
#: run line, maintenance ledger and /metrics call it stalled. `rescore_pending`
#: rotates by attempt count, so a queue that keeps refusing still gives every
#: idea its turn; this is the point where "waiting" becomes "not moving" and an
#: operator has to look at the critic rather than the queue.
SCORE_STALL_ATTEMPTS = 5


def compute_floor_flags(
    *,
    kept: int,
    ideas: int,
    all_trivial: bool,
    failed_batches: int,
    dedup_deferred: int = 0,
    pending_scores: int = 0,
) -> List[str]:
    """Derive the flag set for one run.

    - LOW_NUGGETS : nothing survived the archivist threshold
    - NO_IDEAS    : nuggets kept but synthesizer produced nothing
    - THIN_HAUL   : only 1-2 ideas synthesized
    - ALL_TRIVIAL : every kept nugget was low-signal (trivial=1)
    - FAILED_BATCHES : at least one ingest batch ended in 'failed'
    - DEDUP_UNAVAILABLE : the vector store could not be reached for at least
      one nugget, so its batch was left queued rather than archived unchecked.
      Without this a run where Qdrant was down finishes 'completed' having kept
      nothing, which is indistinguishable from a run that found nothing.
    - SCORE_BACKLOG (T-A4) : more than SCORE_BACKLOG_THRESHOLD archived ideas
      sit at `needs_score` when the run finished. The critic that was asked did not
      answer, and `rescore_pending`'s per-run budget spent what it had; when
      re-scoring is off entirely this is the ONLY line the run says about it.
    """
    flags: List[str] = []
    if kept == 0:
        flags.append("LOW_NUGGETS")
    elif ideas == 0:
        flags.append("NO_IDEAS")
    elif ideas <= 2:
        flags.append("THIN_HAUL")
    if kept > 0 and all_trivial:
        flags.append("ALL_TRIVIAL")
    if failed_batches > 0:
        flags.append("FAILED_BATCHES")
    if dedup_deferred > 0:
        flags.append("DEDUP_UNAVAILABLE")
    if pending_scores > SCORE_BACKLOG_THRESHOLD:  # T-A4
        flags.append("SCORE_BACKLOG")
    return flags
