"""M1.3 pre-filter heuristics (§5 / §37.12): pure functions on one comment.

Design rule (jester-setup.md §5): every heuristic is a pure function of a
single comment + thresholds — trivially unit-testable, config-tunable. R35:
the pipeline records per-heuristic funnel counts so drops are never silent.
"""
import re
from dataclasses import dataclass

from jester.config import Thresholds

REACTION_ONLY = {
    "+1", "this", "same", "lol", "lmao", "haha", "true", "facts", "agreed", "^",
}

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # pictographs, emoticons, supplemental symbols
    (0x2600, 0x27BF),    # misc symbols + dingbats
    (0xFE0F, 0xFE0F),    # variation selector-16
    (0x2B50, 0x2B50),    # star
    (0x2764, 0x2764),    # heart
)
_MENTION_RE = re.compile(r"@\w+")


@dataclass(frozen=True)
class PrefilterVerdict:
    keep: bool
    reason: str  # "" when kept


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def _count_emoji(text: str) -> int:
    return sum(1 for ch in text if _is_emoji(ch))


def _normalized(text: str) -> str:
    return text.strip().lower().strip("!.?…")


def prefilter_comment(
    comment: dict, cfg: Thresholds, *, already_used: int = 0
) -> PrefilterVerdict:
    """Apply the §5 heuristics in fixed order; first hit drops the comment."""
    body = (comment.get("body") or comment.get("text") or "").strip()
    if not body:
        return PrefilterVerdict(False, "too_short")
    low = _normalized(body)
    if low in REACTION_ONLY:
        return PrefilterVerdict(False, "reaction_only")
    if len(body) < cfg.prefilter_min_chars:
        return PrefilterVerdict(False, "too_short")
    if len(body) > cfg.prefilter_max_chars:
        return PrefilterVerdict(False, "too_long")
    if _count_emoji(body) > cfg.prefilter_max_emoji:
        return PrefilterVerdict(False, "emoji_spam")
    if len(_MENTION_RE.findall(body)) > cfg.prefilter_max_mentions:
        return PrefilterVerdict(False, "mention_spam")
    if len(low.split()) < cfg.prefilter_min_words:
        return PrefilterVerdict(False, "too_few_words")
    if already_used >= cfg.max_comments_per_thread:
        return PrefilterVerdict(False, "over_budget")
    return PrefilterVerdict(True, "")


def run_prefilter(comments: list, cfg: Thresholds):
    """Filter a flattened comment list; returns (kept, funnel) where funnel is
    a zero-suppressed per-heuristic drop counter plus a 'kept' tally."""
    kept, funnel = [], {}
    used = 0
    for c in comments:
        v = prefilter_comment(c, cfg, already_used=used)
        if v.keep:
            kept.append(c)
            used += 1
            funnel["kept"] = funnel.get("kept", 0) + 1
        else:
            funnel[v.reason] = funnel.get(v.reason, 0) + 1
    return kept, funnel
