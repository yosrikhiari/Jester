"""M2 synthesizer agent: clusters unclaimed nuggets into scored ideas.

Golden gate (R48) is structural: every produced idea must cite >=1 real input
nugget, cite no nugget outside the input set, and carry all subscores in 1-10.
A nugget is only marked synthesized_at when it joins an idea (R41/R50); lone
unprocessed nuggets stay NULL.
"""
from collections import defaultdict
from typing import List, Optional

from jester.agents.critic import Critic, apply_scores
from jester.config import Thresholds
from jester.idea_dedup import (
    MergeResult,
    apply_verdict,
    find_duplicate,
    index_idea,
    merge_supporting_nocommit,
    rescore_after_merge,
)
from jester.llm import CriticLLM, SynthesizerLLM, model_name
from jester.models import Idea, Nugget
from jester.scoring import SCORE_MAX, SCORE_MIN
from jester.store import (
    _now,
    claim_nuggets,
    count_claimed_nuggets,
    find_idea_containing_support,
    insert_idea_nocommit,
    mark_needs_score,
    set_synthesized_nocommit,
    unprocessed_nuggets,
)


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
    if idea.needs_score:
        # Unscored by design: the critic did not answer, so there are no
        # subscores to range-check yet. The citation rules above still hold.
        return
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


def _chunk_groups(groups, cap: int, stats=None):
    """Yield (key, rows) with any group larger than `cap` cut into consecutive
    slices of at most `cap` rows. Order within the thread is preserved, so a
    slice is a contiguous stretch of the discussion rather than a random
    sample of it. A trailing slice of one row is folded into the previous
    slice: R50 would otherwise leave that lone nugget unclaimed forever."""
    for key, rows in groups.items():
        if cap <= 0 or len(rows) <= cap:
            yield key, rows
            continue
        if stats is not None:
            stats.split_groups += 1
        slices = [rows[i:i + cap] for i in range(0, len(rows), cap)]
        if len(slices) > 1 and len(slices[-1]) == 1:
            slices[-2].extend(slices.pop())
        for part in slices:
            yield key, part


