"""Live YouTube fetch (§37.25) — requires the cloakserve container running.

Public video comments only, per the §37.17 decision.
"""
import urllib.request

import pytest

from jester.fetchers.cloak import ScraperConfig
from jester.fetchers.youtube import YoutubeCDPFetcher


def _cdp_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5)
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _cdp_up(), reason="cloakserve CDP endpoint not reachable"),
    pytest.mark.live_youtube,
]


def test_live_fetch_real_video_comments():
    cfg = ScraperConfig()
    fetcher = YoutubeCDPFetcher(
        cfg,
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
    )
    batches = fetcher.fetch()
    assert batches, "expected at least one batch from the live video"
    (batch,) = batches
    assert batch["platform"] == "youtube"
    assert batch["thread_id"] == "dQw4w9WgXcQ"
    assert batch["comments"], "live video must yield comments"
    for c in batch["comments"]:
        assert c["body"].strip()
        assert len(c["fingerprint"]) == 16
