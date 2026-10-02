"""M2 critic agent: scores an idea per §7.2 and can re-score as evidence grows
(R49/R53). Competition is never fabricated (R29): if the LLM leaves it None the
rubric imputes 5 and competitor_notes stays NULL.

A critic that was asked and did not answer leaves no score behind. Its call
falls back to the deterministic stand-in, which is right for keeping a run
alive and wrong for the archive: those numbers are a formula of the nugget
count, not a judgement. So the idea is filed with `needs_score` instead, its
scores absent (new idea) or left as they were (an idea that grew), and a later
run scores it for real (`rescore_pending`).
"""
import json
import sqlite3
from typing import Optional

from jester.config import Thresholds
from jester.llm import CriticLLM, FakeCriticLLM, critic_answered, model_name
from jester.models import Idea, IdeaScores
from jester.scoring import compute_overall
from jester.store import _now, get_idea, update_idea


def row_to_idea(row) -> Idea:
    # Scores stay None when the row has none. Turning NULL into 0.0 here made
    # an unscored idea look like one the critic had scored at the bottom of
    # the scale, and a later update wrote those zeros back as if they were.
    return Idea(
        id=row["id"],
        title=row["title"] or "",
        problem_statement=row["problem_statement"] or "",
        proposed_solution=row["proposed_solution"] or "",
        supporting_nuggets=json.loads(row["supporting_nuggets"] or "[]"),
        source_threads=row["source_threads"] or 0,
        source_platforms=json.loads(row["source_platforms"] or "[]"),
        scores=IdeaScores(
            demand_signal=row["demand_signal"],
            feasibility=row["feasibility"],
            competition=row["competition"],
            overall=row["overall"],
        ),
        competitor_notes=row["competitor_notes"],
        competition_checked=bool(row["competition_checked"]),
        status=row["status"] or "new",
        last_scored_at=row["last_scored_at"] or "",
        critic_model=row["critic_model"] or "",
        synthesis_model=row["synthesis_model"] or "",
        run_id=row["run_id"] or "",
        needs_score=bool(_get(row, "needs_score", 0)),
    )


def _get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def apply_scores(idea: Idea, critic_llm: CriticLLM, config: Thresholds) -> bool:
    """Ask the critic and write its answer onto `idea`. True when it answered.

    When the critic that was asked did not answer, nothing it returned is
    kept. A new idea is left unscored (all four numbers None); an existing
    one keeps the scores it had, which are now stale rather than wrong. Either
    way `needs_score` is set so a later run tries again.
    """
    c = critic_llm.score(idea)
    if not critic_answered(c, critic_llm):
        idea.needs_score = True
        if idea.id is None:
            idea.scores = IdeaScores(demand_signal=None, feasibility=None,
                                     competition=None, overall=None)
            idea.competition_checked = False
            idea.critic_model = ""
        return False
    idea.scores = IdeaScores(
        demand_signal=c.demand_signal,
        feasibility=c.feasibility,
        competition=c.competition,
        overall=compute_overall(c.demand_signal, c.feasibility, c.competition),
    )
    idea.competition_checked = c.competition is not None
    idea.competitor_notes = None  # never fabricated (R29/R41)
    # What answered, not what the config asked for (see CriticDraft.model).
    idea.critic_model = c.model or model_name(critic_llm, config.critic_model)
    idea.last_scored_at = _now()
    idea.needs_score = False
    return True


class Critic:
    def __init__(self, db, config: Thresholds):
        self.db = db
        self.config = config

    def score(self, idea: Idea, critic_llm: CriticLLM) -> Idea:
        """Score (or re-score) an idea in place and persist it."""
        apply_scores(idea, critic_llm, self.config)
        if idea.id is not None:
            update_idea(self.db, idea)
        return idea

    def score_by_id(self, idea_id: int, critic_llm: CriticLLM) -> Optional[Idea]:
        row = get_idea(self.db, idea_id)
        if row is None:
            return None
        return self.score(row_to_idea(row), critic_llm)


def pending_scores(db) -> int:
    """How many ideas are waiting for the critic."""
    return db.execute("SELECT COUNT(*) FROM ideas WHERE needs_score=1").fetchone()[0]


