"""F-05: batched extraction — same rows, fewer calls, safe fallbacks."""
import json

from jester.agents.extractor import extract, extract_batch
from jester.llm import FakeLLM, NuggetDraft, OllamaLLM


def _comments(n):
    return [{"id": f"c{i}", "body": f"deploy pain number {i} breaks prod",
             "score": i} for i in range(n)]


def _keys(nuggets):
    return [(n.unique_key, n.extracted_insight, n.category, n.extractor_model)
            for n in nuggets]


def test_batch_matches_single_call_rows():
    """The batch path must yield the same rows as the legacy path — the saving
    is in requests spent, never in rows changed."""
    assert _keys(extract_batch(_comments(7), FakeLLM(), platform="hn",
                               thread_id="t", source_url="u", run_id="r",
                               batch_size=3)) == \
           _keys(extract(_comments(7), FakeLLM(), platform="hn",
                         thread_id="t", source_url="u", run_id="r"))


def test_batch_size_one_is_legacy():
    assert _keys(extract_batch(_comments(4), FakeLLM(), platform="hn",
                               thread_id="t", source_url="u", run_id="r",
                               batch_size=1)) == \
           _keys(extract(_comments(4), FakeLLM(), platform="hn",
                         thread_id="t", source_url="u", run_id="r"))


def test_empty_bodies_skipped_in_both_paths():
    comments = [{"id": "a", "body": "real pain here"},
                {"id": "b", "body": "   "},
                {"id": "c", "body": ""}]
    for fn in (lambda cs: extract(cs, FakeLLM(), platform="p", thread_id="t",
                                  source_url="u", run_id="r"),
               lambda cs: extract_batch(cs, FakeLLM(), platform="p",
                                        thread_id="t", source_url="u",
                                        run_id="r", batch_size=10)):
        rows = fn(comments)
        assert [n.unique_key for n in rows] == ["p:t:a"]


def test_batch_spends_one_call_per_chunk():
    """5 comments at batch_size 2 = 3 model calls, not 5."""
    calls = []

    class CountingLLM(FakeLLM):
        def extract_batch(self, bodies):
            calls.append(len(bodies))
            return super().extract_batch(bodies)

    rows = extract_batch(_comments(5), CountingLLM(), platform="hn",
                         thread_id="t", source_url="u", run_id="r",
                         batch_size=2)
    assert calls == [2, 2, 1]
    assert [n.unique_key for n in rows] == [f"hn:t:c{i}" for i in range(5)]


def test_exploding_batch_falls_back_per_comment():
    """A batch call that raises must not lose comments or misattribute rows."""
    class ExplodingLLM(FakeLLM):
        def extract_batch(self, bodies):
            raise RuntimeError("batch endpoint down")

    rows = extract_batch(_comments(3), ExplodingLLM(), platform="hn",
                         thread_id="t", source_url="u", run_id="r",
                         batch_size=10)
    assert [n.unique_key for n in rows] == ["hn:t:c0", "hn:t:c1", "hn:t:c2"]
    assert all(n.extractor_model == FakeLLM.name for n in rows)


def test_short_batch_reply_falls_back_per_comment():
    class ShortLLM(FakeLLM):
        def extract_batch(self, bodies):
            return [NuggetDraft(extracted_insight="only one", category="pain_point",
                                model=self.name)]

    rows = extract_batch(_comments(3), ShortLLM(), platform="hn",
                         thread_id="t", source_url="u", run_id="r",
                         batch_size=10)
    assert [n.unique_key for n in rows] == ["hn:t:c0", "hn:t:c1", "hn:t:c2"]


class _CannedClient:
    """Stands in for ollama.Client: answers chat() with a fixed content string."""

    def __init__(self, content):
        self._content = content
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        return {"message": {"content": self._content}}


def test_live_role_batch_parses_enumerated_reply():
    bodies = ["first pain", "second pain"]
    reply = json.dumps({"items": [
        {"category": "pain_point", "insight": "insight one"},
        {"category": "feature_request", "insight": "insight two"},
    ]})
    llm = OllamaLLM(model="test-model", client=_CannedClient(reply))
    drafts = llm.extract_batch(bodies)
    assert llm.calls == 1
    assert [(d.category, d.extracted_insight, d.model) for d in drafts] == [
        ("pain_point", "insight one", "test-model"),
        ("feature_request", "insight two", "test-model"),
    ]


def test_live_role_batch_mismatch_falls_back_per_item():
    """A short/garbled batch reply retries per comment through the SAME
    single path (and its stand-in accounting), never shipping guesses."""
    bodies = ["first pain", "second pain"]
    script = [
        json.dumps({"items": [{"category": "pain_point",
                               "insight": "only one"}]}),  # short: mismatch
        '{"category": "pain_point", "insight": "single one"}',
        '{"category": "question", "insight": "single two"}',
    ]

    class _SeqClient:
        def __init__(self):
            self.calls = 0

        def chat(self, **kw):
            self.calls += 1
            return {"message": {"content": script[self.calls - 1]}}

    client = _SeqClient()
    llm = OllamaLLM(model="test-model", client=client)
    drafts = llm.extract_batch(bodies)
    assert client.calls == 1 + 2  # one batch + two singles
    assert [(d.category, d.extracted_insight, d.model) for d in drafts] == [
        ("pain_point", "single one", "test-model"),
        ("question", "single two", "test-model"),
    ]


def test_live_role_rate_limit_stops_without_extra_calls():
    """A batch call that exhausts quota stands in directly: no N extra
    refused requests before the caller's brake trips."""
    from jester.llm import GroqRateLimited

    class _LimitedClient:
        calls = 0

        def chat(self, **kw):
            _LimitedClient.calls += 1
            raise GroqRateLimited("exhausted", retry_after=60)

    llm = OllamaLLM(model="test-model", client=_LimitedClient())
    drafts = llm.extract_batch(["a pain", "another pain"])
    assert _LimitedClient.calls == 1
    assert llm.rate_limited == 1
    assert [d.model for d in drafts] == [FakeLLM.name, FakeLLM.name]
