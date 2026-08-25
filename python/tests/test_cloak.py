"""M1.1 CloakBrowser adapter tests (§37.18) — fully offline via FakeCDP.

Contract tests never need a live scrape or a live CloakBrowser container
(M1.1 DoD). The CDP seam is injectable; block policies use the injected
fake-block fixture the DoD prescribes.
"""
import hashlib
import json
from pathlib import Path

import pytest

from jester.fetchers import FixtureFetcher


# --- Task 1: scraper config ----------------------------------------------------

def test_load_scraper_config_expands_env(tmp_path, monkeypatch):
    from jester.fetchers.cloak import load_scraper_config

    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", "lic-123")
    p = tmp_path / "scraper.yaml"
    p.write_text(
        "cdp_url: http://localhost:9222\n"
        "license_key: ${CLOAKBROWSER_LICENSE_KEY}\n"
        "proxy: \"\"\ngeoip: us\nblocked_response_action: backoff\n",
        encoding="utf-8",
    )
    cfg = load_scraper_config(p)
    assert cfg.cdp_url == "http://localhost:9222"
    assert cfg.license_key == "lic-123"
    assert cfg.blocked_response_action == "backoff"


def test_load_scraper_config_defaults_when_file_missing(tmp_path):
    from jester.fetchers.cloak import load_scraper_config

    cfg = load_scraper_config(tmp_path / "nope.yaml")
    assert cfg.cdp_url == "http://localhost:9222"
    assert cfg.blocked_response_action == "backoff"


