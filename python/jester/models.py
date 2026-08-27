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
    #: When jester INGESTED this — not when the comment was written. See
    #: created_utc for that; the two used to be conflated under this name.
    timestamp: str = ""
    embedding_id: Optional[str] = None
    synthesized_at: str = ""
    needs_reembed: bool = False
    run_id: str = ""
    trivial: Optional[bool] = None  # R37 judge verdict; None = unset (legacy fallback)
    #: Which extractor actually produced `extracted_insight`, or "fake-llm" for
    #: the deterministic stand-in. Empty on rows written before this existed.
    #:
    #: The archive is only as trustworthy as its ability to say which rows are
    #: real. With a 1,000-request daily allowance and a 17,000-comment queue,
    #: any run long enough to matter WILL cross into the stand-in partway
    #: through, and without this the two are the same row.
    extractor_model: str = ""

    # ---- what the platform published about the comment --------------------
    # Every count here is Optional and defaults to None, which means "this
    # platform does not publish that figure" — a different fact from zero, and
    # the true state more often than not:
    #
    #   downvotes  Reddit stopped publishing per-comment downvotes in 2014.
    #   dislikes   YouTube withdrew dislike counts in December 2021.
    #   upvotes    Hacker News has never published per-comment scores.
    #
    # Defaulting any of them to 0 would report "nobody voted against this" for
    # a number nobody can see (R29).
    author: str = ""
    author_url: str = ""
    comment_url: str = ""
    comment_id: str = ""
    parent_id: str = ""
    depth: Optional[int] = None
    #: When the comment was written, RFC3339 UTC, when the platform says.
    created_utc: str = ""
    #: The platform's own words when a relative age is ALL it publishes
    #: ("1 year ago" — YouTube). Kept verbatim rather than resolved to a date
    #: the page never claimed.
    created_raw: str = ""
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    likes: Optional[int] = None
    dislikes: Optional[int] = None
    replies: Optional[int] = None
    awards: Optional[int] = None
    reads: Optional[int] = None
    edited: Optional[bool] = None
    pinned: Optional[bool] = None
    author_is_op: Optional[bool] = None
    distinguished: Optional[bool] = None
    accepted_answer: Optional[bool] = None

    # ---- the thread / video / topic it came from --------------------------
    post_title: str = ""
    post_url: str = ""
    post_author: str = ""
    post_created_utc: str = ""
    post_score: Optional[int] = None
    #: Reddit's published fraction of votes that were upvotes — the only
    #: downvote signal any of these platforms exposes, and left as the raw
    #: ratio because the score it would be solved against is vote-fuzzed.
    post_upvote_ratio: Optional[float] = None
    post_comment_count: Optional[int] = None
    post_views: Optional[int] = None
    community: str = ""

    #: Platform-specific long tail (Discourse trust levels, YouTube creator
    #: hearts, Reddit content types) as a plain dict.
    extra: Optional[dict] = None


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