def rescore_pending(db, config: Thresholds, critic_llm: CriticLLM, limit: int) -> dict:
    """Give up to `limit` waiting ideas a real score, oldest first.

    Stops at the first rate-limit exhaustion, for the reason the synthesizer
    does: every call after it would be another stand-in. An idea whose critic
    still does not answer stays waiting; nothing about it changes.
    """
    out = {"scored": 0, "still_waiting": 0, "stopped_on_quota": False}
    if limit <= 0:
        out["still_waiting"] = pending_scores(db)
        return out
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM ideas WHERE needs_score=1 ORDER BY id LIMIT ?", (int(limit),)
    ).fetchall()
    critic = Critic(db, config)
    for row in rows:
        before = int(getattr(critic_llm, "rate_limited", 0) or 0)
        idea = critic.score(row_to_idea(row), critic_llm)
        if not idea.needs_score:
            out["scored"] += 1
        if int(getattr(critic_llm, "rate_limited", 0) or 0) > before:
            out["stopped_on_quota"] = True
            break
    out["still_waiting"] = pending_scores(db)
    return out


# ---- ideas scored by the stand-in before S1 ---------------------------------
#
# Before the critic recorded who answered, a failed call left the stand-in's
# numbers on the idea under the real model's name. They cannot be told apart
# by provenance, only by shape: FakeCriticLLM is a formula, so its output is
# recognisable exactly. demand = min(10, 2 + supporting nuggets), feasibility
# 5, competition unchecked. On the live archive this matched 602 of 2,588
# ideas; a real gpt-oss critic left competition unchecked only 13 times in the
# other 1,960, so the shape is a strong signal, not a guess.

#: Columns written to the audit file, in order: enough to undo the marking.
STANDIN_AUDIT_COLUMNS = ("id", "title", "demand_signal", "feasibility", "competition",
                         "overall", "competition_checked", "critic_model",
                         "last_scored_at", "created_at")


def _has_column(db, table, column) -> bool:
    return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))


def find_standin_scored(db) -> list:
    """Ideas whose scores match the stand-in critic's formula exactly.

    Read-only, and safe on an archive that predates the `needs_score` column,
    so a dry run can be pointed at the live database without migrating it.
    Skipped: ideas already waiting for a score, and ideas stamped
    `fake-critic`, which is a deliberate `critic_provider: fake` setting.
    """
    db.row_factory = sqlite3.Row
    waiting = "AND needs_score = 0 " if _has_column(db, "ideas", "needs_score") else ""
    rows = db.execute(
        "SELECT * FROM ideas WHERE feasibility = 5.0 AND competition IS NULL "
        "AND demand_signal IS NOT NULL "
        f"AND COALESCE(critic_model, '') != ? {waiting}ORDER BY id",
        (FakeCriticLLM.name,),
    ).fetchall()
    out = []
    for r in rows:
        n = len(json.loads(r["supporting_nuggets"] or "[]"))
        if r["demand_signal"] == min(10.0, 2.0 + n):
            out.append(r)
    return out


def mark_standin_scored(db, rows, audit_path) -> int:
    """Clear the stand-in's numbers and queue the ideas for a real score.

    Writes `audit_path` (CSV, one row per idea, the values being removed)
    BEFORE touching the archive, and marks everything in one transaction, so
    the step is either complete and recorded or did not happen. The ideas end
    up exactly like an idea S1 files when its critic does not answer: scores
    absent, no critic named, `needs_score` set. `status` is not touched.
    """
    import csv
    from pathlib import Path

    rows = list(rows)
    if not rows:
        return 0
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(STANDIN_AUDIT_COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in STANDIN_AUDIT_COLUMNS])
    with db:
        db.executemany(
            "UPDATE ideas SET demand_signal=NULL, feasibility=NULL, competition=NULL, "
            "overall=NULL, competition_checked=0, critic_model='', last_scored_at=NULL, "
            "needs_score=1 WHERE id=?",
            [(r["id"],) for r in rows],
        )
    return len(rows)
