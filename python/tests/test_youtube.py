"""§37.25 YouTube adapter tests — offline via FakeCDP fixtures."""
import hashlib
import json
from pathlib import Path

import pytest

from jester.fetchers import FixtureFetcher


class FakeResponse:
    def __init__(self, url, status=200, body=None):
        self.url = url
        self.status = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class FakeMouse:
    def __init__(self):
        self.wheels = []

    def wheel(self, dx, dy):
        self.wheels.append(dy)


class FakePage:
    def __init__(self, responses, eval_result=None):
        self.responses = responses
        self.handlers = {}
        self.gotos = []
        self.eval_result = eval_result or []
        self.mouse = FakeMouse()

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url):
        self.gotos.append(url)
        for r in self.responses:
            self.handlers.get("response", lambda r: None)(r)

    def evaluate(self, js):
        return self.eval_result

    def close(self):
        pass


class FakeCDP:
    def __init__(self, responses, eval_result=None):
        self.page = FakePage(responses, eval_result=eval_result)


def _fetcher(responses, eval_result=None, scrolls=3, action="backoff"):
    from jester.fetchers.youtube import YoutubeCDPFetcher, ScraperConfig

    cfg = ScraperConfig(cdp_url="http://localhost:9222", blocked_response_action=action)
    sleeps = []
    f = YoutubeCDPFetcher(
        cfg,
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        driver=lambda url: FakeCDP(responses, eval_result=eval_result).page,
        sleeper=lambda s: sleeps.append(s),
        delay_s=0.0,
        scrolls=scrolls,
    )
    f._sleep_calls = sleeps
    return f


XHR_FIXTURE = {
    "frameworkUpdates": {
        "entityBatchUpdate": {
            "mutations": [
                {"payload": {"other": True}},
                {"payload": {"commentEntityPayload": {
                    "key": "EgpxYQ",
                    "properties": {"content": {"content": "great talk, exactly my pain"}},
                    "author": {"displayName": "@selfhoster"},
                }}},
                {"payload": {"commentEntityPayload": {
                    "key": "",
                    "properties": {"content": {"content": "no id here"}},
                    "author": {"displayName": "@anon"},
                }}},
            ]
        }
    }
}


def _is_next_xhr(url):
    from jester.fetchers.youtube import _is_next_xhr as fn
    return fn(url)


def test_next_xhr_filter():
    assert _is_next_xhr("https://www.youtube.com/youtubei/v1/next?key=x")
    assert not _is_next_xhr("https://www.youtube.com/api/stats/ads")


def test_extract_comments_walks_mutations():
    from jester.fetchers.youtube import _extract_comments

    out = _extract_comments(XHR_FIXTURE)
    assert [c["body"] for c in out] == ["great talk, exactly my pain", "no id here"]
    assert out[0]["fingerprint"] == hashlib.sha1(b"EgpxYQ").hexdigest()[:16]
    # No id -> author+body is the stable fallback source.
    assert out[1]["fingerprint"] == hashlib.sha1(b"@anonno id here").hexdigest()[:16]
    assert all(c["upvotes"] == 0.0 for c in out)  # never fabricate counts


def test_happy_path_yields_contract_batch():
    f = _fetcher([FakeResponse("https://www.youtube.com/youtubei/v1/next", body=XHR_FIXTURE)])
    batches = f.fetch()
    contract_keys = set(FixtureFetcher().fetch()[0].keys())
    assert batches and set(batches[0].keys()) == contract_keys
    assert batches[0]["platform"] == "youtube"
    assert batches[0]["thread_id"] == "dQw4w9WgXcQ"


def test_scrolls_performed():
    f = _fetcher([FakeResponse("https://www.youtube.com/youtubei/v1/next", body=XHR_FIXTURE)], scrolls=4)
    f.fetch()
    page_wheels = f._page.mouse.wheels
    assert len(page_wheels) >= 4


def test_dom_fallback_when_no_xhr():
    rows = [
        {"author": "@alice", "body": "dom extracted comment"},
    ]
    f = _fetcher([], eval_result=rows)
    batches = f.fetch()
    assert batches and batches[0]["comments"][0]["body"] == "dom extracted comment"
    want = hashlib.sha1(b"@alicedom extracted comment").hexdigest()[:16]
    assert batches[0]["comments"][0]["fingerprint"] == want


def test_block_action_fail():
    from jester.fetchers.cloak import BlockedResponse

    f = _fetcher([FakeResponse("https://www.youtube.com/watch?v=x", status=403)], action="fail")
    with pytest.raises(BlockedResponse):
        f.fetch()


def _fetcher_kwargs_guard():
    # ScraperConfig import sanity for the config dataclass used above.
    from jester.fetchers.youtube import ScraperConfig

    assert ScraperConfig().cdp_url
