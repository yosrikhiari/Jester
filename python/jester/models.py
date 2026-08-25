"""Shared data model for jester nuggets and synthesized ideas."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Nugget:
    unique_key: str
    platform: str = ""
    source_url: str = ""
    thread_id: str = ""
    raw_text: str = ""
    extracted_insight: str = ""
    category: str = ""
    engagement_score: float = 0.0
    timestamp: str = ""
    embedding_id: Optional[str] = None
    synthesized_at: str = ""
    needs_reembed: bool = False
    run_id: str = ""
    trivial: Optional[bool] = None  # R37 judge verdict; None = unset (legacy fallback)


@dataclass
class IdeaScores:
    """§7.2 subscores (all 1-10). `competition` is nullable: None means the
    critic did NOT verify it (never fabricated). Overall is always derived."""

    demand_signal: float = 0.0
    feasibility: float = 0.0
    competition: Optional[float] = None
    overall: float = 0.0


@dataclass
class Idea:
    id: Optional[int] = None
    title: str = ""
    problem_statement: str = ""
    proposed_solution: str = ""
    supporting_nuggets: List[str] = field(default_factory=list)
    source_threads: int = 0
    source_platforms: List[str] = field(default_factory=list)
    scores: IdeaScores = field(default_factory=IdeaScores)
    competitor_notes: Optional[str] = None
    competition_checked: bool = False
    status: str = "new"  # new | reviewed | archived | building
    last_scored_at: str = ""
    critic_model: str = ""
    synthesis_model: str = ""
    run_id: str = ""
