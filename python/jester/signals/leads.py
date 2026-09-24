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

from . import EXCERPT_CHARS, TABLE

#: What counts as a way to reach them. Deliberately broad: an apply link, a
#: careers page and an email address are all routes a person can follow, and
#: which one an advert uses is not our business.
#: Every repetition is bounded. The unbounded form took 610 ms on a 9 000
#: character string of dots with no "@" in it — the shape a long advert full
#: of version numbers has — because the engine tried every possible split
#: before giving up. Bounded, the same input takes 8 ms and the same real
#: addresses still match. 64 and 63 are the actual limits for an email local
#: part and a DNS label, so the bounds are the standard's, not invented.
CONTACT_RE = re.compile(
    r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}"   # an address
    r"|https?://[^\s|)\]]{1,300}"       # any link, including apply and careers
    r"|\bapply\b|\bcareers\b|\bemail\b|\bcontact\b|\bDM\b|\breach out\b",
    re.I)

#: Pulled out of the excerpt so the reader does not have to. Addresses first
#: because they are unambiguous; a link is the fallback.
EMAIL_RE = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")
LINK_RE = re.compile(r"https?://[^\s|)\]]{1,300}")

COLUMNS = ["outcome", "outcome_note", "company", "buyer_intent", "confidence",
           "headline", "email", "link", "source_url", "why_it_was_kept",
           "first_seen_utc", "record_id"]


def _complete(match, text: str) -> bool:
    """False when the match runs to the end of a TRUNCATED excerpt.

    The archive stores a 600-character excerpt, not the advert. A contact
    route sitting past that cut comes back amputated — a real lead read
    `https://git`, which is not a link, and this file's whole purpose is a
    route a person can follow. An empty cell says "look at source_url"; a
    broken one says "follow this" and wastes the follow.

    Only a match touching the end of a text that WAS cut is distrusted. A
    short advert whose last words are its apply link is complete, and
    dropping that route would cost more leads than the truncation does.
    """
    if match is None:
        return False
    if match.end() < len(text):
        return True                      # real text after it, so it is whole
    return len(text) < EXCERPT_CHARS     # nothing after it, but nothing was cut


def _route(text: str) -> tuple:
    text = text or ""
    email = EMAIL_RE.search(text)
    link = LINK_RE.search(text)
    # Hand over nothing rather than something that does not resolve. Every row
    # still carries source_url, which is the permalink to the full advert.
    return (email.group(0) if _complete(email, text) else "",
            link.group(0) if _complete(link, text) else "")


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

    dropped = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in data["actionable"]:
            excerpt = r.get("excerpt") or ""
            email, link = _route(excerpt)
            # Count what the truncation cost. A route dropped in silence looks
            # identical to an advert that never carried one, and the two want
            # different things from the reader: one means "open source_url,
            # the link is in the full advert", the other means "there is no
            # route to find".
            if not (email or link) and CONTACT_RE.search(excerpt):
                dropped += 1
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
            "unreachable": len(data["unreachable"]), "mode": data["mode"],
            "route_cut": dropped}
