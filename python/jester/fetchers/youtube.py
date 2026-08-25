"""M3.1 YouTube adapter (§37.25): comments over a cloakserve CDP session.

Calibrated against live YouTube through cloakserve (§37.25 findings): no
consent wall on this fingerprint; comments lazy-load after scrolling via
`/youtubei/v1/next` XHRs whose `frameworkUpdates.entityBatchUpdate.mutations`
carry `commentEntityPayload` entries (key / properties.content.content /
author.displayName). DOM extraction is the fallback when XHRs are absent.
"""
import hashlib
import json
import re
import time
from typing import Callable, List, Optional

from jester.fetchers.cloak import BlockedResponse, ScraperConfig, _looks_blocked

NEXT_XHR_MARK = "/youtubei/v1/next"
_CHALLENGE_RE = re.compile(r"(?i)captcha|challenge|access denied|blocked|unusual traffic")

_DOM_JS = """
() => [...document.querySelectorAll('ytd-comment-thread-renderer')].map(n => ({
  author: ((n.querySelector('#author-text') || {}).innerText || '').trim(),
  body: ((n.querySelector('#content-text') || {}).innerText || '').trim(),
})).filter(c => c.body)
"""


def _is_next_xhr(url: str) -> bool:
    return bool(url and NEXT_XHR_MARK in url)


def _walk_comment_payloads(node, out: list) -> None:
    if isinstance(node, dict):
        payload = node.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("commentEntityPayload"), dict):
            out.append(payload["commentEntityPayload"])
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk_comment_payloads(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_comment_payloads(value, out)


def _extract_comments(payload) -> List[dict]:
    """commentEntityPayload walker. Fingerprint source: the stable entity key,
    falling back to author+body when the key is missing. Like counts are NOT
    fabricated (R29 spirit) — upvotes stay 0 until a reliable signal exists."""
    payloads: list = []
    _walk_comment_payloads(payload, payloads)
    comments = []
    for cep in payloads:
        key = str(cep.get("key") or "")
        props = cep.get("properties") or {}
        body = str(((props.get("content") or {}).get("content")) or "").strip()
        if not body:
            continue
        author = str((cep.get("author") or {}).get("displayName") or "")
        source = key if key else f"{author}{body}"
        comments.append({
            "body": body,
            "fingerprint": hashlib.sha1(source.encode("utf-8")).hexdigest()[:16],
            "upvotes": 0.0,
        })
    return comments


def comments_from_dom(page) -> List[dict]:
    """Fallback: server-rendered/hydrated comment threads straight from DOM."""
    try:
        rows = page.evaluate(_DOM_JS) or []
    except Exception:
        return []
    out = []
    for row in rows:
        author = str(row.get("author") or "")
        body = str(row.get("body") or "").strip()
        if not body:
            continue
        out.append({
            "body": body,
            "fingerprint": hashlib.sha1(f"{author}{body}".encode("utf-8")).hexdigest()[:16],
            "upvotes": 0.0,
        })
    return out


class YoutubeCDPFetcher:
    """One Fetcher per video. Live use requires cloakserve (scraper.yaml cdp_url)."""

    def __init__(
        self,
        scraper: ScraperConfig,
        *,
        video_url: str,
        video_id: str,
        driver: Optional[Callable[[str], object]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        delay_s: float = 2.0,
        scrolls: int = 5,
    ):
        self.cfg = scraper
        self.video_url = video_url
        self.video_id = video_id
        self._driver = driver or self._default_driver
        self._sleep = sleeper or time.sleep
        self.delay_s = delay_s
        self.scrolls = scrolls
        self._pw = None
        self._page = None

    def _default_driver(self, cdp_url: str):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        browser = self._pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context.new_page()

    def _scroll_page(self, page):
        for _ in range(self.scrolls):
            page.mouse.wheel(0, 1600)
            self._sleep(1.0)

    def fetch(self) -> List[dict]:
        page = self._driver(self.cfg.cdp_url)
        self._page = page
        events: list = []
        comments: List[dict] = []
        consecutive_blocks = 0
        try:
            page.on("response", lambda r: events.append(r))
            self._sleep(self.delay_s)
            page.goto(self.video_url)

            for resp in events:
                url = getattr(resp, "url", "")
                status = getattr(resp, "status", 200)
                if status in (403, 429):
                    consecutive_blocks += 1
                    if self.cfg.blocked_response_action == "fail":
                        raise BlockedResponse(url, "fail")
                    if self.cfg.blocked_response_action == "backoff":
                        self._sleep(5.0)
                        if consecutive_blocks >= 2:
                            raise BlockedResponse(url, "backoff")
                    continue
                consecutive_blocks = 0

            self._scroll_page(page)

            for resp in events:
                url = getattr(resp, "url", "")
                if not _is_next_xhr(url):
                    continue
                try:
                    payload = resp.json()
                except Exception:
                    continue
                comments.extend(_extract_comments(payload))

            if not comments:
                comments.extend(comments_from_dom(page))
        finally:
            close = getattr(page, "close", None)
            if callable(close):
                close()

        if not comments:
            return []
        return [{
            "platform": "youtube",
            "source": self.video_url,
            "thread_id": self.video_id,
            "comments": comments,
        }]
