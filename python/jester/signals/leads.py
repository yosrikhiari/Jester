"""The handover: the buyer signals somebody can actually act on today.

This exists because of a gap the archive could not see. The collector scored
753 adverts, kept 35, and every one of them sat untouched — because outreach
is somebody else's job and nobody had been handed a list. A signal nobody
works is indistinguishable from a signal that does not work.

So this writes the shortest useful thing: the buyer records that carry a
contact route the company published itself, in the order worth working, with
a column for the answer. Nothing here contacts anyone; it produces a file a
person reads.

**Why a contact route is the filter.** A buyer signal with no way to reach the
company is real evidence and a dead end for today. It stays in the archive and
out of this file, and the count of both is reported, so the distinction is
visible rather than quietly applied.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import List

from . import TABLE

#: What counts as a way to reach them. Deliberately broad: an apply link, a
#: careers page and an email address are all routes a person can follow, and
#: which one an advert uses is not our business.
CONTACT_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"          # an address
    r"|https?://[^\s|)\]]+"             # any link, including apply and careers
    r"|\bapply\b|\bcareers\b|\bemail\b|\bcontact\b|\bDM\b|\breach out\b",
    re.I)

#: Pulled out of the excerpt so the reader does not have to. Addresses first
#: because they are unambiguous; a link is the fallback.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
LINK_RE = re.compile(r"https?://[^\s|)\]]+")

COLUMNS = ["outcome", "outcome_note", "company", "buyer_intent", "confidence",
           "headline", "email", "link", "source_url", "why_it_was_kept",
           "first_seen_utc", "record_id"]


def _route(text: str) -> tuple:
    email = EMAIL_RE.search(text or "")
    link = LINK_RE.search(text or "")
    return (email.group(0) if email else "", link.group(0) if link else "")


def gather(db: sqlite3.Connection, *, mode: str = "live",
           include_worked: bool = False) -> dict:
    """The actionable buyer records, ranked, plus what was held back and why."""
    # COALESCE throughout: an archive migrated before these columns carried
    # a default holds NULL where a fresh one holds ''. The query should not
    # depend on every past migration having been right.
    where = ["audience = 'buyer'", "COALESCE(removed_utc, '') = ''"]
    args: List = []
    if mode:
        where.append("mode = ?")
        args.append(mode)
    if not include_worked:
        # Already worked means somebody has it. Handing it over again is how
        # two people email the same company in the same week.
        where.append("COALESCE(outcome, '') = ''")
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM {TABLE} WHERE {' AND '.join(where)} "
        "ORDER BY match_confidence DESC, first_seen_utc DESC", args).fetchall()]

    actionable, unreachable = [], []
    for r in rows:
        (actionable if CONTACT_RE.search(r.get("excerpt") or "") else unreachable).append(r)
    return {"actionable": actionable, "unreachable": unreachable,
            "mode": mode or "all"}


def write_csv(db: sqlite3.Connection, out_dir: str | Path, *, mode: str = "live",
              include_worked: bool = False) -> dict:
    """Write the handover file and report what went into it."""
    data = gather(db, mode=mode, include_worked=include_worked)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "leads.csv"

    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in data["actionable"]:
            email, link = _route(r.get("excerpt") or "")
            w.writerow([
                r.get("outcome", ""),
                r.get("outcome_note", ""),
                r.get("company", ""),
                r.get("buyer_intent", ""),
                f"{float(r.get('match_confidence') or 0):.2f}",
                (r.get("title") or "")[:160],
                email,
                link,
                r.get("source_url", ""),
                (r.get("match_reason") or "")[:200],
                (r.get("first_seen_utc") or "")[:19],
                r.get("record_id", ""),
            ])

    # outcome is the FIRST column on purpose: the file is meant to be edited,
    # and the thing the reader has to fill in should not be off the right edge
    # of the screen behind eleven columns they will not change.
    return {"path": str(path), "written": len(data["actionable"]),
            "unreachable": len(data["unreachable"]), "mode": data["mode"]}
