"""Hacker News, through the public Algolia search API.

**Why this source exists.** If commercial Reddit access is not approved,
the work has to move to a different approved source. Hacker News is the
obvious one: its search API is public, keyless and documented, so it can be
built and proven before that decision is taken rather than after.

**Why search rather than a feed.** W3 measured the alternative: walking a
community's `/new/` collects whatever was posted today, and what a community
posts today is mostly not a buyer — 123 posts and 1 113 comments produced a
0.65% buyer rate. Searching for the sentences a buyer actually writes is the
same number of requests at a far higher hit rate, and the phrases double as
Omar's opening lines. `search_by_date` is used rather than `search` because a
daily collector wants what appeared since the last run, not what Algolia
considers most relevant since 2007.

**Comments carry their story's title.** The densest evidence in the Reddit
archive sat in replies that are three words long, where the thread is what says
who is speaking. The classifier already knows how to inherit a voice from the
parent, so a comment is collected with its story title as context — and the
inherited verdict is flagged as inherited, because borrowed evidence is weaker
evidence.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterator, List

from .. import Signal
from . import SourceError, register

API = "https://hn.algolia.com/api/v1/search_by_date"

#: Algolia serves up to 1000 per page. 100 keeps a failed request cheap to
#: retry and keeps one query's whole result in memory without thought.
PAGE_SIZE = 100

#: Self-imposed pace between requests, in seconds. The API publishes no hard
#: limit; that is a reason to be careful with it, not a licence. "Do not bypass
#: limits is not something to do, and a limit nobody publishes is one you
#: set yourself.
RATE = 1.0

#: How long to wait, and how many times, when the service pushes back. A 429
#: or a 5xx is the service asking for room — retrying immediately is the
#: behaviour the card calls bypassing a limit.
RETRIES = 3
BACKOFF = 4.0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain(raw: str) -> str:
    """Algolia returns the author's HTML. Store the words, not the markup:
    `<p>` between paragraphs and `&#x2F;` inside a URL both defeat phrase
    matching, and the excerpt is meant to be read by a person."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _bucket(tags: List[str]) -> str:
    """Which part of the site a hit came from.

    Stored in `community` — the column means "the room it was posted in", and
    on Hacker News the rooms are Ask, Show, the front page and the comment
    threads. Keeping them apart is what makes the per-community query useful:
    Ask HN and Show HN are different populations, and lumping them together
    hides that.
    """
    tagset = set(tags or [])
    if "comment" in tagset:
        return "hn/comment"
    if "ask_hn" in tagset:
        return "hn/ask"
    if "show_hn" in tagset:
        return "hn/show"
    return "hn/story"


@register
class HackerNews:
    name = "hackernews"
    platform = "hackernews"
    access = ("public, keyless search API published by Algolia for Hacker News; "
              "no account, no commercial restriction stated")
    terms_url = "https://hn.algolia.com/api"
    rate = f"{RATE:.1f}s between requests, {RETRIES} backoff retries"

    def __init__(self, opener=None, sleep=time.sleep, timeout: int = 30,
                 user_agent: str = "jester-signals/1.0 (the collector problem-signal collector)"):
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._timeout = timeout
        self._user_agent = user_agent
        self._last_call = 0.0

    # -- plumbing ----------------------------------------------------------
    def _get(self, params: dict) -> dict:
        url = f"{API}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
        for attempt in range(RETRIES):
            # Pace every call, including retries: the gap is the point.
            gap = RATE - (time.monotonic() - self._last_call)
            if gap > 0:
                self._sleep(gap)
            self._last_call = time.monotonic()
            try:
                with self._opener(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                    self._sleep(BACKOFF * (attempt + 1))
                    continue
                raise SourceError(f"HTTP {exc.code} from Hacker News search",
                                  status=str(exc.code)) from exc
            except urllib.error.URLError as exc:
                if attempt < RETRIES - 1:
                    self._sleep(BACKOFF * (attempt + 1))
                    continue
                raise SourceError(f"cannot reach Hacker News search: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                # A body that is not JSON is usually an interstitial. Recorded
                # as a failure rather than read as "no results", which is how a
                # block becomes an empty day nobody investigates.
                raise SourceError(f"Hacker News search returned non-JSON: {exc}") from exc
        raise SourceError("Hacker News search exhausted its retries")

    # -- collection --------------------------------------------------------
    def search(self, query: str, *, since_utc: str = "", limit: int = 100) -> Iterator[Signal]:
        """Every hit for one phrase, newest first, as unclassified `Signal`s.

        The caller classifies and stores. This yields what the source said and
        nothing more — an adapter that also decided relevance would make the
        rule set impossible to change without touching every source.
        """
        params = {
            "query": query,
            "tags": "(story,comment)",
            "hitsPerPage": min(PAGE_SIZE, max(1, limit)),
            "page": 0,
        }
        if since_utc:
            params["numericFilters"] = f"created_at_i>{_epoch(since_utc)}"

        seen = 0
        while seen < limit:
            body = self._get(params)
            hits = body.get("hits") or []
            if not hits:
                return
            for hit in hits:
                signal = self._to_signal(hit, query)
                if signal is None:
                    continue
                yield signal
                seen += 1
                if seen >= limit:
                    return
            params["page"] += 1
            if params["page"] >= int(body.get("nbPages") or 0):
                return

    def _to_signal(self, hit: dict, query: str) -> Signal | None:
        source_id = str(hit.get("objectID") or "").strip()
        if not source_id:
            return None
        tags = hit.get("_tags") or []
        is_comment = "comment" in set(tags)
        text = _plain(hit.get("comment_text") or hit.get("story_text") or "")
        title = _plain(hit.get("title") or "")
        # A comment's own title is empty; its story's title is the thread
        # context the classifier needs to decide whose voice is speaking.
        context_title = _plain(hit.get("story_title") or "") if is_comment else ""
        if not text and not title:
            # Nothing permitted to store. A link-only submission is a URL and a
            # headline; with neither there is no text to classify, and an empty
            # record would inflate the collected count for nothing.
            return None
        return Signal(
            source_id=source_id,
            platform=self.platform,
            source_url=f"https://news.ycombinator.com/item?id={source_id}",
            community=_bucket(tags),
            kind="comment" if is_comment else "post",
            author=str(hit.get("author") or ""),
            title=title or context_title,
            text=text,
            created_utc=str(hit.get("created_at") or ""),
            query=query,
            mode="live",
        )


def _epoch(when: str) -> int:
    """`2026-09-22T00:00:00+00:00` or `7d` -> a unix timestamp.

    The relative form exists because that is how the run is actually invoked
    ("since yesterday"), and computing it in the caller would put the same
    three lines in the CLI, the scheduler and the recovery pass.
    """
    text = (when or "").strip()
    m = re.fullmatch(r"(\d+)([dh])", text, re.I)
    if m:
        amount = int(m.group(1))
        delta = timedelta(days=amount) if m.group(2).lower() == "d" else timedelta(hours=amount)
        return int((datetime.now(timezone.utc) - delta).timestamp())
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceError(f"cannot read a date from {when!r}; use 7d, 24h or an ISO date") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
