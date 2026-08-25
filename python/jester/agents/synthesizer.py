"""M2 synthesizer agent: clusters unclaimed nuggets into scored ideas.

Golden gate (R48) is structural: every produced idea must cite >=1 real input
nugget, cite no nugget outside the input set, and carry all subscores in 1-10.
A nugget is only marked synthesized_at when it joins an idea (R41/R50); lone
unprocessed nuggets stay NULL.
"""
from collections import defaultdict
from typing import List, Optional

from jester.agents.critic import Critic
from jester.config import Thresholds
from jester.idea_dedup import (
    MergeResult,
    apply_verdict,
    find_duplicate,
    index_idea,
    merge_supporting,
    rescore_after_merge,
)
from jester.llm import CriticLLM, SynthesizerLLM
from jester.models import Idea, IdeaScores, Nugget
from jester.scoring import compute_overall, SCORE_MAX, SCORE_MIN
from jester.store import _now, insert_idea, set_synthesized, unprocessed_nuggets


class GoldenGateError(ValueError):
    """Raised when a synthesized idea violates the R48 structural gate."""


def golden_gate(idea: Idea, input_keys: set) -> None:
    """Structural gate for synthesizer output (R48).

    Every produced idea must cite >=1 real input nugget, cite no nugget
    outside the input set, and carry all subscores within 1-10. Competition
    may be None when the critic left it unchecked (R29) and is then allowed.
    """
    supporting = idea.supporting_nuggets or []
    if not supporting:
        raise GoldenGateError("idea cites no input nuggets (R48)")
    outside = [k for k in supporting if k not in input_keys]
    if outside:
        raise GoldenGateError(f"idea cites nugget(s) outside input set (R48): {outside}")
    for name, val in (
        ("demand_signal", idea.scores.demand_signal),
        ("feasibility", idea.scores.feasibility),
    ):
        if not (SCORE_MIN <= val <= SCORE_MAX):
            raise GoldenGateError(f"{name} out of 1-10 range (R48): {val}")
    if idea.scores.competition is not None and not (
        SCORE_MIN <= idea.scores.competition <= SCORE_MAX
    ):
        raise GoldenGateError(
            f"competition out of 1-10 range (R48): {idea.scores.competition}"
        )


def row_to_nugget(row) -> Nugget:
    return Nugget(
        unique_key=row["unique_key"],
        platform=row["platform"] or "",
        source_url=row["source_url"] or "",
        thread_id=row["thread_id"] or "",
        raw_text=row["raw_text"] or "",
        extracted_insight=row["extracted_insight"] or "",
        category=row["category"] or "",
        engagement_score=float(row["engagement_score"] or 0.0),
        timestamp=row["timestamp"] or "",
        embedding_id=row["embedding_id"],
        synthesized_at=row["synthesized_at"] or "",
        needs_reembed=bool(row["needs_reembed"]),
        run_id=row["run_id"] or "",
    )


class Synthesizer:
    def __init__(self, db, config: Thresholds):
        self.db = db
        self.config = config
        # R44 merges from the last run(), for the run summary. Merges are not
        # returned as ideas — nothing new was archived — so without this the
        # run would report "0 ideas" on a night that did real work.
        self.merged: List[MergeResult] = []

    def run(
        self,
        synth_llm: SynthesizerLLM,
        critic_llm: CriticLLM,
        *,
        run_id: str,
        rows: Optional[List] = None,
        checker=None,
        idea_vector=None,
    ) -> List[Idea]:
        """M2.3: `checker` runs the competitor web step before final scoring;
        `idea_vector` enables R44 idea-level dedup. Both default to off, so a
        caller that passes neither gets exactly the M2.2 behaviour."""
        # §37.28 resynth: cluster an explicit subset (manual nugget selection)
        # instead of the NULL-pool. Lone-nugget R50 skip still applies.
        self.merged = []
        if rows is None:
            rows = unprocessed_nuggets(self.db)
        if not rows:
            return []

        # R41: group only unclaimed nuggets by (platform, thread_id)
        groups = defaultdict(list)
        for r in rows:
            groups[(r["platform"], r["thread_id"] or "")].append(r)

        ideas: List[Idea] = []
        for (platform, thread_id), group_rows in groups.items():
            if len(group_rows) < 2:
                # Lone nugget: leave unprocessed (R50). A singleton could be
                # attached to an existing idea via vector search, but offline we
                # keep it NULL rather than fabricate a cluster.
                continue

            nuggets = [row_to_nugget(r) for r in group_rows]
            draft = synth_llm.synthesize(nuggets)

            platforms = sorted({n.platform for n in nuggets})
            threads = sorted({n.thread_id for n in nuggets})
            supporting = [n.unique_key for n in nuggets]

            idea = Idea(
                title=draft.title,
                problem_statement=draft.problem_statement,
                proposed_solution=draft.proposed_solution,
                supporting_nuggets=supporting,
                source_threads=len(threads),
                source_platforms=platforms,
                status="new",
                synthesis_model=self.config.synthesizer_model,
                run_id=run_id,
            )

            # Critic fills the subscores (golden gate R48/R29).
            now = _now()
            c = critic_llm.score(idea)
            idea.scores = IdeaScores(
                demand_signal=c.demand_signal,
                feasibility=c.feasibility,
                competition=c.competition,
                overall=compute_overall(c.demand_signal, c.feasibility, c.competition),
            )
            idea.competition_checked = c.competition is not None
            idea.competitor_notes = None  # never fabricated (R29/R41)
            idea.critic_model = self.config.critic_model
            idea.last_scored_at = now

            # M2.3 / D-1: a verified competitor check overrides the model's
            # prior. When it cannot run, R29 applies — competition goes NULL
            # and §7.2 imputes 5 rather than trusting an unverified number.
            if checker is not None and checker.enabled:
                apply_verdict(idea, checker.check(idea))

            # R48 structural gate: reject before persisting anything.
            golden_gate(idea, set(supporting))

            # R44: an idea the archive already holds gains evidence instead of
            # gaining a duplicate row.
            match = find_duplicate(idea_vector, idea, self.config.dedup_threshold) \
                if idea_vector is not None else None
            if match is not None:
                existing_id, score = match
                grew, added = merge_supporting(self.db, existing_id, supporting)
                self.merged.append(MergeResult(existing_id, grew, score, added))
                if grew:
                    # R49: re-score against the evidence it now actually rests on.
                    rescore_after_merge(self.db, existing_id, Critic(self.db, self.config),
                                        critic_llm, checker)
                set_synthesized(self.db, supporting, now)
                continue

            idea.id = insert_idea(self.db, idea)
            if idea_vector is not None:
                index_idea(idea_vector, idea)
            # R50: stamp synthesized_at ONLY on the nuggets that joined.
            set_synthesized(self.db, supporting, now)
            ideas.append(idea)

        return ideas