def test_load_scraper_config_rejects_bad_action(tmp_path):
    from jester.fetchers.cloak import load_scraper_config

    p = tmp_path / "scraper.yaml"
    p.write_text("cdp_url: http://x\nblocked_response_action: nuke\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scraper_config(p)


# --- Task 2: parsing + block detection -------------------------------------------

def test_extract_comments_walks_nested_payload():
    from jester.fetchers.cloak import _extract_comments

    payload = {
        "data": {
            "post": {
                "comments": [
                    {"id": "t1_a1", "body": "first pain", "score": 12},
                    {"kind": "t1", "data": {"id": "t1_a2", "body": "second", "score": 3}},
                    {"nope": True},
                ]
            }
        }
    }
    comments = _extract_comments(payload)
    bodies = [c["body"] for c in comments]
    assert bodies == ["first pain", "second"]
    scores = [c["upvotes"] for c in comments]
    assert scores == [12, 3]


def test_fingerprints_stable_and_id_preferred():
    from jester.fetchers.cloak import _extract_comments

    payload = {"comments": [{"id": "t1_zz", "body": "same text"}]}
    again = {"comments": [{"id": "t1_zz", "body": "different text, same id"}]}
    c1 = _extract_comments(payload)[0]
    c2 = _extract_comments(again)[0]
    assert c1["fingerprint"] == c2["fingerprint"]
    expected = hashlib.sha1("t1_zz".encode()).hexdigest()[:16]
    assert c1["fingerprint"] == expected


def test_looks_blocked_matrix():
    from jester.fetchers.cloak import _looks_blocked

    assert _looks_blocked(403, "")
    assert _looks_blocked(429, "")
    assert not _looks_blocked(200, "")
    assert _looks_blocked(200, "solve this captcha to continue")
    assert _looks_blocked(200, "access blocked by network security")
    assert not _looks_blocked(200, "normal json payload")


# --- FakeCDP driver ---------------------------------------------------------------

class FakeResponse:
    def __init__(self, url, status=200, body=None):
        self.url = url
        self.status = status
        self._body = body if body is not None else {}

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class FakePage:
    def __init__(self, responses, eval_result=None):
        self.responses = responses          # fired on goto
        self.handlers = {}
        self.gotos = []
        self.eval_result = eval_result or []

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
    """Playwright-shaped driver stub: connect_over_cdp -> browser -> page."""

    def __init__(self, responses, eval_result=None):
        self.page = FakePage(responses, eval_result=eval_result)

    def connect_over_cdp(self, url):
        class Browser:
            def new_page(self, inner_page=None):
                return None

        b = type("B", (), {})
        ctx = type("Ctx", (), {})
        # sync_api pattern: browser.contexts[0].new_page()
        ctx.new_page = lambda: self.page
        b.contexts = [ctx]
        return b


def _fetcher(responses, action="backoff", delay_s=0.0, eval_result=None):
    from jester.fetchers.cloak import RedditCDPFetcher, ScraperConfig

    cfg = ScraperConfig(cdp_url="http://localhost:9222", license_key="",
                        proxy="", geoip="", blocked_response_action=action)
    calls = []
    f = RedditCDPFetcher(
        cfg,
        source_url="https://www.reddit.com/r/selfhosted/comments/abc/",
        thread_id="abc",
        driver=lambda url: FakeCDP(responses, eval_result=eval_result).page,
        sleeper=lambda s: calls.append(s),
        delay_s=delay_s,
    )
    f._sleep_calls = calls
    return f


DOM_ROWS = [
    {"id": "t1_p5jpjst", "body": "You could use yubal to download from youtube",
     "score": "1"},
    {"id": "t1_p5jq0j4", "body": "Check droppedneedle.", "score": "1"},
]


GRAPHQL = {
    "data": {
        "post": {
            "comments": [
                {"id": "t1_1", "body": "I lose my config every weekend", "score": 30},
            ]
        }
    }
}


def test_url_of_graphql_response_is_captured():
    """Sanity on the capture filter: only reddit graphql-ish URLs feed parsing."""
    from jester.fetchers.cloak import _is_reddit_api_response

    assert _is_reddit_api_response("https://graphql.reddit.com/api/PersistedQuery")
    assert _is_reddit_api_response("https://www.reddit.com/svc/shreddit/comments/abc")
    assert not _is_reddit_api_response("https://fonts.redditstatic.com/x.woff2")


# --- Task 3: block policy matrix -----------------------------------------------------

def test_block_action_fail_raises_immediately():
    from jester.fetchers.cloak import BlockedResponse

    f = _fetcher([FakeResponse("https://graphql.reddit.com/x", status=403)], action="fail")
    with pytest.raises(BlockedResponse):
        f.fetch()


def test_block_action_backoff_retries_then_raises():
    from jester.fetchers.cloak import BlockedResponse

    f = _fetcher(
        [FakeResponse("https://graphql.reddit.com/x", body="captcha challenge page")] * 2,
        action="backoff",
    )
    with pytest.raises(BlockedResponse):
        f.fetch()
    assert f._sleep_calls  # backed off before retrying


def test_block_action_pass_through_skips_source():
    f = _fetcher(
        [FakeResponse("https://graphql.reddit.com/x", status=429)],
        action="pass_through",
    )
    batches = f.fetch()
    assert batches == []


# --- Task 4: happy path + pacing -------------------------------------------------------

def test_happy_path_yields_contract_batch():
    f = _fetcher([FakeResponse("https://graphql.reddit.com/api/PersistedQuery", body=GRAPHQL)])
    batches = f.fetch()
    contract_keys = set(FixtureFetcher().fetch()[0].keys())
    assert batches, "expected one batch"
    assert set(batches[0].keys()) == contract_keys
    assert batches[0]["platform"] == "reddit"
    assert batches[0]["thread_id"] == "abc"
    (c,) = batches[0]["comments"]
    assert c["body"] == "I lose my config every weekend"
    assert c["fingerprint"] == hashlib.sha1(b"t1_1").hexdigest()[:16]
    assert c["upvotes"] == 30


def test_pacing_sleeper_honored_between_navigations():
    f = _fetcher([FakeResponse("https://graphql.reddit.com/a", body=GRAPHQL)], delay_s=2.5)
    f.fetch()
    assert 2.5 in f._sleep_calls


# --- §37.19: DOM extraction (shreddit is server-rendered; GraphQL gives stubs) ----

def test_dom_extraction_used_when_graphql_yields_stubs():
    f = _fetcher(
        [FakeResponse("https://www.reddit.com/svc/shreddit/graphql", body={"data": {}})],
        eval_result=DOM_ROWS,
    )
    batches = f.fetch()
    assert len(batches) == 1
    assert batches[0]["comments"][0]["body"].startswith("You could use yubal")
    c = batches[0]["comments"][0]
    assert c["fingerprint"] == hashlib.sha1(b"t1_p5jpjst").hexdigest()[:16]
    assert c["upvotes"] == 1.0


def test_dom_fingerprints_stable_across_fetches():
    a = _fetcher([], eval_result=DOM_ROWS).fetch()
    b = _fetcher([], eval_result=DOM_ROWS).fetch()
    fa = sorted(c["fingerprint"] for c in a[0]["comments"])
    fb = sorted(c["fingerprint"] for c in b[0]["comments"])
    assert fa == fb and len(fa) == 2


def test_empty_dom_and_no_payloads_yields_no_batch():
    f = _fetcher([], eval_result=[])
    assert f.fetch() == []
