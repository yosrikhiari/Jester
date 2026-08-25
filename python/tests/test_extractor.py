"""TDD tests for the M1.4 extractor agent (fake LLM)."""
from jester.agents.extractor import extract
from jester.llm import FakeLLM, ALLOWED_CATEGORIES


def _comments():
    return [
        {"id": "c1", "body": "I really wish there was a simpler way to self host my email", "score": 42},
        {"id": "c2", "body": "Managing 14 docker containers just to read my mail is insane", "score": 88},
        {"id": "c3", "body": "👍👍👍", "score": 3},
    ]


def test_extract_produces_valid_nuggets():
    nuggets = extract(
        _comments(),
        FakeLLM(),
        platform="reddit",
        thread_id="reddit_thread_1.json",
        source_url="https://reddit.com/r/selfhosted/",
        run_id="mock-run",
    )
    assert len(nuggets) == 3
    for n in nuggets:
        assert n.category in ALLOWED_CATEGORIES
        assert n.unique_key.startswith("reddit:reddit_thread_1.json:")
        assert n.raw_text
        assert n.extracted_insight


def test_unique_key_format_is_platform_thread_comment():
    nuggets = extract(
        [{"id": "c9", "body": "DNS records take a weekend", "score": 1}],
        FakeLLM(),
        platform="reddit",
        thread_id="t1",
        source_url="x",
        run_id="r",
    )
    assert nuggets[0].unique_key == "reddit:t1:c9"


def test_empty_body_skipped():
    nuggets = extract(
        [{"id": "c0", "body": "   ", "score": 0}],
        FakeLLM(),
        platform="reddit",
        thread_id="t1",
        source_url="x",
        run_id="r",
    )
    assert nuggets == []
