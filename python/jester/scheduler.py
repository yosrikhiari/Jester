"""Deterministic source allocation: rank enabled sources by measured yield.

No model sits in this loop, on purpose (trade-off table §4): the data for
scoring is already structured, the choice must be reproducible from the
archive alone, and mock mode has to stay offline-deterministic. The
scheduler is a pure function of three inputs:

  - the archive's own ledger: `ingest_batch` rows are per (run, source,
    thread) fetch events, and `nuggets` rows are what came OUT of them;
  - `sources.yaml` (which sources are enabled at all);
  - two thresholds: `scheduler_enabled` (the run hook) and
    `scheduler_window_batches` (how many recent batch rows per source the
    score reads).

The decision it makes is ranking, and nothing more aggressive:

  - hot / warm sources (yield > 0) walk every run, in yield order;
  - brand-new sources walk too — a source with no batches is "fresh", and
    you cannot get yield data without walking it once;
  - a ZERO-yield source (threads walked in the window, zero nuggets out)
    walks only on its rotating probe turn — one per run, round-robin —
    instead of every run at full depth.

THE CAP CLAMP is structural (DQ6): the scheduler never passes a thread
override to the Go worker, because there is no such flag and it must not
want one. It hands `worker.ingest` a `-only` name list; Reddit/SE/whatever
caps stay exactly what `max_threads_per_source`/`max_threads_per_platform`
configured, and the plan REPORTS those caps without ever exceeding them
(see test_scheduler.test_caps_never_raised_names_only).

The fallback is honest silence: when the scheduler is off, or the archive
has no batches yet, or config is unreadable, `alloc_names` returns None
and the run walks every enabled source exactly as it always did. An empty
plan can never become "walk nothing".
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

#: Probe eligibility: a zero-yield source must have walked at least this
#: many DISTINCT threads in the window before "nothing out of it" means
#: anything (one thread walked says nothing about a source).
MIN_ZERO_YIELD_THREADS = 2

#: With only one non-zero source there is no median; this watermark stands in
#: (a quarter of a nugget per thread walked is "worthful").
SINGLE_NONZERO_HOT_RATE = 0.25

GROUP_ORDER = {"hot": 0, "warm": 1, "fresh": 2, "zero": 3}


class SchedulerError(ValueError):
    """A plan cannot be computed for the state it was given."""


@dataclass(frozen=True)
class SourceScore:
    """One source, measured over its own recent-batch window."""

    name: str
    platform: str
    kind: str
    threads: int          # distinct threads walked inside the window
    batches: int          # batch events in the window (a thread may repeat)
    fresh_nuggets: int    # archived rows out of those threads
    cap: int              # the CONFIGURED walk ceiling (reported, never raised)

    @property
    def rate(self) -> float:
        if self.threads <= 0:
            return 0.0
        return self.fresh_nuggets / self.threads


@dataclass(frozen=True)
class Decision:
    """What to do with one source on the next run, and why, in words."""

    name: str
    platform: str
    group: str            # hot | warm | fresh | zero
    walks_this_run: bool
    reason: str

    # From the score the decision was built on; the cap is the CONFIGURED
    # number and travel with the plan as a report, not a new override.
    cap: int
    rate: float


@dataclass(frozen=True)
class Plan:
    run_seq: int          # DISTINCT run count the ledger saw at plan time
    window: int           # the threshold value this plan used
    decisions: Tuple[Decision, ...]
    walked: Tuple[str, ...]  # the -only list this run, in walk order


def _threads_in_window(db: sqlite3.Connection, name: str, window: int) -> List[str]:
    """Distinct threads this source yielded most recently, newest first."""
    rows = db.execute(
        "SELECT DISTINCT thread_id FROM ingest_batch "
        "WHERE source=? AND thread_id IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        (name, window),
    ).fetchall()
    return [r[0] for r in rows]


def _nuggets_of(db: sqlite3.Connection, platform: str,
                thread_ids: Sequence[str]) -> int:
    if not thread_ids:
        return 0
    marks = ",".join("?" for _ in thread_ids)
    row = db.execute(
        f"SELECT COUNT(*) FROM nuggets WHERE platform=? AND thread_id "
        f"IN ({marks})",
        (platform, *thread_ids),
    ).fetchone()
    return int(row[0])


def _window_batches(db: sqlite3.Connection, name: str, window: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM ingest_batch WHERE source=? "
        "ORDER BY id DESC LIMIT ?)",
        (name, window),
    ).fetchone()
    return int(row[0])


def _run_seq(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(DISTINCT run_id) FROM ingest_batch").fetchone()
    return int(row[0])


def _cap(cfg, platform: str) -> int:
    """The walk ceiling as configured today — read, never rewritten."""
    override = (getattr(cfg, "max_threads_per_platform", None) or {}).get(platform)
    if override is not None and int(override) > 0:
        return int(override)
    return int(getattr(cfg, "max_threads_per_source", 1) or 1)


def score_source(db: sqlite3.Connection, cfg, source, window: int) -> SourceScore:
    """One source's ledger score over its own most-recent-batch window."""
    threads = _threads_in_window(db, source.name, window)
    batches = _window_batches(db, source.name, window)
    fresh = _nuggets_of(db, source.platform, threads)
    return SourceScore(
        name=source.name,
        platform=source.platform,
        kind=source.kind,
        threads=len(threads),
        batches=batches,
        fresh_nuggets=fresh,
        cap=_cap(cfg, source.platform),
    )


