"""Deterministic classification of a problem signal, and who it is for.

Twenty labeled cases have to pass, deterministically, so this is rules
over text: same input, same verdict, no network, no model, no
quota. A model may one day propose new phrases; it never decides a fixture's
verdict, because a gate that depends on a model's mood is not a gate.

**Why families rather than a flat phrase score.** Version 1 summed phrase
weights and scored 1 signal in 2,600 archived records — and that one was a
false positive. Measured on 184 real posts, "asking for help" language appears
in a third of them and career/personal/hobby noise in another third, so no flat
sum can separate a buyer from a hobbyist: both say "looking for a". What
separates them is a *combination* — a business voice, or hiring, or cost,
together with work that software could remove. Families encode that: each
family contributes its weight once, however many of its phrases matched, so a
long rambling post cannot out-score a precise one.

**Why two audiences.** The collector feeds a buyer test and a content
feed, and the archive shows those are different posts. A self-hoster migrating
42 workflows off Zapier is excellent content and a terrible lead — he is the
person who does the work himself. So each record is labeled `buyer`,
`practitioner` or `none`, and outreach only ever sees the first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "signal_rules.yaml"

#: Links are stripped before matching. A slug like `/manually-curated-list`
#: sits between two non-word characters, so a word-bounded phrase fires inside
#: it — and a Reddit post is mostly links. The words in a URL are not the
#: author's sentence, so they do not get a vote.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)

BUYER = "buyer"
PRACTITIONER = "practitioner"
NONE = "none"


@dataclass(frozen=True)
class Family:
    name: str
    weight: float
    role: str          # buyer_marker | pain_marker | negative
    phrases: tuple
    note: str = ""


@dataclass(frozen=True)
class Verdict:
    audience: str          # buyer | practitioner | none
    relevant: int          # 1 kept (buyer or practitioner), 0 rejected
    confidence: float      # 0–1
    reason: str            # human-checkable sentence
    ambiguous: bool        # kept, but just under the bar
    families: tuple        # families that fired, in weight order
    matched: tuple         # the phrases that fired them
    against: tuple         # negative families that fired
    #: True when the buyer voice came from the thread rather than the record
    #: itself. Stored so a digest can say so: an inherited voice is weaker
    #: evidence than a stated one, and pretending otherwise is how a survey
    #: reply becomes a "qualified lead".
    inherited: bool = False


@dataclass
class Rules:
    buyer_at: float = 0.60
    practitioner_at: float = 0.45
    ambiguous_margin: float = 0.15
    #: What a voice borrowed from the thread is worth. Deliberately below the
    #: owner_voice family's own weight: inherited evidence is weaker evidence.
    inherited_weight: float = 0.25
    #: Longest reply that may inherit a voice. Above it the reply is advice,
    #: not an answer - see the note in the rules file.
    inherited_max_chars: int = 320
    max_reasons: int = 3
    families: tuple = ()
    hard_reject: tuple = ()
    #: Second-person address used only for frame detection (see is_buyer_frame).
    frame_phrases: tuple = ()
    scope: dict = None
    config_version: int = 2

    def by_role(self, role: str) -> List[Family]:
        return [f for f in self.families if f.role == role]

    # -- scope helpers ------------------------------------------------------
    @property
    def communities(self) -> List[dict]:
        return list((self.scope or {}).get("communities", []))

    def communities_for(self, audience: str) -> List[str]:
        return [c["name"] for c in self.communities if c.get("serves") == audience]

    @property
    def excluded(self) -> List[dict]:
        return list((self.scope or {}).get("excluded", []))

    @property
    def queries(self) -> List[str]:
        return list((self.scope or {}).get("queries", []))

    @property
    def approved(self) -> bool:
        return (self.scope or {}).get("status") == "approved"


class RulesError(ValueError):
    """The rules file is unusable. Loud on purpose: a silently empty rule set
    classifies every post as irrelevant and the run still looks healthy."""


def load_rules(path: str | Path | None = None) -> Rules:
    p = Path(path or DEFAULT_RULES_PATH)
    if not p.exists():
        raise RulesError(f"no rules file at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    fams = []
    for name, spec in (raw.get("families") or {}).items():
        if not isinstance(spec, dict) or "phrases" not in spec:
            raise RulesError(f"family {name!r} needs `phrases`")
        role = spec.get("role", "pain_marker")
        if role not in ("buyer_required", "buyer_boost", "pain_marker", "negative"):
            raise RulesError(f"family {name!r}: unknown role {role!r}")
        weight = float(spec.get("weight", 0.0))
        if role == "negative" and weight >= 0:
            raise RulesError(f"family {name!r} is negative but its weight is {weight}")
        if role != "negative" and not 0 < weight <= 1:
            raise RulesError(f"family {name!r}: weight must be in (0, 1], got {weight}")
        phrases = tuple(str(x).lower() for x in spec["phrases"] if str(x).strip())
        if not phrases:
            raise RulesError(f"family {name!r} has no phrases")
        fams.append(Family(name, weight, role, phrases, str(spec.get("note", ""))))

    rules = Rules(
        buyer_at=float(raw.get("buyer_at", 0.60)),
        practitioner_at=float(raw.get("practitioner_at", 0.45)),
        ambiguous_margin=float(raw.get("ambiguous_margin", 0.15)),
        inherited_weight=float(raw.get("inherited_weight", 0.25)),
        inherited_max_chars=int(raw.get("inherited_max_chars", 320)),
        max_reasons=int(raw.get("max_reasons", 3)),
        families=tuple(fams),
        hard_reject=tuple(str(s).lower() for s in (raw.get("hard_reject") or [])),
        frame_phrases=tuple(str(s).lower() for s in (raw.get("frame_phrases") or [])),
        scope=raw.get("scope") or {},
        config_version=int(raw.get("config_version", 2)),
    )
    if not rules.by_role("buyer_required"):
        raise RulesError("no buyer_required family: nothing could ever be a buyer signal")
    if not rules.by_role("pain_marker"):
        raise RulesError("no pain_marker family: every post would be rejected and the run would look fine")
    if not 0 < rules.practitioner_at <= rules.buyer_at <= 1:
        raise RulesError(
            f"thresholds must satisfy 0 < practitioner_at ({rules.practitioner_at}) "
            f"<= buyer_at ({rules.buyer_at}) <= 1")
    return rules


def _fired(blob: str, fam: Family) -> List[str]:
    """Which of a family's phrases appear, matched on word boundaries."""
    out = []
    for phrase in fam.phrases:
        # Phrases ending in '.' or '/' (like 'v1.' or '/mo') have no trailing
        # word character, so only the leading boundary applies.
        lead = r"(?<!\w)" if phrase[:1].isalnum() else ""
        trail = r"(?!\w)" if phrase[-1:].isalnum() else ""
        if re.search(lead + re.escape(phrase) + trail, blob):
            out.append(phrase)
    return out