class Synthesizer:
    def __init__(self, db, config: Thresholds):
        self.db = db
        self.config = config
        # R44 merges from the last run(), for the run summary. Merges are not
        # returned as ideas — nothing new was archived — so without this the
        # run would report "0 ideas" on a night that did real work.
        self.merged: List[MergeResult] = []
        #: Groups whose synthesis fell back to the deterministic stand-in and
        #: were therefore NOT archived. Reported by the caller: "0 ideas
        #: because the model was rate-limited" and "0 ideas because nothing
        #: qualified" are different nights.
        self.skipped_fallback: int = 0
        # True when a `max_ideas` ceiling ended run() with groups still
        # unclustered, so the caller can say so instead of implying the pool
        # was exhausted.
        self.stopped_early: bool = False
        #: Thread groups that exceeded max_nuggets_per_idea and were cut into
        #: chunks this run. Reported so "12 ideas from 3 threads" is legible.
        self.split_groups: int = 0
        #: Ideas archived without scores because the critic did not answer.
        #: Reported, and re-scored by a later run (`rescore_pending`).
        self.unscored: int = 0
        #: Groups skipped because another worker had already claimed part of
        #: their evidence (V3-01). Rare — it needs two writers holding the same
        #: pool — and distinct from a merge: nothing was archived or grown
        #: here, and the unclaimed remainder is still queued for the next run.
        self.claimed_elsewhere: int = 0
        #: Set by run(): the manual resynth path (see reuse_claimed_evidence).
        self.reuse_claimed_evidence: bool = False

    def run(
        self,
        synth_llm: SynthesizerLLM,
        critic_llm: CriticLLM,
        *,
        run_id: str,
        rows: Optional[List] = None,
        checker=None,
        idea_vector=None,
        max_ideas: Optional[int] = None,
        reuse_claimed_evidence: bool = False,
    ) -> List[Idea]:
        """M2.3: `checker` runs the competitor web step before final scoring;
        `idea_vector` enables R44 idea-level dedup. Both default to off, so a
        caller that passes neither gets exactly the M2.2 behaviour.

        `max_ideas` backs the console's "stop at N ideas" goal. Stopping early
        is safe precisely because of R50: a group is only stamped synthesized
        once its idea is persisted, so the groups this run does not reach stay
        unclaimed and are the next run's first work. Merges (R44) do not count
        against the ceiling — they archive no new idea.

        `reuse_claimed_evidence` is the D-16/R56 manual escape hatch
        (`jester resynth`): re-derive a NEW idea from evidence an idea already
        rests on. It is the one caller that may stamp already-claimed nuggets
        and knowingly archive a second idea on the same supported set, so the
        V3-01 duplicate guard does not apply — but the claim and the insert
        are still one transaction.
        """
        # §37.28 resynth: cluster an explicit subset (manual nugget selection)
        # instead of the NULL-pool. Lone-nugget R50 skip still applies.
        self.merged = []
        self.skipped_fallback = 0
        self.reuse_claimed_evidence = bool(reuse_claimed_evidence)
        if rows is None:
            rows = unprocessed_nuggets(self.db)
        if not rows:
            return []

        # R41: group only unclaimed nuggets by (platform, thread_id)
        groups = defaultdict(list)
        for r in rows:
            groups[(r["platform"], r["thread_id"] or "")].append(r)

        ideas: List[Idea] = []
        self.stopped_early = False
        #: Set when the run stopped because the provider's quota ran out, as
        #: distinct from stopping because `max_ideas` was reached. The caller
        #: records a block from it, and "we chose to stop" and "we were cut
        #: off" should not look the same in a run summary.
        self.stopped_on_quota = False
        self.split_groups = 0
        self.unscored = 0
        self.claimed_elsewhere = 0
        for (platform, thread_id), group_rows in _chunk_groups(
            groups, int(self.config.max_nuggets_per_idea or 0), self
        ):
            if max_ideas is not None and max_ideas > 0 and len(ideas) >= max_ideas:
                # No silent caps: the caller reports what was left behind, or
                # "3 ideas" reads as "the archive had only 3 to give".
                self.stopped_early = True
                break
            if len(group_rows) < 2:
                # Lone nugget: leave unprocessed (R50). A singleton could be
                # attached to an existing idea via vector search, but offline we
                # keep it NULL rather than fabricate a cluster.
                continue

            nuggets = [row_to_nugget(r) for r in group_rows]

            # Count fallbacks either side of the call, so a group synthesised
            # by the deterministic stand-in can be told from one the model
            # actually wrote.
            before_fallbacks = int(getattr(synth_llm, "fallbacks", 0) or 0)
            before_limited = int(getattr(synth_llm, "rate_limited", 0) or 0)
            draft = synth_llm.synthesize(nuggets)
            # Either signal is enough: the agent counted a fallback, or the
            # draft names someone other than the agent that was asked (the
            # stand-in signs its drafts "fake-synth"). The stamp also covers
            # an agent with no counters at all.
            who = getattr(draft, "model", "") or ""
            fell_back = (
                int(getattr(synth_llm, "fallbacks", 0) or 0) > before_fallbacks
                or (bool(who) and who != model_name(synth_llm, who)))
            hit_the_limit = (
                int(getattr(synth_llm, "rate_limited", 0) or 0) > before_limited)

            if hit_the_limit:
                # THE QUOTA IS GONE. Stop, do not carry on down the list.
                #
                # cmd_treat's docstring already promises this — "the run stops
                # the moment the limit is hit and leaves the rest queued" — and
                # until now the check sat AFTER the whole run, which made it a
                # post-mortem rather than a brake. The cost was not abstract: a
                # treat run hung for three hours with 27,914 batches queued,
                # because every remaining group spent up to two minutes in
                # `chat`'s retry backoff before falling back. Four scheduled
                # runs in a row ended aborted or still-running, and `done` did
                # not move for a day.
                #
                # One exhaustion is enough to stop on. `_obj` only counts it
                # after `chat` has already made four attempts honouring the
                # server's own Retry-After; if it still says no, waiting on the
                # next group will not help. Everything after this point would
                # be a stand-in, and `_obj`'s docstring is explicit that this
                # is "not a degraded run, it is a run that should not have
                # continued".
                #
                # Nothing is lost. R50 stamps a group synthesised only once its
                # idea is persisted, so every group not reached stays unclaimed
                # and is the next run's first work — the same guarantee that
                # makes `max_ideas` safe.
                self.skipped_fallback += 1
                self.stopped_early = True
                self.stopped_on_quota = True
                break

            if fell_back:
                # A stand-in "idea" is a truncated comment with a score
                # attached. Archiving it is worse than producing nothing:
                # nothing is visibly nothing, while this looks like output.
                #
                # Seen live — a Groq daily token limit turned one night's run
                # into 26 archived ideas titled things like "[hackernews] bro
                # how is this different from just asking Claude?". The run
                # summary DID say it had degraded 36 times; the ideas were
                # written to the archive regardless, and the summary is not
                # what anyone reads a week later.
                self.skipped_fallback += 1
                continue

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
                # What ACTUALLY wrote this, not what the config asked for.
                # With llm_provider=fake the two disagree, and the archive
                # was recording the frontier model name next to a line the
                # deterministic stand-in produced.
                synthesis_model=model_name(synth_llm, self.config.synthesizer_model),
                run_id=run_id,
            )

            # Critic fills the subscores (golden gate R48/R29).
            #
            # When the critic does not answer, the idea is still archived, but
            # unscored and marked `needs_score`. The synthesis call already
            # spent its share of the quota, and the idea it wrote is real; only
            # the numbers are missing, and a later run supplies them. Before,
            # the stand-in's formula went in under the real model's name.
            now = _now()
            critic_limited_before = int(getattr(critic_llm, "rate_limited", 0) or 0)
            scored = apply_scores(idea, critic_llm, self.config)
            critic_hit_the_limit = (
                int(getattr(critic_llm, "rate_limited", 0) or 0) > critic_limited_before)
            if not scored:
                self.unscored += 1

            # M2.3 / D-1: a verified competitor check overrides the model's
            # prior. When it cannot run, R29 applies — competition goes NULL
            # and §7.2 imputes 5 rather than trusting an unverified number.
            # Skipped on an unscored idea: there is no score to fold it into,
            # and the web check has its own daily budget.
            if scored and checker is not None and checker.enabled:
                apply_verdict(idea, checker.check(idea))

            # R48 structural gate: reject before persisting anything.
            golden_gate(idea, set(supporting))

            # R44: an idea the archive already holds gains evidence instead of
            # gaining a duplicate row. The vector compare is one way to find
            # that idea; it is skipped entirely when Qdrant is down, which is
            # why the write below also asks the archive itself.
            match = find_duplicate(idea_vector, idea, self.config.dedup_threshold) \
                if idea_vector is not None else None
            if match is not None:
                existing_id, score = match
                self._absorb(existing_id, score, supporting, now,
                             critic_llm, checker, critic_hit_the_limit)
                if critic_hit_the_limit:
                    self._stop_on_quota()
                    break
                continue

            # V3-01: claim the evidence and write the row as ONE unit.
            outcome, existing_id = self._claim_and_insert(idea, supporting, now)
            if outcome == "duplicate":
                # An idea already rests on this evidence — exactly it, or a
                # superset of it (a merge grew that idea over this group).
                # Some other writer got there first, or this run is a re-run
                # over a stale pool. Merge, exactly as the R44 path does.
                # Score 1.0 because the evidence is already inside that idea,
                # not merely similar to it.
                self._absorb(existing_id, 1.0, supporting, now,
                             critic_llm, checker, critic_hit_the_limit)
                if critic_hit_the_limit:
                    self._stop_on_quota()
                    break
                continue
            if outcome == "partial":
                # Another worker claimed part of this evidence between this
                # run's read and its write. Two ideas sharing provenance is
                # worse than leaving the group for the next run, which is
                # where the nuggets it did not claim still are.
                self.claimed_elsewhere += 1
                continue

            if idea_vector is not None:
                index_idea(idea_vector, idea)
            ideas.append(idea)

            if critic_hit_the_limit:
                # The same brake as the synthesizer's, for the same reason:
                # every idea after this one would be filed unscored. This one
                # is kept (it is a real idea, waiting for a score); the groups
                # not reached stay unclaimed for the next run.
                self._stop_on_quota()
                break

        return ideas

    def _claim_and_insert(self, idea: Idea, supporting, now: str):
        """Claim the nuggets and insert the idea in one transaction.

        Returns ``("inserted", None)`` when the row was written (``idea.id`` is
        set), ``("duplicate", existing_id)`` when the archive already rests on
        this evidence, or ``("partial", None)`` when part of the evidence
        belongs to somebody else.

        A BEGIN IMMEDIATE write lock, not a plain SELECT-then-INSERT: SQLite
        serialises writes but not decisions, and the decision here is "nobody
        has archived this evidence yet". Holding the lock across the duplicate
        check, the claim and the insert is what makes that decision true, and
        means a crash anywhere inside it leaves neither an idea nor a claim —
        so the re-run archives exactly one idea instead of two.

        The check is CONTAINMENT, not equality (V3-01): an idea grown by a
        merge contains the union of its old evidence and the absorbed group,
        so the group that produced the absorbed half matches nothing exactly.
        Equality is the special case, and it is `duplicate` too.
        """
        manual = self.reuse_claimed_evidence
        own_txn = not self.db.in_transaction
        if own_txn:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            if not manual:
                existing = find_idea_containing_support(self.db, supporting)
                if existing is not None:
                    if own_txn:
                        self.db.rollback()
                    return "duplicate", existing
            if manual:
                # The operator asked for this evidence again: stamp it even
                # though it is claimed, as resynth has always done.
                set_synthesized_nocommit(self.db, supporting, now)
            else:
                if count_claimed_nuggets(self.db, supporting) > 0:
                    # Some of this evidence already carries a claim: another
                    # writer archived it between this run's read and its write.
                    if own_txn:
                        self.db.rollback()
                    return "partial", None
                claim_nuggets(self.db, supporting, now)
            idea.id = insert_idea_nocommit(self.db, idea)
            if own_txn:
                self.db.commit()
            return "inserted", None
        except BaseException:
            if own_txn:
                self.db.rollback()
            raise

    def _absorb(self, existing_id, score, supporting, now, critic_llm, checker,
                critic_hit_the_limit) -> None:
        """Merge this group's evidence into `existing_id` and claim it, as ONE
        write, then re-score outside that write.

        V3-01: the merge and the claim used to be two commits, in that order.
        A crash between them left the idea already grown with the newly
        absorbed nuggets still unclaimed; the next run then archived a second
        idea over evidence the first one was resting on. One BEGIN IMMEDIATE
        unit makes the two facts atomic — either the idea holds the evidence
        and the evidence is claimed, or neither happened. The re-score calls a
        model, so it stays outside the lock.
        """
        own_txn = not self.db.in_transaction
        if own_txn:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            grew, added = merge_supporting_nocommit(self.db, existing_id, supporting)
            # R50: stamp synthesized_at ONLY on the nuggets that joined — in
            # the same unit as the merge that took them, so the row and its
            # evidence can never disagree about what this idea rests on.
            set_synthesized_nocommit(self.db, supporting, now)
            if own_txn:
                self.db.commit()
        except BaseException:
            if own_txn:
                self.db.rollback()
            raise
        self.merged.append(MergeResult(existing_id, grew, score, added))
        if grew and critic_hit_the_limit:
            # The critic just refused on quota; asking it again for
            # the re-score would only be refused again. Flag the idea
            # so a later run re-scores it against its new evidence.
            mark_needs_score(self.db, existing_id)
        elif grew:
            # R49: re-score against the evidence it now actually rests on.
            rescore_after_merge(self.db, existing_id, Critic(self.db, self.config),
                                critic_llm, checker)

    def _stop_on_quota(self):
        self.stopped_early = True
        self.stopped_on_quota = True
