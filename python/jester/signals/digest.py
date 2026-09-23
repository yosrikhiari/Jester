"""The weekly digest — repeated needs, with links, counts and what we do not know.

What the digest has to carry: repeated needs, source links, real counts, an
explicit account of what is unknown — and no automatic outreach.

Four things that rules out, and they shape the file more than the things it
asks for:

* **"Repeated"** — a need mentioned once is an anecdote. The digest groups by
  the *work that was named*, and a group of one is reported separately and
  labelled as such, rather than padded into the list to make the week look
  productive.
* **"Source links"** — every claim carries the permalink that produced it.
  A digest whose reader cannot check a line is a digest whose reader has to
  trust it.
* **"Unknowns"** — a section, not a footnote. The easiest way to make a thin
  week look strong is to omit what the data does not say, so what it does not
  say is a heading.
* **"No automatic outreach"** — nothing here produces a contact, a handle to
  message, or a "recommended next action" aimed at a person. It is a reading
  document. `company` and `buyer_intent` stay `unknown`, and the digest says
  so where a reader might otherwise assume otherwise.

A zero-signal week is a valid week and gets a real document saying so, because
"we found nothing" is a finding about the source and pretending otherwise is
how a dead collector goes unnoticed for a month.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import TABLE, outcomes as signal_outcomes
from .filters import load_rules
from .run import RUN_TABLE, ensure_run_table

#: Families whose phrases name *the work*, as opposed to the voice describing
#: it or the money behind it. Grouping on these is what turns 30 records into
#: "five people said invoicing" — grouping on owner_voice would just say
#: "thirty people own a business".
NEED_FAMILIES = ("work_named", "work_pain", "needs_built")

#: A group this size or larger is a repeated need. Two is the smallest number
#: that can repeat; calling one occurrence a trend is the failure mode this
#: constant exists to prevent.
REPEAT_AT = 2

_MATCHED_RE = re.compile(r"\[matched: ([^\]]*)\]")


def _matched_phrases(reason: str) -> List[str]:
    """The phrases the classifier said fired, read back off the stored reason.

    They are parsed rather than recomputed so the digest reports what the
    archive recorded at collection time, not what today's rules would say
    about a truncated excerpt. Those are different claims and only one of them
    is evidence.
    """
    m = _MATCHED_RE.search(reason or "")
    return [p.strip() for p in m.group(1).split(",") if p.strip()] if m else []


def _window(since: str, until: str) -> tuple:
    """A week, or whatever was asked for. Defaults to the last seven days
    ending now, because that is what "weekly digest" means when nobody says."""
    end = datetime.fromisoformat(until.replace("Z", "+00:00")) if until \
        else datetime.now(timezone.utc)
    if since:
        start = datetime.fromisoformat(since.replace("Z", "+00:00"))
    else:
        start = end - timedelta(days=7)
    return (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))


def gather(db: sqlite3.Connection, *, since: str = "", until: str = "",
           mode: str = "live", rules=None) -> dict:
    """Everything the digest reports, as data. Separated from the writing so
    the numbers can be tested without parsing Markdown."""
    rules = rules or load_rules()
    start, end = _window(since, until)
    ensure_run_table(db)

    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM {TABLE} WHERE mode = ? AND first_seen_utc >= ? "
        "AND first_seen_utc <= ? ORDER BY match_confidence DESC",
        (mode, start, end)).fetchall()]

    need_phrases = {p for fam in rules.families if fam.name in NEED_FAMILIES
                    for p in fam.phrases}

    groups: Dict[str, List[dict]] = defaultdict(list)
    ungrouped: List[dict] = []
    for row in rows:
        if row["audience"] not in ("buyer", "practitioner"):
            continue
        named = [p for p in _matched_phrases(row["match_reason"]) if p in need_phrases]
        if not named:
            # Kept but unnamed: the classifier found a voice and a threshold,
            # but no vocabulary for the work itself. Listed apart rather than
            # filed under a need it never stated.
            ungrouped.append(row)
            continue
        for phrase in named:
            groups[phrase].append(row)

    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    repeated = [{"need": n, "records": rs} for n, rs in ranked if len(rs) >= REPEAT_AT]
    once = [{"need": n, "records": rs} for n, rs in ranked if len(rs) < REPEAT_AT]

    runs = [dict(r) for r in db.execute(
        f"SELECT * FROM {RUN_TABLE} WHERE mode = ? AND started_utc >= ? "
        "AND started_utc <= ? ORDER BY started_utc", (mode, start, end)).fetchall()]

    return {
        "window": {"from": start, "to": end, "mode": mode},
        "collected": len(rows),
        "buyer": sum(1 for r in rows if r["audience"] == "buyer"),
        "practitioner": sum(1 for r in rows if r["audience"] == "practitioner"),
        "rejected": sum(1 for r in rows if r["audience"] == "none"),
        "ambiguous": sum(1 for r in rows if "ambiguous" in (r["match_reason"] or "")),
        "inherited": sum(1 for r in rows if "inherited" in (r["match_reason"] or "")),
        "errors": sum(1 for r in rows if r["run_status"] != "ok"),
        "edited": sum(1 for r in rows if r["edited_utc"]),
        "removed": sum(1 for r in rows if r["removed_utc"]),
        "communities": Counter(r["community"] for r in rows).most_common(),
        "queries": Counter(r["query"] for r in rows if r["audience"] != "none").most_common(),
        "dead_queries": [q for q in rules.queries
                         if not any(r["query"] == q and r["audience"] != "none" for r in rows)],
        "repeated_needs": repeated,
        "single_mentions": once,
        "unnamed": ungrouped,
        "runs": runs,
        # What actually came of the signals. Every other number in this
        # digest is the rule set agreeing with itself; this is the only one
        # that comes from a company answering.
        "outcomes": signal_outcomes(db, mode=mode),
        "config_version": getattr(rules, "config_version", ""),
    }


def write_digest(db: sqlite3.Connection, out_dir: str | Path, *, since: str = "",
                 until: str = "", mode: str = "live", rules=None) -> dict:
    data = gather(db, since=since, until=until, mode=mode, rules=rules)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = data["window"]["to"][:10]
    path = out / f"digest-{stamp}.md"
    path.write_text(render(data), encoding="utf-8")
    (out / f"digest-{stamp}.json").write_text(
        json.dumps(_jsonable(data), indent=2, ensure_ascii=False), encoding="utf-8")
    data["path"] = str(path)
    return data


def _jsonable(data: dict) -> dict:
    """Drop the full record rows from the JSON twin; they are in the CSV and
    the archive, and duplicating 400 rows into a digest makes a file nobody
    opens."""
    slim = dict(data)
    for key in ("repeated_needs", "single_mentions"):
        slim[key] = [{"need": g["need"], "count": len(g["records"]),
                      "links": [r["source_url"] for r in g["records"][:20]]}
                     for g in data[key]]
    slim["unnamed"] = [r["source_url"] for r in data["unnamed"][:40]]
    return slim


def _record_line(r: dict) -> str:
    what = (r["title"] or r["excerpt"] or "").strip()
    what = re.sub(r"\s+", " ", what)[:120]
    flag = " · *ambiguous*" if "ambiguous" in (r["match_reason"] or "") else ""
    if "inherited" in (r["match_reason"] or ""):
        flag += " · *voice inherited from the thread*"
    return (f"- **{r['audience']}** ({r['match_confidence']:.2f}{flag}) — {what}  \n"
            f"  `{r['community']}` · [source]({r['source_url']}) · matched "
            f"`{'`, `'.join(_matched_phrases(r['match_reason'])[:6]) or 'nothing recorded'}`")


def render(d: dict) -> str:
    w = d["window"]
    lines = [
        f"# Problem-signal digest — {w['from'][:10]} to {w['to'][:10]}",
        "",
        f"> {d['collected']} records collected in this window, mode `{w['mode']}`, "
        f"rule set v{d['config_version']}. Every line below links to the post it "
        "came from.",
        "",
    ]

    if d["collected"] == 0:
        lines += [
            "## Nothing was collected this week",
            "",
            "This is a finding, not an omission. Either the scheduled run did not "
            "happen or the queries returned nothing — the runs table below says "
            "which, and those are very different problems.",
            "",
        ]

    lines += [
        "## The counts",
        "",
        "| | |",
        "|---|---|",
        f"| collected | {d['collected']} |",
        f"| **buyer signals** | **{d['buyer']}** |",
        f"| practitioner signals (content, not leads) | {d['practitioner']} |",
        f"| collected and rejected | {d['rejected']} |",
        f"| flagged ambiguous | {d['ambiguous']} |",
        f"| buyer voice inherited from a thread | {d['inherited']} |",
        f"| edited since we saw them | {d['edited']} |",
        f"| removed at the source | {d['removed']} |",
        f"| failed fetches | {d['errors']} |",
        "",
    ]

    lines += ["## Repeated needs", ""]
    if d["repeated_needs"]:
        lines.append(f"Work named by **{REPEAT_AT} or more** different records. "
                     "A need mentioned once is an anecdote and is listed further down.")
        lines.append("")
        for group in d["repeated_needs"]:
            lines.append(f"### `{group['need']}` — {len(group['records'])} records")
            lines.append("")
            for r in group["records"][:8]:
                lines.append(_record_line(r))
            if len(group["records"]) > 8:
                lines.append(f"- _… {len(group['records']) - 8} more; all of them in the CSV._")
            lines.append("")
    else:
        lines += ["_Nothing was named by two or more records this week._ With "
                  f"{d['buyer'] + d['practitioner']} kept record(s) in the window, "
                  "that is what the data says rather than a failure of the grouping.", ""]

    if d["single_mentions"]:
        lines += ["### Mentioned once", "",
                  "Listed so they are not lost, and kept apart so they are not "
                  "counted as a trend.", ""]
        for group in d["single_mentions"][:12]:
            r = group["records"][0]
            lines.append(f"- `{group['need']}` — [{(r['title'] or r['excerpt'] or '')[:80]}]"
                         f"({r['source_url']})")
        lines.append("")

    lines += ["## Where it came from", "", "| community | records |", "|---|---|"]
    for community, n in d["communities"][:15]:
        lines.append(f"| `{community}` | {n} |")
    lines += ["", "| phrase that found it | kept records |", "|---|---|"]
    for query, n in d["queries"][:15]:
        lines.append(f"| `{query}` | {n} |")
    lines.append("")

    lines += ["## The runs behind these numbers", ""]
    if d["runs"]:
        lines += ["| started | kind | source | collected | new | errors | status |",
                  "|---|---|---|---|---|---|---|"]
        for r in d["runs"]:
            lines.append(f"| {r['started_utc'][:19]} | {r['kind']} | {r['source']} | "
                         f"{r['collected']} | {r['new']} | {r['errors']} | {r['status']} |")
    else:
        lines.append("_No run was recorded in this window._ If records appeared "
                     "anyway they were collected outside the schedule, which is "
                     "worth knowing.")
    lines.append("")

    o = d.get("outcomes") or {}
    lines += ["## What came of them", ""]
    if not o.get("buyers"):
        lines += ["_No buyer signals in this window._", ""]
    elif not o.get("worked"):
        lines += [
            f"**{o['buyers']} buyer signal(s), none worked yet.** That is the "
            "number worth arguing about. Everything else in this digest is the "
            "rule set agreeing with itself — how precise the collector is "
            "against its own criteria. Whether those criteria pick companies "
            "that reply is untested until somebody works the list and records "
            "what happened (`jester signals outcome`).",
            "",
        ]
    else:
        lines += ["| outcome | count |", "|---|---:|"]
        for name, n in sorted(o["by_outcome"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {name} | {n} |")
        rate = o["replied_or_better"] / o["worked"] * 100 if o["worked"] else 0
        lines += [
            "",
            f"**{o['worked']} of {o['buyers']} worked · "
            f"{o['replied_or_better']} replied or better ({rate:.0f}%).**",
            "",
        ]
        if o.get("unfit"):
            lines += [
                f"**{o['unfit']} marked unfit** — those should never have "
                "reached outreach, and each one is a fault in the rules rather "
                "than a company saying no. They are the most useful records "
                "here: re-read them before changing any phrase.",
                "",
            ]

    lines += [
        "## What this does not tell you",
        "",
        "The honest half. Omitting it is the easiest way to make a thin week "
        "read as a strong one.",
        "",
        "* **No company and no budget.** `company` and `buyer_intent` are "
        "`unknown` on every record and always will be. Someone describing a "
        "problem is not evidence that they have money to solve it, and nothing "
        "here infers one.",
        "* **No identities.** Handles are stored as published. Nobody is "
        "enriched, matched against a contact database, or messaged. This "
        "document produces no outreach and no contact list.",
        f"* **{d['ambiguous']} record(s) are flagged ambiguous** — real evidence, "
        "too thin to lean on. They are shown with the flag rather than promoted "
        "or hidden.",
        f"* **{d['inherited']} buyer voice(s) were inherited** from the thread "
        "rather than stated by the person. Weaker evidence, marked as such.",
        "* **Only what the phrases found.** This is a search, not a census. A "
        "problem nobody phrases the way the query does is invisible here, and "
        "the fix for that is better phrases, not a bigger number.",
    ]
    if d["dead_queries"]:
        lines.append(f"* **{len(d['dead_queries'])} phrase(s) returned nothing "
                     f"worth keeping:** {', '.join('`%s`' % q for q in d['dead_queries'])}. "
                     "Cheap to replace — that is the point of phrases over communities.")
    if d["unnamed"]:
        lines.append(f"* **{len(d['unnamed'])} kept record(s) named no specific work,** so "
                     "they are in no group above. They passed on voice and "
                     "threshold alone.")
    lines.append("")
    return "\n".join(lines) + "\n"
