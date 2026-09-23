"""Hacker News' monthly "Who is hiring?" threads — companies that have said
they will pay.

**Why this source exists, when four others did not work.** Twenty-one Reddit
communities and two collection modes produced no stable buyer signal in 2,257
records. The premise underneath all of them was that people who will pay for
engineering work describe their problem on a public forum. They mostly do not:
someone with a budget calls an agency or hires, and writing it up for strangers
is what you do when you cannot pay to make it go away.

So this looks where spending money is the *point* of the post. One thread a
month, each carrying 400–600 company adverts, every one of them a named
company with a stated need and usually a stated rate. Measured on the
September 2026 thread: 258 posts, 24 wanting contract or part-time work, 19 of
those also naming AI, automation, integration or internal tooling. Against
~0% everywhere else.

**A record here is a different object**, and two field rules change with it:

* `company` stops being `unknown` — because the advert names the company
  itself, in its own words. That is exactly the "unless a permitted source
  states it" case the field map already allows. It is read, never inferred.
* `buyer_intent` becomes a quote rather than a guess: the engagement line the
  company wrote. Still never a judgement about whether they will buy from us.

**What does not change.** Nothing is enriched, nobody is messaged. Collecting
a public advert is not resolving an identity; contacting anyone is outreach,
which is not this collector's job and does not become it here.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, List

from .. import Signal
from . import SourceError, register
from .hackernews import BACKOFF, RATE, RETRIES, _plain

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hn.algolia.com/api/v1/items/"

#: Only the title is searched. Without this the query matches any story that
#: merely mentions hiring, and the thread index fills with commentary about
#: the threads instead of the threads.
TITLE_ONLY = "title"

#: A month's thread carries hundreds of replies; anything smaller with this
#: title is someone discussing the thread, not the thread itself.
MIN_COMMENTS = 50

_TITLE_RE = re.compile(r"who\s+is\s+hiring", re.I)
#: The company's own first line, which is where it puts its name. Splitting on
#: the pipe is the convention the thread has used for a decade: NAME | ROLE |
#: LOCATION | ENGAGEMENT | RATE.
_NAME_RE = re.compile(r"^\s*([^|\n]{2,60}?)\s*(?:\||$)")
#: The engagement words, quoted back rather than interpreted.
_ENGAGEMENT_RE = re.compile(
    r"\b(contract(?:\s+to\s+permanent)?|freelance|part[- ]time|full[- ]time"
    r"|fractional|consult(?:ing|ant)?|intern(?:ship)?)\b", re.I)


def _headline(text: str, limit: int = 150) -> str:
    """The advert's first line - by convention NAME | ROLE | LOCATION |
    ENGAGEMENT | RATE, which is the most informative 150 characters in it."""
    head = (text or "").splitlines()[0].strip() if (text or "").strip() else ""
    return head[:limit]


@register
class HackerNewsHiring:
    name = "hackernews-hiring"
    platform = "hackernews"
    access = ("public, keyless search API published by Algolia for Hacker News; "
              "the adverts are posted publicly by the companies themselves")
    terms_url = "https://hn.algolia.com/api"
    rate = f"{RATE:.1f}s between requests, {RETRIES} backoff retries"

    def __init__(self, opener=None, sleep=time.sleep, timeout: int = 30,
                 user_agent: str = "jester-signals/1.0 (problem-signal collector)"):
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._timeout = timeout
        self._user_agent = user_agent
        self._last_call = 0.0

    # -- plumbing ----------------------------------------------------------
    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
        for attempt in range(RETRIES):
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
                raise SourceError(f"HTTP {exc.code} from Hacker News",
                                  status=str(exc.code)) from exc
            except urllib.error.URLError as exc:
                if attempt < RETRIES - 1:
                    self._sleep(BACKOFF * (attempt + 1))
                    continue
                raise SourceError(f"cannot reach Hacker News: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise SourceError(f"Hacker News returned non-JSON: {exc}") from exc
        raise SourceError("Hacker News exhausted its retries")

    # -- the monthly threads ------------------------------------------------
    def threads(self, limit: int = 14) -> List[dict]:
        """The monthly threads, newest first.

        Filtered twice on purpose: the title has to actually say "who is
        hiring", and the thread has to be big enough to be the real one. A
        story called "Analysis of 88,975 HN Who Is Hiring Job Posts" matches
        the query and is not a hiring thread.
        """
        q = urllib.parse.urlencode({
            "query": "Ask HN: Who is hiring?",
            "tags": "story",
            "restrictSearchableAttributes": TITLE_ONLY,
            "hitsPerPage": max(limit * 3, 30),
        })
        body = self._get(f"{SEARCH}?{q}")
        out = []
        for hit in body.get("hits") or []:
            title = hit.get("title") or ""
            if not _TITLE_RE.search(title):
                continue
            if (hit.get("num_comments") or 0) < MIN_COMMENTS:
                continue
            out.append({"id": str(hit.get("objectID")), "title": title.strip(),
                        "created_at": hit.get("created_at") or "",
                        "num_comments": hit.get("num_comments") or 0})
            if len(out) >= limit:
                break
        return out

    def search(self, query: str = "", *, since_utc: str = "",
               limit: int = 2000) -> Iterator[Signal]:
        """Every company advert from the recent monthly threads.

        `query` is how many months to read, so the run ledger records the
        depth alongside the counts — "12" reads a year. The signature matches
        the other sources so the run loop does not need to know the
        difference.
        """
        try:
            months = int(query) if str(query).strip().isdigit() else 12
        except (TypeError, ValueError):
            months = 12

        seen = 0
        for thread in self.threads(limit=months):
            body = self._get(f"{ITEM}{thread['id']}")
            for child in body.get("children") or []:
                signal = self._to_signal(child, thread)
                if signal is None:
                    continue
                yield signal
                seen += 1
                if seen >= limit:
                    return

    def _to_signal(self, child: dict, thread: dict) -> Signal | None:
        source_id = str(child.get("id") or "").strip()
        text = _plain(child.get("text") or "")
        # A deleted advert leaves an empty node. Nothing to classify and
        # nothing to show a reader, so it is not a record.
        if not source_id or not text:
            return None

        name = ""
        m = _NAME_RE.match(text)
        if m:
            candidate = m.group(1).strip()
            # A pipe-delimited first field is the company name by convention.
            # A long sentence is somebody replying to the thread, not
            # advertising in it, and guessing a name out of prose is exactly
            # the inference this collector does not do.
            if "|" in text[:200] and len(candidate) <= 60:
                name = candidate

        engagement = ", ".join(sorted({
            g.lower() for g in _ENGAGEMENT_RE.findall(text)})) or "unknown"

        return Signal(
            source_id=source_id,
            platform="hackernews",
            source_url=f"https://news.ycombinator.com/item?id={source_id}",
            community="hn/hiring",
            kind="post",
            author=str(child.get("author") or ""),
            # The advert's own headline line, not the company name: `company`
            # already carries that, and putting it in both made the console
            # print it twice, one above the other. The headline is where the
            # role, location and engagement live, which is what a reader
            # actually needs under the name.
            title=_headline(text) or thread["title"],
            text=text,
            created_utc=str(child.get("created_at") or ""),
            query=f"who-is-hiring {thread['created_at'][:7]}",
            mode="live",
            # Read off the advert, never inferred. This is the "unless a
            # permitted source states it" case the field map allows for.
            company=name or "unknown",
            buyer_intent=engagement,
        )