def _rate_watermark(scores: Sequence[SourceScore]) -> float:
    """The non-zero median, or the single-source stand-in when there is one."""
    rates = [s.rate for s in scores if s.fresh_nuggets > 0]
    if not rates:
        return 0.0
    if len(rates) == 1:
        return SINGLE_NONZERO_HOT_RATE
    return median(rates)


def _group(score: SourceScore, watermark: float) -> str:
    if score.batches == 0 and score.threads == 0:
        return "fresh"
    if score.fresh_nuggets == 0:
        return "zero"
    return "hot" if score.rate >= watermark else "warm"


def _reason(score: SourceScore, group: str, zero_len: int) -> str:
    if group == "fresh":
        return "no batches yet — walking it to get measurements"
    if group == "hot":
        return (f"{score.fresh_nuggets} nuggets from {score.threads} threads "
                f"in its last {score.batches} batches — keep walking")
    if group == "warm":
        return (f"{score.fresh_nuggets} nuggets from {score.threads} threads — "
                "below the median yield; keeps its turn")
    return (f"0 nuggets from {score.threads} threads; probed every "
            f"{max(1, zero_len)} runs")


def source_plan(cfg, db: sqlite3.Connection, sources, *,
                window: Optional[int] = None,
                run_seq: Optional[int] = None) -> Plan:
    """Rank, group and decide, fully deterministically.

    Pure function of (thresholds, archive rows, sources.yaml, run_seq).
    Same inputs → identical plan, byte for byte.
    """
    window = int(getattr(cfg, "scheduler_window_batches", 30)) if window is None \
        else int(window)
    enabled = [s for s in sources if s.enabled]
    scores = [score_source(db, cfg, s, window) for s in enabled]
    watermark = _rate_watermark(scores)

    zero_names = sorted(s.name for s in scores
                        if _group(s, watermark) == "zero"
                        and s.threads >= MIN_ZERO_YIELD_THREADS)
    probe: Optional[str] = None
    if zero_names:
        seq = _run_seq(db) if run_seq is None else int(run_seq)
        probe = zero_names[seq % len(zero_names)]

    decisions: List[Decision] = []
    for s in scores:
        group = _group(s, watermark)
        if group == "zero" and s.threads < MIN_ZERO_YIELD_THREADS:
            # too little data to call a source barren: measure more
            group = "fresh"
        walks = group != "zero" or s.name == probe
        decisions.append(Decision(
            name=s.name,
            platform=s.platform,
            group=group,
            walks_this_run=walks,
            reason=_reason(s, group, len(zero_names)),
            cap=s.cap,
            rate=s.rate,
        ))

    def sort_key(d: Decision):
        return (GROUP_ORDER[d.group], d.name)

    ordered = sorted(decisions, key=sort_key)
    walked = tuple(d.name for d in ordered if d.walks_this_run)
    return Plan(
        run_seq=_run_seq(db) if run_seq is None else int(run_seq),
        window=window,
        decisions=tuple(ordered),
        walked=walked,
    )


