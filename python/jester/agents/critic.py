"""M2 critic agent: scores an idea per §7.2 and can re-score as evidence grows
(R49/R53). Competition is never fabricated (R29): if the LLM leaves it None the
rubric imputes 5 and competitor_notes stays NULL.
"""
import json
from typing import Optional

from jester.config import Thresholds
from jester.llm import CriticLLM, model_name
from jester.models import Idea, IdeaScores
from jester.scoring import compute_overall
from jester.store import _now, get_idea, update_idea


def row_to_idea(row) -> Idea:
    competition = row["competition"]
    return Idea(
        id=row["id"],
        title=row["title"] or "",
        problem_statement=row["problem_statement"] or "",
        proposed_solution=row["proposed_solution"] or "",
        supporting_nuggets=json.loads(row["supporting_nuggets"] or "[]"),
        source_threads=row["source_threads"] or 0,
        source_platforms=json.loads(row["source_platforms"] or "[]"),
        scores=IdeaScores(
            demand_signal=row["demand_signal"] or 0.0,
            feasibility=row["feasibility"] or 0.0,
            competition=competition,
            overall=row["overall"] or 0.0,
        ),
        competitor_notes=row["competitor_notes"],
        competition_checked=bool(row["competition_checked"]),
        status=row["status"] or "new",
        last_scored_at=row["last_scored_at"] or "",
        critic_model=row["critic_model"] or "",
        synthesis_model=row["synthesis_model"] or "",
        run_id=row["run_id"] or "",
    )


class Critic:
    def __init__(self, db, config: Thresholds):
        self.db = db
        self.config = config

    def score(self, idea: Idea, critic_llm: CriticLLM) -> Idea:
        """Score (or re-score) an idea in place and persist it."""
        c = critic_llm.score(idea)
        idea.scores = IdeaScores(
            demand_signal=c.demand_signal,
            feasibility=c.feasibility,
            competition=c.competition,
            overall=compute_overall(c.demand_signal, c.feasibility, c.competition),
        )
        idea.competition_checked = c.competition is not None
        idea.competitor_notes = None  # never fabricated (R29/R41)
        idea.critic_model = model_name(critic_llm, self.config.critic_model)
        idea.last_scored_at = _now()
        if idea.id is not None:
            update_idea(self.db, idea)
        return idea

    def score_by_id(self, idea_id: int, critic_llm: CriticLLM) -> Optional[Idea]:
        row = get_idea(self.db, idea_id)
        if row is None:
            return None
        return self.score(row_to_idea(row), critic_llm)
