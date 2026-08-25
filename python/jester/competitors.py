"""M2.3 critic web step (D-1): verify competition instead of guessing at it.

Until now `competitor_notes` was hard-coded to None and `competition_checked`
rode on whether the LLM happened to emit a number — which is not a check, it is
the model's prior. This module does the actual lookup, under the two budgets
§37.15 reserved for it (`critic_web_rate_limit` calls/min, `critic_web_daily_budget`
calls/day) and the R53 recheck TTL.

R29 is the governing rule: a search that did not run, or ran and failed, yields
``competition=None`` / ``checked=False`` / ``notes=None`` so §7.2 imputes 5. A
search that ran and found nothing is a *result*, not a failure — it is checked,
with a low competition score and a factual note.
"""
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Protocol

from jester.quota import QuotaExceeded, spend
from jester.scoring import clip

# Ledger key for the critic's web budget. Distinct from the platform scrape
# budgets so a busy scrape night cannot starve the critic (or vice versa).
QUOTA_PLATFORM = "critic_web"

PROVIDERS = ("none", "fake", "duckduckgo")


@dataclass(frozen=True)
class CompetitorHit:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class CompetitorVerdict:
    """What the check concluded. `checked` False means §7.2 imputes 5 (R29)."""

    competition: Optional[float]
    notes: Optional[str]
    checked: bool
    reason: str  # why it came out this way; surfaced in run logs, never stored


class SearchProvider(Protocol):
    def search(self, query: str) -> List[CompetitorHit]: ...


class NullSearch:
    """The default. Never searches, so the critic keeps M2.2 behaviour."""

    name = "none"

    def search(self, query: str) -> List[CompetitorHit]:
        raise RuntimeError("competitor search is disabled (provider=none)")


class FakeSearch:
    """Deterministic offline provider: the same query always yields the same
    hits, so scoring tests are reproducible without a network."""

    name = "fake"

    def __init__(self, table: Optional[dict] = None, fail_on: str = ""):
        self._table = table or {}
        self._fail_on = fail_on

    def search(self, query: str) -> List[CompetitorHit]:
        if self._fail_on and self._fail_on.lower() in query.lower():
            raise RuntimeError("simulated search failure")
        for needle, hits in self._table.items():
            if needle.lower() in query.lower():
                return list(hits)
        # Deterministic default: derive a stable count from the query text so a
        # fixture corpus produces stable scores run to run.
        n = sum(ord(ch) for ch in query) % 4
        return [
            CompetitorHit(title=f"{query.split()[0]} tool {i + 1}",
                          url=f"https://example.test/{i + 1}",
                          snippet="fixture hit")
            for i in range(n)
        ]


_DDG_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_TAGS = re.compile(r"<[^>]+>")


class DuckDuckGoSearch:
    """Live provider over DDG's HTML endpoint — no API key, no new dependency.

    Kept deliberately dumb: one GET, a regex over anchors, no pagination. If
    the markup drifts the result is zero hits, which surfaces as an unchecked
    verdict rather than a wrong one.
    """

    name = "duckduckgo"
    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, opener: Optional[Callable[[str], str]] = None, timeout: float = 10.0):
        self._opener = opener or self._fetch
        self._timeout = timeout

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; jester/1.0; +local research tool)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return resp.read(1 << 20).decode("utf-8", "replace")

    def search(self, query: str) -> List[CompetitorHit]:
        html = self._opener(self.ENDPOINT + "?" + urllib.parse.urlencode({"q": query}))
        hits = []
        for href, title in _DDG_RESULT.findall(html):
            text = _TAGS.sub("", title).strip()
            if not text:
                continue
            hits.append(CompetitorHit(title=text[:120], url=href[:300]))
            if len(hits) >= 10:
                break
        return hits


def select_competitor_search(cfg) -> SearchProvider:
    provider = getattr(cfg, "competitor_search_provider", "none")
    if provider == "none":
        return NullSearch()
    if provider == "fake":
        return FakeSearch()
    if provider == "duckduckgo":
        return DuckDuckGoSearch()
    raise ValueError(
        f"unknown competitor_search_provider {provider!r}; valid: {', '.join(PROVIDERS)}"
    )


def build_query(idea) -> str:
    """A search a human would run to see whether this already exists."""
    title = (getattr(idea, "title", "") or "").strip()
    if not title:
        title = (getattr(idea, "problem_statement", "") or "").strip()[:80]
    return f"{title} alternative existing tool".strip()


def competition_from_hits(hits: List[CompetitorHit]) -> float:
    """Map verified competitor count onto the §7.2 1-10 competition axis.

    Monotonic and deliberately coarse — the count is evidence of crowding, not
    a measurement, and a finer curve would imply precision that is not there.
    """
    n = len(hits)
    if n == 0:
        return 2.0
    if n <= 2:
        return 4.0
    if n <= 5:
        return 6.0
    if n <= 9:
        return 8.0
    return 9.0


def render_notes(hits: List[CompetitorHit], query: str) -> str:
    if not hits:
        return f"no competitors found (searched: {query})"
    listed = "; ".join(f"{h.title} <{h.url}>" for h in hits[:5])
    more = f" (+{len(hits) - 5} more)" if len(hits) > 5 else ""
    return f"{len(hits)} possible competitor(s): {listed}{more}"


def needs_recheck(last_checked_at: Optional[str], ttl_days: int, now=None) -> bool:
    """R53: a competition score older than the TTL is stale, not settled."""
    if not last_checked_at:
        return True
    now = now or datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            when = datetime.strptime(str(last_checked_at)[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return now - when >= timedelta(days=ttl_days)
    return True  # unparseable stamp: recheck rather than trust it


class CompetitorChecker:
    """Runs the web step under its rate limit and daily budget."""

    def __init__(self, db, thresholds, search: Optional[SearchProvider] = None,
                 *, clock: Optional[Callable[[], float]] = None,
                 sleeper: Optional[Callable[[float], None]] = None):
        self.db = db
        self.cfg = thresholds
        self.search = search if search is not None else select_competitor_search(thresholds)
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._last_call = None

    @property
    def enabled(self) -> bool:
        return not isinstance(self.search, NullSearch)

    def _pace(self) -> None:
        """v3.11 #4: space calls out. rate is calls/min, so the floor between
        two calls is 60/rate seconds."""
        rate = max(1, int(getattr(self.cfg, "critic_web_rate_limit", 6)))
        min_gap = 60.0 / rate
        if self._last_call is not None:
            waited = self._clock() - self._last_call
            if waited < min_gap:
                self._sleep(min_gap - waited)
        self._last_call = self._clock()

    def check(self, idea) -> CompetitorVerdict:
        unchecked = lambda reason: CompetitorVerdict(None, None, False, reason)  # noqa: E731

        if not self.enabled:
            return unchecked("provider=none")

        budget = int(getattr(self.cfg, "critic_web_daily_budget", 0))
        try:
            spend(self.db, QUOTA_PLATFORM, 1, daily_budget=budget)
        except QuotaExceeded as exc:
            # Budget exhaustion is a normal end state, not an error: the run
            # keeps going with unchecked competition (R29) and says so.
            return unchecked(f"daily budget exhausted ({exc.remaining} left)")

        query = build_query(idea)
        self._pace()
        try:
            hits = self.search.search(query)
        except Exception as exc:  # noqa: BLE001 - any provider failure is "unchecked"
            return unchecked(f"search failed: {exc}")

        return CompetitorVerdict(
            competition=clip(competition_from_hits(hits)),
            notes=render_notes(hits, query),
            checked=True,
            reason=f"{len(hits)} hit(s)",
        )
