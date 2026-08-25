"""M2.3 / R44: idea-level dedup, and the R49 re-score that follows a merge.

Nugget dedup (M1.5) stops the same *comment* being archived twice. It does
nothing about the same *idea* being synthesized again next week from a fresh
batch of comments saying the same thing — which is how an archive fills with
near-identical entries and the review loop stops being worth running.

So: before an idea is inserted, it is compared against the ideas already in the
archive by embedding cosine. A near-duplicate does not become a second row; its
supporting nuggets are merged into the existing idea. And because that idea now
rests on more evidence than it was scored against, it is re-scored and its
`last_scored_at` advances (R49).
"""
import json
from dataclasses import dataclass
from typing import List, Optional

from jester.store import _now, get_idea, update_idea

# The text an idea is embedded as. Title alone is too thin (two different ideas
# often share a noun phrase); the problem statement carries the actual claim.
def idea_text(idea) -> str:
    title = (getattr(idea, "title", "") or "").strip()
    problem = (getattr(idea, "problem_statement", "") or "").strip()
    return f"{title}. {problem}".strip(". ").strip()


@dataclass(frozen=True)
class MergeResult:
    """What happened to a candidate idea."""

    duplicate_of: Optional[int]   # existing idea id, or None when it is new
    grew: bool                    # did the existing idea gain nuggets? (drives R49)
    score: float = 0.0            # cosine against the matched idea
    added: tuple = ()             # the nugget keys actually merged in


def index_idea(vector, idea) -> None:
    """Record an idea in the idea vector collection so later runs can match it."""
    if idea.id is None:
        return
    vector.upsert(f"idea:{idea.id}", idea_text(idea), {"idea_id": idea.id})


def find_duplicate(vector, idea, threshold: float) -> Optional[tuple]:
    """Nearest existing idea at or above `threshold`, as (idea_id, score)."""
    text = idea_text(idea)
    if not text:
        return None
    for score, key in vector.search_scored(text, limit=5):
        if score < threshold:
            continue
        if not key or not str(key).startswith("idea:"):
            continue
        try:
            return int(str(key).split(":", 1)[1]), float(score)
        except (ValueError, IndexError):
            continue
    return None


def merge_supporting(db, existing_id: int, new_keys: List[str]) -> tuple:
    """Union the candidate's nuggets into the existing idea.

    Returns (grew, added_keys). Order is preserved and existing keys keep their
    position, so a merge never reshuffles the citation list a reviewer read.
    """
    row = get_idea(db, existing_id)
    if row is None:
        return False, ()
    current = json.loads(row["supporting_nuggets"] or "[]")
    seen = set(current)
    added = [k for k in new_keys if k and k not in seen]
    if not added:
        return False, ()
    merged = current + added
    db.execute(
        "UPDATE ideas SET supporting_nuggets=?, source_threads=? WHERE id=?",
        (json.dumps(merged), max(row["source_threads"] or 0, len(merged)), existing_id),
    )
    db.commit()
    return True, tuple(added)


def rescore_after_merge(db, existing_id: int, critic, critic_llm, checker=None):
    """R49: an idea that gained evidence is re-scored before the archive write.

    Without this the score on screen belongs to a smaller evidence set than the
    citations underneath it — the exact mismatch R49 exists to prevent.
    """
    from jester.agents.critic import row_to_idea  # local: avoids an import cycle

    row = get_idea(db, existing_id)
    if row is None:
        return None
    idea = row_to_idea(row)
    idea = critic.score(idea, critic_llm)
    if checker is not None and checker.enabled:
        verdict = checker.check(idea)
        apply_verdict(idea, verdict)
        update_idea(db, idea)
    idea.last_scored_at = idea.last_scored_at or _now()
    return idea


def apply_verdict(idea, verdict) -> None:
    """Fold a CompetitorVerdict into an idea's scores (R29 on failure)."""
    from jester.scoring import compute_overall

    idea.scores.competition = verdict.competition
    idea.competition_checked = bool(verdict.checked)
    idea.competitor_notes = verdict.notes
    idea.scores.overall = compute_overall(
        idea.scores.demand_signal, idea.scores.feasibility, verdict.competition
    )
