"""The life of a journal, and the gate on every step forward.

    captured -> proposed -> chosen -> researching -> experimenting -> drafting
      -> in_review -> ready -> published -> shared -> measured

Kinds without an experiment (til, note, postmortem) go researching -> drafting.
Any state before `published` can be parked (and resumed) or abandoned. An
abandoned journal keeps every record: a test that went wrong is still a
finding, and may become its own "what failed" post.

A gate is a function that returns the reasons a move is blocked, in plain
words. No reasons -> the move happens and is written to `transition` with the
list of checks that passed. Reasons -> nothing changes and the caller prints
them. That list is also what `journal show` prints as "next", so the thing
blocking an article is always one command away.

Five gates need a human approval (`choose`, `plan`, `review`, `publish`,
`post`). They count only approvals whose actor is `human`, so an agent that
records an approval has recorded a suggestion, not a decision.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from jester import journal as J

FORWARD = ("captured", "proposed", "chosen", "researching", "experimenting",
           "drafting", "in_review", "ready", "published", "shared", "measured")
PARKED, ABANDONED = "parked", "abandoned"
STATES = FORWARD + (PARKED, ABANDONED)

#: States a journal can be parked or abandoned from. Once published, the
#: article is out in the world; taking it back is a different act
#: (withdrawal, not built) and must not look like abandoning a draft.
OPEN = frozenset(FORWARD[:FORWARD.index("published")])

#: Steps backwards. No gate: going back never publishes anything.
BACK = frozenset({
    ("experimenting", "researching"),
    ("drafting", "researching"),
    ("drafting", "experimenting"),
    ("in_review", "drafting"),
    ("ready", "drafting"),
})

#: Measurements a posted article must have before it counts as measured.
MEASURE_AFTER = (timedelta(days=7), timedelta(days=30))

Gate = Callable[[sqlite3.Connection, sqlite3.Row, Dict], List[str]]


def needs_experiment(j: sqlite3.Row) -> bool:
    return j["kind"] in J.EXPERIMENT_KINDS


def next_state(j: sqlite3.Row) -> Optional[str]:
    """The forward step from here, or None at the end / when parked."""
    state = j["state"]
    if state not in FORWARD or state == FORWARD[-1]:
        return None
    if state == "researching" and not needs_experiment(j):
        return "drafting"
    return FORWARD[FORWARD.index(state) + 1]


# ---- the gates --------------------------------------------------------------

def _gate_proposed(db, j, ctx):
    return [] if j["question"].strip() else ["write the question this journal answers (set --question)"]


def _gate_chosen(db, j, ctx):
    out = []
    if needs_experiment(j):
        if not j["metric"].strip():
            out.append("name the metric that will decide it (set --metric)")
        if not j["baseline"].strip():
            out.append("name the baseline it is compared against (set --baseline)")
        if not j["prior_coverage"].strip():
            out.append("list what has already been written on it, and what that missed "
                       "(set --prior-coverage)")
    if not J.human_approvals(db, j["id"], "choose"):
        out.append("you have not chosen it yet (approve choose)")
    return out


def _gate_researching(db, j, ctx):
    return []


def _gate_experimenting(db, j, ctx):
    out = []
    exps = J.experiments(db, j["id"])
    if not exps:
        out.append("write the experiment plan first: hypothesis, metric, threshold, "
                   "baseline, number of runs")
    for e in exps:
        missing = [f for f in ("hypothesis", "metric", "threshold", "baseline") if not e[f].strip()]
        if e["n_planned"] <= 0:
            missing.append("planned runs")
        if missing:
            out.append(f"experiment {e['id']} plan is missing: {', '.join(missing)}")
    if not J.human_approvals(db, j["id"], "plan"):
        out.append("you have not approved the experiment plan (approve plan)")
    return out


def _gate_drafting(db, j, ctx):
    if not needs_experiment(j):
        return []
    out = []
    exps = J.experiments(db, j["id"])
    if not exps:
        out.append("no experiment recorded")
    for e in exps:
        finished = [r for r in J.runs(db, e["id"]) if r["status"] == "ok" and r["raw_path"]]
        if not finished:
            out.append(f"experiment {e['id']} has no finished run with raw data")
        missing = J.missing_answers(e["checklist"])
        if missing:
            out.append(f"experiment {e['id']} checklist unanswered: {', '.join(missing)}")
    return out


def _gate_in_review(db, j, ctx):
    rev = J.latest_revision(db, j["id"])
    if rev is None:
        return ["no saved version yet (save)"]
    out = []
    if ctx.get("file_sha256") and ctx["file_sha256"] != rev["content_sha256"]:
        out.append("the file has changes that are not saved (save)")
    checks = J.latest_checks(db, rev["id"])
    for kind in J.REQUIRED_CHECKS:
        if kind not in checks:
            out.append(f"version {rev['n']} has no {kind} check yet")
        elif not checks[kind]:
            out.append(f"version {rev['n']} fails the {kind} check")
    return out


def _review_matches_latest(db, j) -> Tuple[Optional[sqlite3.Row], List[str]]:
    rev = J.latest_revision(db, j["id"])
    if rev is None:
        return None, ["no saved version yet (save)"]
    reviewed = {a["revision_id"] for a in J.human_approvals(db, j["id"], "review")}
    if rev["id"] not in reviewed:
        return rev, [f"you have not approved version {rev['n']} (approve review)"]
    return rev, []


def _gate_ready(db, j, ctx):
    rev, out = _review_matches_latest(db, j)
    if rev is not None and ctx.get("file_sha256") and ctx["file_sha256"] != rev["content_sha256"]:
        out.append("the file has changes that are not saved (save)")
    return out


def _gate_published(db, j, ctx):
    rev, out = _review_matches_latest(db, j)
    if rev is not None:
        published = {a["revision_id"] for a in J.human_approvals(db, j["id"], "publish")}
        if rev["id"] not in published:
            out.append(f"you have not approved publishing version {rev['n']} (approve publish)")
    if not j["canonical_url"].strip():
        out.append("record where the original lives (set --canonical-url)")
    if not j["disclosure"].strip():
        out.append("say how AI was used in it (set --disclosure)")
    return out


def _posted(db, j) -> List[sqlite3.Row]:
    return [p for p in J.publications(db, j["id"]) if p["status"] == "posted"]


def _gate_shared(db, j, ctx):
    posted = _posted(db, j)
    if not posted:
        return ["nothing has been posted anywhere yet"]
    approved = {a["publication_id"] for a in J.human_approvals(db, j["id"], "post")}
    return [f"the {p['channel']} post has no approval from you"
            for p in posted if p["id"] not in approved]


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _gate_measured(db, j, ctx):
    posted = _posted(db, j)
    if not posted:
        return ["nothing has been posted anywhere yet"]
    out = []
    for p in posted:
        start = _parse(p["posted_at"])
        if start is None:
            out.append(f"the {p['channel']} post has no posting time")
            continue
        seen = [t for t in (_parse(s) for s in J.metric_times(db, p["id"])) if t is not None]
        for after in MEASURE_AFTER:
            if not any(t >= start + after for t in seen):
                out.append(f"the {p['channel']} post has no numbers from {after.days} days after posting")
    return out


GATES: Dict[str, Gate] = {
    "proposed": _gate_proposed,
    "chosen": _gate_chosen,
    "researching": _gate_researching,
    "experimenting": _gate_experimenting,
    "drafting": _gate_drafting,
    "in_review": _gate_in_review,
    "ready": _gate_ready,
    "published": _gate_published,
    "shared": _gate_shared,
    "measured": _gate_measured,
}


# ---- moving -----------------------------------------------------------------

class MoveRefused(J.JournalError):
    def __init__(self, slug: str, to: str, reasons: List[str]):
        self.reasons = reasons
        super().__init__(f"{slug} cannot move to {to}: " + "; ".join(reasons))


def blockers(db: sqlite3.Connection, j: sqlite3.Row, to: str,
             file_sha256: str = "", reason: str = "") -> List[str]:
    """Why `j` cannot move to `to` right now. Empty list = it can."""
    state = j["state"]
    if to not in STATES:
        return [f"{to!r} is not a state ({', '.join(STATES)})"]
    if to == state:
        return [f"already {state}"]
    if to == PARKED:
        return [] if state in OPEN else [f"a {state} journal cannot be parked"]
    if to == ABANDONED:
        if state not in OPEN and state != PARKED:
            return [f"a {state} journal cannot be abandoned"]
        return [] if reason.strip() else ["say why it is abandoned (--reason)"]
    if state == PARKED:
        return [] if to == j["parked_from"] else [f"resume it to {j['parked_from']} first"]
    if (state, to) in BACK:
        if to == "experimenting" and not needs_experiment(j):
            return [f"a {j['kind']} has no experiment step"]
        return []
    if to != next_state(j):
        return [f"{state} can only move forward to {next_state(j) or 'nothing'}"]
    return GATES[to](db, j, {"file_sha256": file_sha256})


def move(db: sqlite3.Connection, slug: str, to: str, actor: str = J.HUMAN,
         note: str = "", reason: str = "", file_sha256: str = "",
         at: Optional[str] = None) -> sqlite3.Row:
    """Move a journal if every gate passes; otherwise raise MoveRefused."""
    j = J.get(db, slug)
    reasons = blockers(db, j, to, file_sha256=file_sha256, reason=reason)
    if reasons:
        raise MoveRefused(slug, to, reasons)
    at = at or J.now_utc()
    sets = {"state": to, "updated_at": at}
    if to == PARKED:
        sets["parked_from"] = j["state"]
    elif j["state"] == PARKED:
        sets["parked_from"] = ""
    if to == ABANDONED:
        sets["abandoned_reason"] = reason.strip()
    if to == "published":
        sets["published_at"] = at
    db.execute(f"UPDATE journal SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
               (*sets.values(), j["id"]))
    passed = _passed_checks(j["state"], to)
    db.execute(
        "INSERT INTO transition (journal_id, from_state, to_state, actor, at, note, checks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (j["id"], j["state"], to, actor, at, note or reason, json.dumps(passed)))
    db.commit()
    return J.get(db, slug)


def _passed_checks(frm: str, to: str) -> List[str]:
    """What the transition row says was checked, for the audit trail."""
    if to in (PARKED, ABANDONED) or frm == PARKED or (frm, to) in BACK:
        return []
    return [f"gate:{to}"]


def next_step(db: sqlite3.Connection, j: sqlite3.Row, file_sha256: str = "") -> Tuple[Optional[str], List[str]]:
    """The next forward state and what stands in its way -- the "red chip"."""
    if j["state"] == PARKED:
        return j["parked_from"], []
    to = next_state(j)
    if to is None:
        return None, []
    return to, blockers(db, j, to, file_sha256=file_sha256)
