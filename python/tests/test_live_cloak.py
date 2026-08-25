"""Live CloakBrowser fetch (§37.19) — requires a running cloakserve container.

Manual-approval territory per the §37.17 decision: public comment text only,
single session, seconds-level pacing.
"""
import urllib.request

import pytest

from jester.fetchers.cloak import RedditCDPFetcher, ScraperConfig


def _cdp_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5)
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _cdp_up(), reason="cloakserve CDP endpoint not reachable"),
    pytest.mark.live_cloak,
]


def test_live_fetch_real_public_thread():
    cfg = ScraperConfig()  # defaults: localhost:9222, blocked_response_action=backoff
    fetcher = RedditCDPFetcher(
        cfg,
        source_url="https://www.reddit.com/r/selfhosted/comments/1vwtv9f/apple_music_alternative/",
        thread_id="1vwtv9f",
    )
    batches = fetcher.fetch()
    assert batches, "expected at least one batch from the live thread"
    (batch,) = batches
    assert batch["platform"] == "reddit"
    assert batch["thread_id"] == "1vwtv9f"
    assert batch["comments"], "live thread must yield comments via SSR DOM"
    for c in batch["comments"]:
        assert c["body"].strip()
        assert len(c["fingerprint"]) == 16