def alloc_names(config_dir: str, db_path: str) -> Optional[List[str]]:
    """The `-only` list for this run, when the scheduler has bought it.

    `None` means "no opinion": scheduler off, config unreadable, no ledger
    rows yet, or an empty walk list — the run then behaves exactly as it did
    before this module existed. Any failure falls back the same way; a
    scheduler that must never becomes a run that must not fail.
    """
    try:
        from jester.config import load_thresholds
        from jester.store import open_db

        cfg = load_thresholds(Path(config_dir) / "thresholds.yaml")
        if not getattr(cfg, "scheduler_enabled", False):
            return None
        from jester.sources import load_sources

        sources = load_sources(Path(config_dir) / "sources.yaml")
        db = open_db(db_path, migrate=False)
        try:
            plan = source_plan(cfg, db, sources)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - the legacy walk is the safety net
        return None
    return list(plan.walked) or None


# ---- the console/CLI rendering ------------------------------------------------

def print_plan(cfg, db: sqlite3.Connection, sources) -> Plan:
    """`jester schedule plan` prints this and returns it for replay reuse."""
    plan = source_plan(cfg, db, sources)
    print(f"scheduler: {'ON' if getattr(cfg, 'scheduler_enabled', False) else 'OFF'}"
          f"  (turns are chosen only when ON)")
    print(f"window: {plan.window} most recent batch events per source; "
          f"ledger runs so far: {plan.run_seq}")
    print(f"{'rank':<5}{'source':<22}{'platform':<12}{'group':<6}"
          f"{'cap':<4}{'walk':<6}note")
    for i, d in enumerate(plan.decisions, start=1):
        print(f"{i:<5}{d.name[:21]:<22}{d.platform[:11]:<12}{d.group:<6}"
              f"{d.cap:<4}{'yes' if d.walks_this_run else 'no':<6}{d.reason}")
    return plan


def replay(plan: Plan, cfg, db: sqlite3.Connection, sources, ticks: int):
    """N projected turns, entirely from today's scores (no RNG, no clock).

    Per tick the probe rotation advances the run_seq. The summary counts
    THREAD-TURNS per group — what the walk list spends — against the flat
    legacy baseline, where every enabled source walks every run.
    """
    by_group: Dict[str, int] = {}
    for tick in range(int(ticks)):
        p = source_plan(cfg, db, sources, window=plan.window,
                        run_seq=plan.run_seq + tick)
        print(f"tick {tick + 1}: walk {' '.join(p.walked) or '(nothing)'}")
        for d in p.decisions:
            if d.walks_this_run:
                by_group[d.group] = by_group.get(d.group, 0) + 1
    legacy_turns = int(ticks) * len(sources and [s for s in sources if s.enabled])
    print(f"\n{int(ticks)} turns, flat legacy: every enabled source every run "
          f"({legacy_turns} source-turns)")
    for group in ("hot", "warm", "fresh", "zero"):
        print(f"  {group:<5}: {by_group.get(group, 0)} turns under the plan")
    moved = 0
    zero_turns = by_group.get("zero", 0)
    zero_sources = sum(1 for d in plan.decisions if d.group == "zero")
    if zero_sources:
        legacy_zero = int(ticks) * zero_sources
        moved = legacy_zero - zero_turns
        share = moved / legacy_zero if legacy_zero else 0.0
        print(f"  reallocation: {moved} of {legacy_zero} zero-yield turns "
              f"moved to yielding groups ({share:.0%} of them)")
    return moved


def plan_action(args):
    """The `jester schedule plan` action: score, decide, print, optionally
    replay the walk horizon."""
    from jester.config import load_thresholds
    from jester.sources import load_sources
    from jester.store import open_db

    cfg = load_thresholds(Path(args.config) / "thresholds.yaml")
    db = open_db(args.db, migrate=False)
    try:
        sources = load_sources(Path(args.config) / "sources.yaml")
        plan = print_plan(cfg, db, sources)
        ticks = int(getattr(args, "replay", None) or 0)
        if ticks > 0:
            replay(plan, cfg, db, sources, ticks)
    finally:
        db.close()
    return 0