def is_buyer_frame(text: str, rules: Optional[Rules] = None, *, title: str = "") -> bool:
    """Does this post establish a buyer frame for everything under it?

    Measured need: the densest buyer evidence in the archive sits under posts
    like "What's one part of running your small business you wish you could
    automate?". Every reply is a business owner naming work they would pay to
    remove - and each reply, read alone, is two or three words with no
    ownership language at all. The thread is what says who is speaking.

    A frame needs both halves: the post speaks as/to a business (owner voice)
    AND it asks the room (asking). An owner merely telling a story frames
    nothing, and a general question to a general audience frames nothing.
    """
    rules = rules or load_rules()
    blob = _URL_RE.sub(" ", f"{title}\n{text}").lower().strip()
    if not blob:
        return False
    fired = {fam.name for fam in rules.families if _fired(blob, fam)}
    has_owner = any(f.name in fired for f in rules.by_role("buyer_required"))
    if not has_owner and rules.frame_phrases:
        # A frame may also be set by addressing owners in the second person,
        # which a record's own voice may not use (see frame_phrases).
        has_owner = any(
            re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", blob) for p in rules.frame_phrases)
    return has_owner and "asking" in fired


def classify(text: str, rules: Optional[Rules] = None, *, title: str = "",
             context: str = "", context_title: str = "") -> Verdict:
    """Score one record. Title and body are scored together: the problem is
    often only in the title and only the body explains it.

    `context` / `context_title` are the parent post of a comment. When that
    parent establishes a buyer frame (see `is_buyer_frame`), a reply inherits
    the ownership voice it never had to state - at a reduced weight, and
    flagged `inherited`, because a voice borrowed from the thread is weaker
    evidence than one the person used themselves.
    """
    rules = rules or load_rules()
    blob = _URL_RE.sub(" ", f"{title}\n{text}").lower().strip()
    if not blob:
        return Verdict(NONE, 0, 0.0, "no text to classify", False, (), (), ())

    for phrase in rules.hard_reject:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", blob):
            return Verdict(NONE, 0, 0.0, f"hard reject: contains {phrase!r}", False, (), (), (phrase,))

    hits: Dict[str, Tuple[Family, List[str]]] = {}
    for fam in rules.families:
        found = _fired(blob, fam)
        if found:
            hits[fam.name] = (fam, found)

    score = sum(f.weight for f, _p in hits.values())
    score = max(0.0, min(1.0, score))

    # A buyer signal REQUIRES ownership language. Hiring and cost only raise
    # confidence: "we should pay someone" from an employee is a wish, and an
    # engineer quoting a monthly bill is writing content, not buying.
    required = [n for n, (f, _p) in hits.items() if f.role == "buyer_required"]
    boosts = [n for n, (f, _p) in hits.items() if f.role == "buyer_boost"]

    # Inherited voice: only when the record states none of its own, and only
    # when the parent really is a buyer frame. Worth less than saying it.
    # Inheritance is for ANSWERS, not for advice. Two guards, both measured:
    # the reply must be short enough to be an answer, and it must not be
    # telling the asker what to do.
    inherited = False
    answerable = (len(blob) <= rules.inherited_max_chars
                  and "advice_giving" not in hits)
    if not required and answerable and (context or context_title)             and is_buyer_frame(context, rules, title=context_title):
        inherited = True
        required = ["thread frame"]
        score = min(1.0, score + rules.inherited_weight)
    buyer_markers = required + boosts
    pain_markers = [n for n, (f, _p) in hits.items() if f.role == "pain_marker"]
    negatives = tuple(n for n, (f, _p) in hits.items() if f.role == "negative")
    ordered = tuple(n for n, _ in sorted(hits.items(), key=lambda kv: -abs(kv[1][0].weight)))
    phrases = tuple(p for _n, (_f, ps) in hits.items() for p in ps)[: rules.max_reasons * 2]

    def named(names):
        return ", ".join(names[: rules.max_reasons])

    # A buyer needs BOTH halves: a business/hiring/cost voice AND work that
    # software could remove. Either alone is a hobbyist or a vent.
    if required and pain_markers:
        if score >= rules.buyer_at:
            return Verdict(BUYER, 1, round(score, 3),
                           "buyer signal"
                           + (" (voice inherited from the thread)" if inherited else "")
                           + f": {named(buyer_markers)} + {named(pain_markers)}"
                           + (f"; against it: {named(list(negatives))}" if negatives else ""),
                           False, ordered, phrases, negatives, inherited)
        if score >= rules.buyer_at - rules.ambiguous_margin:
            return Verdict(BUYER, 1, round(score, 3),
                           "ambiguous buyer signal"
                           + (" (voice inherited from the thread)" if inherited else "")
                           + f": {named(buyer_markers)} + {named(pain_markers)}, "
                           "but the post does not say what the work costs"
                           + (f"; against it: {named(list(negatives))}" if negatives else ""),
                           True, ordered, phrases, negatives, inherited)

    # Delivery pain without a business voice is content material, not a lead.
    if pain_markers and score >= rules.practitioner_at:
        return Verdict(PRACTITIONER, 1, round(score, 3),
                       f"practitioner pain (content, not a lead): {named(pain_markers)}"
                       + (f"; against it: {named(list(negatives))}" if negatives else ""),
                       False, ordered, phrases, negatives)
    if pain_markers and score >= rules.practitioner_at - rules.ambiguous_margin:
        return Verdict(PRACTITIONER, 1, round(score, 3),
                       f"ambiguous practitioner pain: {named(pain_markers)} but little detail"
                       + (f"; against it: {named(list(negatives))}" if negatives else ""),
                       True, ordered, phrases, negatives)

    # The rejection has to say which half was missing, or it reads as a
    # contradiction: "no business voice" on a post whose owner_voice fired.
    if negatives:
        reason = f"rejected: {named(list(negatives))} outweighs any problem language"
    elif required and not pain_markers:
        reason = (f"rejected: {named(required)} is a business voice, but the post never says "
                  "what work it needs done")
    elif pain_markers and not required:
        reason = (f"rejected: {named(pain_markers)} but no business voice — "
                  "a practitioner or a hobbyist, below the content bar")
    elif hits:
        reason = f"rejected: only {named(list(hits))}, not enough to place it"
    else:
        reason = "rejected: no problem stated"
    return Verdict(NONE, 0, round(score, 3), reason, False, ordered, phrases, negatives)


def apply_to(signal, rules: Optional[Rules] = None, *,
             context: str = "", context_title: str = ""):
    """Classify a Signal in place and hand it back, so a collector can pipe
    records straight into `upsert`.

    **A comment's stored `title` belongs to its thread, not to it.** A comment
    has no title of its own; what the collector put there is the parent post's,
    kept so the record reads as something when a person opens it. Scoring it as
    the record's own words would let a thread called "What would you automate
    in your business?" lend its ownership voice to every reply under it,
    including the advice and the jokes. So for a comment it is passed as
    context instead, where the inheritance rules can weigh it, cap it by
    length and flag the verdict `inherited`.
    """
    is_comment = getattr(signal, "kind", "post") == "comment"
    own_title = "" if is_comment else signal.title
    parent_title = context_title or (signal.title if is_comment else "")
    v = classify(signal.text or signal.excerpt, rules, title=own_title,
                 context=context, context_title=parent_title)
    signal.relevant = v.relevant
    signal.audience = v.audience
    signal.match_confidence = v.confidence
    # The stored reason carries the WORDS, not just the family names.
    #
    # Found the hard way: the verdict is computed from the whole post, and the
    # archive keeps a 600-character excerpt (retention §14). So a record can
    # say "buyer signal: owner_voice + work_pain" while the text beside it
    # contains neither phrase, because both were at character 900 — and a
    # reviewer checking the claim finds nothing and concludes the classifier
    # is wrong. Naming the phrases makes the verdict checkable against the
    # source link, which is the evidence that is actually wanted.
    signal.match_reason = (
        v.reason + (f" [matched: {', '.join(v.matched)}]" if v.matched else ""))
    return signal


# ---------------------------------------------------------------------------
# Cross-posts
# ---------------------------------------------------------------------------
def crossposts(db) -> List[dict]:
    """Records that share their text with another record.

    Reported, never merged. The same question asked in two communities is two
    real records with two real source links, and collapsing them would make the
    counts lie in the other direction — "1 signal" when two communities each
    raised it is the more useful fact, not the less.
    """
    from . import TABLE
    rows = db.execute(
        f"SELECT text_hash, COUNT(*) n, GROUP_CONCAT(record_id) ids, "
        f"       GROUP_CONCAT(DISTINCT community) communities "
        f"FROM {TABLE} WHERE text_hash != '' GROUP BY text_hash HAVING n > 1"
    ).fetchall()
    return [
        {
            "text_hash": r[0],
            "count": r[1],
            "record_ids": (r[2] or "").split(","),
            "communities": sorted(set((r[3] or "").split(","))),
        }
        for r in rows
    ]
