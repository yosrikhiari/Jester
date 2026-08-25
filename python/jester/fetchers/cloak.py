"""M1.1 Reddit adapter over the CloakBrowser (cloakserve) CDP endpoint.

§35/§36 contract: connect via Playwright ``connect_over_cdp``; capture Reddit
GraphQL JSON responses; pace navigation at seconds level (§36 #7); honor
D-19's ``blocked_response_action`` (pass_through | backoff | fail) when a
block/challenge shows up. The CDP driver and sleeper are injectable so every
policy is testable offline (M1.1 DoD: contract tests never touch a live scrape).
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import yaml

ALLOWED_ACTIONS = ("pass_through", "backoff", "fail")
_DEFAULTS = {
    "cdp_url": "http://localhost:9222",
    "license_key": "",
    "proxy": "",
    "geoip": "",
    "blocked_response_action": "backoff",
}


class BlockedResponse(RuntimeError):
    """D-19 signal: the platform served a block/challenge instead of content."""

    def __init__(self, url: str, action: str):
        super().__init__(f"blocked response at {url} (action={action})")
        self.url = url
        self.action = action


@dataclass(frozen=True)
class ScraperConfig:
    cdp_url: str = _DEFAULTS["cdp_url"]
    license_key: str = ""
    proxy: str = ""
    geoip: str = ""
    blocked_response_action: str = "backoff"


def load_scraper_config(path=None) -> ScraperConfig:
    if path is None:
        path = Path(__file__).resolve().parents[3] / "config" / "scraper.yaml"
    path = Path(path)
    if not path.exists():
        return ScraperConfig()
    raw = yaml.safe_load(os.path.expandvars(path.read_text(encoding="utf-8"))) or {}
    action = raw.get("blocked_response_action", _DEFAULTS["blocked_response_action"])
    if action not in ALLOWED_ACTIONS:
        raise ValueError(
            f"blocked_response_action={action!r} invalid; valid: {', '.join(ALLOWED_ACTIONS)}"
        )
    return ScraperConfig(
        cdp_url=raw.get("cdp_url", _DEFAULTS["cdp_url"]),
        license_key=str(raw.get("license_key", "")),
        proxy=str(raw.get("proxy", "")),
        geoip=str(raw.get("geoip", "")),
        blocked_response_action=action,
    )


_API_RE = re.compile(r"graphql\.reddit\.com|/svc/shreddit|reddit\.com/api", re.I)
_CHALLENGE_RE = re.compile(r"captcha|challenge|access denied|blocked|unusual traffic", re.I)


def _is_reddit_api_response(url: str) -> bool:
    return bool(url and _API_RE.search(url))


def _looks_blocked(status: int, text: str) -> bool:
    if status in (403, 429):
        return True
    return bool(text and _CHALLENGE_RE.search(text))


def _walk_comment_nodes(node, out: list) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_comment_nodes(item, out)
    elif isinstance(node, dict):
        body = node.get("body")
        if isinstance(body, str) and body.strip():
            nested = node.get("data") if isinstance(node.get("data"), dict) else {}
            cid = node.get("id") or nested.get("id")
            score = node.get("score", nested.get("score", 0))
            out.append((cid, body.strip(), score))
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk_comment_nodes(value, out)


def _extract_comments(payload) -> List[dict]:
    found: list = []
    _walk_comment_nodes(payload, found)
    comments = []
    for cid, body, score in found:
        source = str(cid) if cid else body
        try:
            upvotes = float(score or 0)
        except (TypeError, ValueError):
            upvotes = 0.0
        comments.append({
            "body": body,
            "fingerprint": hashlib.sha1(source.encode("utf-8")).hexdigest()[:16],
            "upvotes": upvotes,
        })
    return comments


_BACKOFF_S = 5.0  # seconds-level pause before trusting the next response

# shreddit is server-rendered: during SSR loads /svc/shreddit/graphql returns
# stubs while the comments are already in the HTML (calibrated live, §37.19).
_DOM_JS = """
() => [...document.querySelectorAll('shreddit-comment')].map(n => ({
    id: n.getAttribute('thingid') || '',
    body: ((n.querySelector('.md') || {}).innerText || '').trim(),
    score: n.getAttribute('score') || '0',
})).filter(c => c.body)
"""


def comments_from_dom(page) -> List[dict]:
    """Extract comments straight from the server-rendered shreddit DOM."""
    try:
        rows = page.evaluate(_DOM_JS) or []
    except Exception:
        return []
    out = []
    for row in rows:
        cid = (row.get("id") or "").strip()
        body = (row.get("body") or "").strip()
        if not body:
            continue
        source = cid if cid else body
        try:
            upvotes = float(row.get("score") or 0)
        except (TypeError, ValueError):
            upvotes = 0.0
        out.append({
            "body": body,
            "fingerprint": hashlib.sha1(source.encode("utf-8")).hexdigest()[:16],
            "upvotes": upvotes,
        })
    return out


class RedditCDPFetcher:
    """One Fetcher per source thread. Live use requires a running cloakserve
    endpoint (config/scraper.yaml ``cdp_url``) and stays behind manual --live."""

    def __init__(
        self,
        scraper: ScraperConfig,
        source_url: str,
        thread_id: str,
        *,
        driver: Optional[Callable[[str], object]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        delay_s: float = 2.0,
    ):
        self.cfg = scraper
        self.source_url = source_url
        self.thread_id = thread_id
        self._driver = driver or self._default_driver
        self._sleep = sleeper or time.sleep
        self.delay_s = delay_s
        self._pw = None

    def _default_driver(self, cdp_url: str):
        from playwright.sync_api import sync_playwright  # lazy; no local browsers needed

        self._pw = sync_playwright().start()
        browser = self._pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context.new_page()

    def fetch(self) -> List[dict]:
        page = self._driver(self.cfg.cdp_url)
        events: list = []
        comments: List[dict] = []
        consecutive_blocks = 0
        try:
            page.on("response", lambda r: events.append(r))
            self._sleep(self.delay_s)  # §36 #7: seconds-level pacing
            page.goto(self.source_url)

            for resp in events:
                url = getattr(resp, "url", "")
                if not _is_reddit_api_response(url):
                    continue
                status = getattr(resp, "status", 200)
                payload = None
                text = ""
                try:
                    payload = resp.json()
                except Exception:
                    text = getattr(resp, "text", lambda: "")() or ""
                probe = text if payload is None else json.dumps(payload)
                if _looks_blocked(status, probe):
                    consecutive_blocks += 1
                    if self.cfg.blocked_response_action == "fail":
                        raise BlockedResponse(url, "fail")
                    if self.cfg.blocked_response_action == "backoff":
                        self._sleep(_BACKOFF_S)
                        if consecutive_blocks >= 2:
                            raise BlockedResponse(url, "backoff")
                    # pass_through falls through: skip this response, keep going
                    continue
                consecutive_blocks = 0
                if payload is not None:
                    comments.extend(_extract_comments(payload))

            if not comments:
                # SSR path: shreddit ships comments in the HTML itself.
                comments.extend(comments_from_dom(page))
        finally:
            close = getattr(page, "close", None)
            if callable(close):
                close()

        if not comments:
            return []
        return [{
            "platform": "reddit",
            "source": self.source_url,
            "thread_id": self.thread_id,
            "comments": comments,
        }]
