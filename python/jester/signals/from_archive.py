"""Score records the nuggets pipeline already collected, into the signals archive.

THE GAP THIS CLOSES. There are two pipelines and they never met:

    sources.yaml  -> Go worker -> nuggets           105,388 rows, NO audience
    signal_rules  -> collector -> problem_signal       669 rows, buyer/practitioner

Hiring subreddits were added to `sources.yaml`, so the worker collected them —
18 batches, 54 records — into the pipeline that never asks whether something
is a buyer. The `problem_signal` archive held zero Reddit rows, so the buyer
count did not move and it looked like the rooms had produced nothing.

They had not produced nothing. Scoring those 54 records against
`hiring_rules.yaml` finds one genuine buyer:

    [Hiring] Senior Full-Stack Engineer ($100-$180/hr) - AI Dataset & App
    Building (Next.js / Redis / SQL) - high-paying remote contract

THIS DOES NOT COLLECT ANYTHING. It reads rows already on disk, classifies
them with the rules that already exist, and writes the verdicts where the
leads file can see them. No network, no new access. That distinction matters:
the scope document gates Reddit COLLECTION on written commercial access, and
this is not collection.

`mode` stays 'live' because the records are live — the worker fetched them
from the real site. What changes is only which archive holds the verdict.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Iterable

from . import Signal, upsert
from .filters import classify, load_rules

#: Where a record came from, in the words the signals archive uses. The worker
#: stores a thread URL; the community is the room inside it.
def _community(source: str) -> str:
    s = str(source or "")
    if "/r/" in s:
        return "r/" + s.split("/r/", 1)[1].split("/", 1)[0]
    return s[:60] or "unknown"


#: Reddit's hiring rooms declare the DIRECTION of the deal in the title tag,
#: and the rooms enforce it. That tag beats anything the prose scoring can
#: work out, because a freelancer's advert and a client's advert read almost
#: identically: both name the work, both state a rate, both speak as "I".
#:
#: MEASURED on what is stored: 11 [for hire] + 1 [forhire] + 1 [available]
#: against 7 [hiring]. Sellers outnumber buyers nearly two to one, so without
#: this the majority of every "buyer" found here is a developer touting for
#: work -- the exact opposite of a lead. One of them scored 1.00:
#:
#:     [FOR HIRE] I'm a Web Developer - WordPress, WooCommerce, Shopify - $20/hr
#:
#: Owner voice, named work, a price. The classifier was right about every
#: feature it looked at and wrong about the only thing that mattered.
SELLER_TAGS = frozenset({
    "for hire", "forhire", "for-hire", "hire me", "hireme",
    "available", "available for work", "seeking work", "seeking",
    "looking for work", "open to work", "resume", "cv",
})

#: Not adverts at all. `[Discussion] - devops engineer` scored 1.00 buyer: it
#: mentions the work and the room, and nothing in the prose says "this is
#: people talking, not a company buying".
NOT_AN_ADVERT = frozenset({
    "discussion", "meta", "mod", "mods", "moderator", "question",
    "advice", "rant", "news", "announcement", "psa",
})

_TAG_RE = re.compile(r"\s*[\[\(]([^\]\)]{1,24})[\]\)]")


def _first_line(body: str) -> str:
    lines = (body or "").strip().splitlines()[:1]
    return lines[0].strip() if lines else ""


def leading_tag(body: str) -> str:
    """The bracketed tag a Reddit post opens with, lowercased, or "".

    Positional on purpose. A post that merely CONTAINS "for hire" somewhere in
    its prose ("we are not for hire") is not making a declaration; a post that
    OPENS with [For Hire] is, and that is the one the room enforces.
    """
    first = (body or "").strip().splitlines()[:1]
    if not first:
        return ""
    m = _TAG_RE.match(first[0])
    return m.group(1).strip().lower() if m else ""


def _records(db: sqlite3.Connection, slugs: set[str]) -> Iterable[tuple]:
    """Every stored record from the named rooms, newest batch first."""
    db.row_factory = sqlite3.Row
    for row in db.execute(
        "SELECT source, thread_id, comments FROM ingest_batch "
        "WHERE platform='reddit' ORDER BY id DESC"
    ):
        src = str(row["source"] or "")
        if slugs and not any(s in src.lower() for s in slugs):
            continue
        try:
            items = json.loads(row["comments"] or "[]")
        except (TypeError, ValueError):
            continue
        for item in items:
            body = str(item.get("body") or "").strip()
            if body:
                yield src, row["thread_id"], item, body


def score_archive(nuggets_db: sqlite3.Connection, signals_db: sqlite3.Connection,
                  *, slugs: set[str], rules_path: str, run_id: str) -> dict:
    """Classify collected records and upsert the ones worth keeping.

    Returns the counts, including how many were rejected — a run that keeps
    nothing and a run that read nothing are different results, and only one of
    them is a reason to look at the rules.
    """
    rules = load_rules(rules_path)
    seen = kept = 0
    by_audience: dict[str, int] = {}
    rejected: dict[str, int] = {}
    batch: list[Signal] = []

    for src, thread_id, item, body in _records(nuggets_db, slugs):
        seen += 1
        # The tag decides before the prose gets a vote. Counted, not dropped
        # in silence: "12 buyers" and "12 buyers after discarding 13 sellers"
        # describe very different archives, and the second one is the truth.
        tag = leading_tag(body)
        if tag in SELLER_TAGS:
            rejected["seller"] = rejected.get("seller", 0) + 1
            continue
        if tag in NOT_AN_ADVERT:
            rejected["not_an_advert"] = rejected.get("not_an_advert", 0) + 1
            continue
        # A vacancy is never posed as a question. "Micro1, Mercor and Ethos?"
        # scored 1.00 buyer -- a CANDIDATE asking whether the interview
        # platforms he had been through were legitimate. It named the work and
        # quoted "$30-100/hr", so every feature the scorer looks at said
        # buyer. Structural, like the tag: it does not depend on guessing
        # which words a job-seeker happens to use. A tagged [Hiring] post
        # keeps its exemption, since that tag is the stronger statement.
        if tag != "hiring" and _first_line(body).endswith("?"):
            rejected["question"] = rejected.get("question", 0) + 1
            continue
        verdict = classify(body, rules)
        by_audience[verdict.audience] = by_audience.get(verdict.audience, 0) + 1
        if verdict.audience == "none":
            continue
        source_id = str(item.get("id") or "").strip() or f"{thread_id}:{seen}"
        batch.append(Signal(
            source_id=source_id,
            platform="reddit",
            source_url=str(item.get("permalink") or src),
            community=_community(src),
            kind="post",
            author=str(item.get("author") or ""),
            title=body.splitlines()[0][:150] if body else "",
            text=body,
            created_utc=str(item.get("created_at") or ""),
            query=f"archive:{_community(src)}",
            mode="live",
            run_id=run_id,
            audience=verdict.audience,
            match_confidence=verdict.confidence,
            match_reason=verdict.reason,
            relevant=1,
            # Never inferred. A subreddit advert names no company field, and
            # guessing one out of prose is the inference this collector does
            # not make.
            company="unknown",
            buyer_intent="unknown",
        ))
        kept += 1

    if batch:
        upsert(signals_db, batch)
    return {"seen": seen, "kept": kept, "by_audience": by_audience,
            "rejected": rejected}


def hiring_slugs(sources_path: str = "config/sources.yaml") -> set[str]:
    """The rooms whose posts are adverts, read from sources.yaml.

    `posts_only: true` is already the worker's marker for "the advert is the
    post, the replies are applicants". Reading it back here means the room
    list has one home: add a subreddit to sources.yaml and scoring picks it
    up, with no second list to forget to update.

    The Python Source dataclass does not carry the field -- only the Go one
    does -- so this reads the file directly rather than through load_sources.
    """
    import yaml

    with open(sources_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out: set[str] = set()
    for entry in (doc.get("sources") or []):
        if not isinstance(entry, dict) or not entry.get("posts_only"):
            continue
        url = str(entry.get("url") or "")
        if "/r/" in url:
            out.add("r/" + url.split("/r/", 1)[1].strip("/").split("/")[0].lower())
    return out
