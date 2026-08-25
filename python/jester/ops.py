"""M2.4 operational helpers: floor-flag computation (jester-setup.md 13.1).

Floor flags are quality gates stamped on each run row so a nightly run can be
judged at a glance; FAILED_BATCHES is the only flag that fails the CLI exit code.
"""
from typing import List


def compute_floor_flags(
    *, kept: int, ideas: int, all_trivial: bool, failed_batches: int
) -> List[str]:
    """Derive the flag set for one run.

    - LOW_NUGGETS : nothing survived the archivist threshold
    - NO_IDEAS    : nuggets kept but synthesizer produced nothing
    - THIN_HAUL   : only 1-2 ideas synthesized
    - ALL_TRIVIAL : every kept nugget was low-signal (trivial=1)
    - FAILED_BATCHES : at least one ingest batch ended in 'failed'
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
    return flags
