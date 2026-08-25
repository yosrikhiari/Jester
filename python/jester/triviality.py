"""R37 rule-based triviality judge (§7.2 / jester-setup.md §37.5).

Deterministic proxies for the non-trivial bar: (a) specific task/situation,
(b) not a generic paraphrase, (c) not solved by a mainstream tool. Pure
functions, no I/O. Floor, not gate: callers must never filter on this —
it only tags nuggets and feeds run-summary observability (trivial_share,
ALL_TRIVIAL).
"""
import re
from dataclasses import dataclass

REASON_GENERIC = "generic_paraphrase"
REASON_TOOL_SOLVES = "mainstream_tool_solves"
REASON_VAGUE = "no_specific_context"


@dataclass(frozen=True)
class TrivialityVerdict:
    trivial: bool
    reasons: tuple = ()


# Proxy (b): generic-paraphrase constructions — no concrete situation named.
_GENERIC_RE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwish (there was|someone would)\b",
        r"\bsomeone should\b",
        r"\bwould be nice if\b",
        r"\bwants? better \w+\b",
        r"\bneeds? better \w+\b",
        r"\bbetter ux\b",
        r"\bimprove (the )?ux\b",
        r"\bwhy (isn'?t|is not) there\b",
    )
]

# Proxy (c): a mainstream tool framed as already solving it. Merely mentioning
# a tool while describing pain *inside* it must not fire (§7.2 Excel example).
_TOOL = r"(?:excel|sheets?|notion|jira|slack|obsidian|vscode|airtable|zapier|photoshop|chrome)"
_TOOL_SOLVES_RE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b{_TOOL}\b[^.!?]{{0,40}}\b(?:already|does|can do|handles|supports)\b",
        r"\b(?:just|simply) use\b",
        r"\bhave you tried\b",
        r"\balready exists\b",
    )
]

# Proxy (a): specificity anchors — digits, units, file types, tech nouns.
_SPECIFIC_RE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\d",
        r"\b(?:gb|mb|kb|ms|sec|secs|min|mins|hour|hours|day|days|week|weeks)s?\b",
        r"\.\w{2,5}\b",
        r"\b(?:csv|yaml|json|xml|pdf|sql)\b",
    )
]
_TECH_NOUNS = frozenset(
    "config backup api docker database server sync crash freeze export import "
    "timeout deploy schema queue memory disk latency token cluster migration "
    "update install upgrade restore reboot".split()
)
_TECH_NOUNS_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_TECH_NOUNS)) + r")s?\b", re.IGNORECASE
)


def _has_specific_anchor(text: str) -> bool:
    if any(p.search(text) for p in _SPECIFIC_RE):
        return True
    return _TECH_NOUNS_RE.search(text) is not None


def judge_trivial(text: str) -> TrivialityVerdict:
    """Classify one comment body. VAGUE is reported alone: it is the absence
    of signal, while the other two are positive noise signals."""
    t = text or ""
    reasons = []
    if any(p.search(t) for p in _GENERIC_RE):
        reasons.append(REASON_GENERIC)
    if any(p.search(t) for p in _TOOL_SOLVES_RE):
        reasons.append(REASON_TOOL_SOLVES)
    if not reasons and not _has_specific_anchor(t):
        reasons.append(REASON_VAGUE)
    return TrivialityVerdict(trivial=bool(reasons), reasons=tuple(reasons))
