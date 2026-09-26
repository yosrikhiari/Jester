"""The scope/access document, generated from the rules that actually run.

What it has to state: which communities and queries are in scope, which fields
are stored, what it costs and what permission it runs on. The scope owner
signs it; this module drafts it.

It is generated rather than typed so it cannot drift from the collector. The
communities and queries come from `config/signal_rules.yaml`, the field list
from the field map, the retention numbers from the code that enforces them. If
someone edits the rules and forgets the document, the document changes with
them — and `scope.status` says in the file itself whether it has been
signed.

The access section states the thing that decides the collector's shape:
Reddit's free Data API tier is for non-commercial use, so commercial work needs
a paid agreement plus written approval covering storage *and* AI processing.
That is the scope owner's decision and budget, and if it is refused the work
moves to a pre-named replacement source.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import EXCERPT_CHARS, FIELDS, field_map_markdown
from .filters import Rules, load_rules


def _live_sources():
    """Every registered collector, read from the registry.

    Imported here rather than at module scope because the registry imports the
    collectors, and a document generator should not be the reason a network
    client gets imported when the rules alone were wanted.
    """
    from .sources import available
    return sorted(available(), key=lambda s: s["name"])

#: Pre-named alternatives, so choosing one is a confirmation and not a week
#: of drift. Each already has a working adapter in this repo.
REPLACEMENT_SOURCES = [
    ("Hacker News (Algolia Search API)", "public, keyless, documented",
     "Ask HN is the densest software-delivery pain feed in the source list; adapter already ingesting"),
    ("Discourse instances (latest.json / t/<id>.json)", "public per-instance JSON",
     "one adapter reaches many forums; operator and practitioner pain"),
    ("Stack Exchange (official API)", "public, documented, quota'd",
     "explicit problem statements by construction"),
    ("Lemmy (public API)", "public",
     "Reddit-shaped discussion without the terms question"),
]


def scope_markdown(rules: Rules | None = None, *, today: str = "") -> str:
    r = rules or load_rules()
    when = today or date.today().isoformat()
    approved = r.approved
    comms = (r.scope or {}).get("communities", [])
    ret = (r.scope or {}).get("retention", {})

    lines = []
    add = lines.append
    add("# Problem-signal collector — scope and access status")
    add("")
    add(f"> **Generated {when}** from `config/signal_rules.yaml` and the collector's field map. "
        "Not typed by hand: if the rules change, this changes with them.")
    # The unapproved branch used to append "awaiting sign-off" to a status
    # that already said it, so the line read "PROVISIONAL — awaiting sign-off
    # · awaiting sign-off". Only the approved branch has anything to add.
    add(f"> **Scope status: {'APPROVED' if approved else 'PROVISIONAL — awaiting sign-off'}**"
        + (f" · decided by {(r.scope or {}).get('decided_by')} on {(r.scope or {}).get('decided_on')}"
           if approved else ""))
    add("")
    # WHAT RUNS, before what is proposed.
    #
    # This document exists to answer "scope, fields and access status", and it
    # answered two of the three. Every community below is a Reddit room that
    # is PROPOSED and awaiting sign-off, and section 6 says plainly there is
    # no Reddit code path — so a reader reasonably concluded that nothing was
    # collecting. Two collectors were running daily the whole time.
    #
    # Generated from the source registry, not typed, for the same reason as
    # the rest of the file: a hand-written list of what runs is a list that
    # stops being true.
    add("## 0. What is collecting today")
    add("")
    add("Sections 1 and 2 are the PROPOSED scope and need a decision. This "
        "section is what the collectors actually read right now, and it needs "
        "no decision from anyone — every source below runs on a public keyless "
        "API, so none of it waits on the access question in section 6.")
    add("")
    add("| collector | platform | authority it runs on |")
    add("|---|---|---|")
    for s in _live_sources():
        add(f"| `{s['name']}` | {s['platform']} | {s['access']} |")
    add("")
    add("They ask different questions and carry different rule sets. One reads "
        "forum discussion for people describing a problem; the other reads "
        "hiring adverts, where a company states what it will pay for. Measured "
        "on the archive, the second finds buyers at roughly ten times the rate "
        "of the first, which is why both are scheduled rather than one.")
    add("")
    add("## 1. Communities")
    add("")
    add("The scope owner owns this list: it defines the buyer test. Every line says whether it was "
        "**measured** against posts already in the archive or is **proposed** and still "
        "unverified, so nothing here is an assumption wearing a recommendation's clothes.")
    add("")
    add("### Buyer sources — what outreach can act on")
    add("")
    add("| community | why | evidence |")
    add("|---|---|---|")
    for c in comms:
        if c.get("serves") == "buyer":
            add(f"| `{c.get('name','')}` | {c.get('why','')} | {c.get('evidence','')} |")
    add("")
    add("### Content sources — content material, explicitly not leads")
    add("")
    add("| community | why | evidence |")
    add("|---|---|---|")
    for c in comms:
        if c.get("serves") == "practitioner":
            add(f"| `{c.get('name','')}` | {c.get('why','')} | {c.get('evidence','')} |")
    add("")
    add(f"**Buyer-source count: {len(r.communities_for('buyer'))}** (the task asks for 3-5).")
    add("")
    add("### Measured and NOT recommended")
    add("")
    add("Listed so the decision is auditable rather than a silent omission.")
    add("")
    add("| community | why not |")
    add("|---|---|")
    for c in r.excluded:
        keep = f" Kept for: {c['keep_for']}" if c.get("keep_for") else ""
        add(f"| `{c.get('name','')}` | {c.get('evidence','')}{keep} |")
    add("")
    add("## 2. Queries")
    add("")
    for q in r.queries:
        add(f"- `{q}`")
    add("")
    add("Queries are applied per community. A run records the query that matched each record, "
        "so a change of targeting is visible in the data rather than only in a config diff.")
    add("")
    add("## 3. Fields")
    add("")
    add(f"{len(FIELDS)} fields, one field map driving the SQLite table, the ClickHouse table "
        "and the CSV header.")
    add("")
    add(field_map_markdown())
    add("## 4. What is kept, and for how long")
    add("")
    add(f"- **Excerpt only**: {ret.get('excerpt_chars', EXCERPT_CHARS)} characters of permitted text per record. "
        "Full text is hashed for edit detection and not stored.")
    add(f"- **Identities**: {ret.get('identities', 'handles as published; never enriched, never contacted')}.")
    add(f"- **Removal**: {ret.get('removal', 'recorded, not deleted')}. "
        "Counts stay reconcilable; the archive's retention sweep prunes the text on its own schedule.")
    add("- **Never stored as fact**: company and buyer intent. Both columns exist and are fixed at "
        "`unknown`; no code path infers them. A post is a signal, never evidence of a budget.")
    add("")
    add("## 5. Classification")
    add("")
    fams = ", ".join(f"`{fam.name}`" for fam in r.families)
    add(f"Deterministic rules in {len(r.families)} families ({fams}). A family counts once however "
        "many of its phrases matched, so a long post cannot out-score a precise one.")
    add("")
    add("Every record is labeled for **who it is for**, because this task feeds two different "
        "people and they do not want the same posts:")
    add("")
    add("- **buyer** — a business voice *and* work that software could remove. "
        f"At or above **{r.buyer_at}**; within **{r.ambiguous_margin}** below it the record is kept "
        "and flagged ambiguous. A buyer signal requires ownership language: an employee says "
        "\"our company\" as readily as an owner does.")
    add(f"- **practitioner** — delivery pain from someone who builds. At or above "
        f"**{r.practitioner_at}**. Content material; **never** handed to outreach as a lead.")
    add("- **none** — collected and rejected, with the reason stored so the rejection can be "
        "audited against the post.")
    add("")
    add("No model sits in the acceptance path: the fixture gate is deterministic checks, and a gate "
        "that depends on a model's mood is not a gate.")
    add("")
    add("## 6. Access — the decision this document exists for")
    add("")
    add("**Required:** approved commercial Reddit API access, in writing, covering both data storage "
        "and the intended AI processing.")
    add("")
    add("**Why it is not free:** Reddit's free Data API tier is for non-commercial use. This work is "
        "commercial — the signals feed a buyer test and published content — so it needs a "
        "paid agreement. Confirm the current terms and pricing with Reddit when applying; they have "
        "changed more than once since 2023.")
    add("")
    cands = (r.scope or {}).get("candidates", [])
    if cands:
        # A shopping list for this decision, kept out of section 1 on purpose:
        # the task says the scope owner chooses 3-5 communities, and a
        # shortlist that grows itself is not a shortlist.
        add("**If access is bought, these are the rooms to point it at — and none "
            "of them has been checked by anyone.**")
        add("")
        add("Every room in section 1 is a problem-DISCUSSION room, and those "
            "yielded almost no buyers. That was read as \"Reddit does not carry "
            "buyers\", but it tested the wrong hypothesis. The same split is "
            "measured on Hacker News: discussion returns 0.4% buyers, hiring "
            "adverts return 6.0% — same classifier, same archive, an order of "
            "magnitude apart. Reddit's hiring rooms are the untested half.")
        add("")
        add("| room | why | status |")
        add("|---|---|---|")
        for c in cands:
            add(f"| `{c.get('name')}` | {c.get('why','')} | {c.get('evidence','')} |")
        add("")
        add("They are written from general knowledge, not measured and not even "
            "browsed: Reddit is closed to the collector and to this repo. **The "
            "benchmark they have to beat is already running and costs nothing** "
            "— `hn/hiring` at 6.0% on a keyless public API. If paid access "
            "cannot beat free, the answer is not to buy it.")
        add("")
    add("**Open decisions**, none of which this repository can make for you:")
    add("")
    add("- Choose the communities and queries (sections 1–2).")
    add("- Apply for commercial access and name the budget.")
    add("- Approve that access **or** name the replacement source.")
    add("- Provide a ClickHouse instance and credentials scoped to this collector.")
    add("- Book the data-design review.")
    add("")
    add("**Until access exists, there is no Reddit code path at all.** Reddit is not in the "
        "collector's source registry, so `jester signals run --source reddit` fails with "
        "\"no source named 'reddit'\" rather than quietly collecting something it should not. "
        "`jester signals sources` prints every collector that does exist and the authority each "
        "one runs on.")
    add("")
    add("Live collection against **approved** sources is already built and running, and every "
        "record carries a `mode` column saying `live` or `fixture` — so a fixture row can never "
        "be counted as a live one, whichever source it came from.")
    add("")
    add("## 7. If access is refused or late")
    add("")
    add("Everything above the fetch layer is source-agnostic: the field map, the classifier, the "
        "fixtures, the ClickHouse load, the digest and the handover do not care where a record came "
        "from. Only the collector changes. Pre-named alternatives, each with a working adapter in "
        "this repo:")
    add("")
    add("| source | access | why it substitutes |")
    add("|---|---|---|")
    for name, access, why in REPLACEMENT_SOURCES:
        add(f"| {name} | {access} | {why} |")
    add("")
    add("**Recommendation:** name the fallback *with* the access decision rather than after it. "
        "Then the switch is a confirmation, and every date downstream holds either way.")
    add("")
    add("## 8. Cost")
    add("")
    add("| line | estimate | note |")
    add("|---|---|---|")
    add("| Reddit commercial API | unknown — the scope owner to obtain | the single unpriced item; a quote is required before committing |")
    add("| ClickHouse | 0 | self-hosted in Docker beside the existing services unless an instance is provided |")
    add("| Build hours | the bulk of the estimate remains | the scaffold is already built |")
    add("| Operation after release | ≤ 4 h/week | the task's own cap; surplus hours are surfaced, not absorbed |")
    add("")
    return "\n".join(lines) + "\n"


def write_scope(out_dir: str | Path, rules: Rules | None = None, *, today: str = "") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "scope-and-access.md"
    path.write_text(scope_markdown(rules, today=today), encoding="utf-8")
    return path
