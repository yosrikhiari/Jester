"""Ingestion boundary (§37.14 / M1.1 mock form): Fetcher protocol + fixture mock.

Live adapters (CloakBrowser scrape sessions) implement the same Fetcher
protocol; the pipeline below it never changes.
"""
from typing import List, Protocol

MOCK_SAMPLE = [
    {
        "platform": "reddit",
        "source": "https://reddit.com/r/selfhosted/comments/abc",
        "thread_id": "abc",
        "comments": [
            {"body": "I hate that this drops every weekend and I lose my config.", "fingerprint": "c1", "upvotes": 12},
            {"body": "Someone should please build a tool that auto-backs-up before updates.", "fingerprint": "c2", "upvotes": 30},
            {"body": "This is fine, no complaints here.", "fingerprint": "c3", "upvotes": 2},
        ],
    }
]


class Fetcher(Protocol):
    """One method, one batch shape: {platform, source, thread_id, comments}."""

    def fetch(self) -> List[dict]: ...


class FixtureFetcher:
    """M1.1 mock-mode first pass: deterministic batches from a canned fixture."""

    def __init__(self, sample=None):
        self._sample = MOCK_SAMPLE if sample is None else sample

    def fetch(self) -> List[dict]:
        return [dict(batch) for batch in self._sample]
