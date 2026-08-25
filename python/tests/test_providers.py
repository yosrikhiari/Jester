"""Provider wiring tests (§37.10): Ollama clients via injected stub transports.

The `ollama` package is never imported here — every client accepts an injected
transport, so parsing/validation/fallback rules are testable offline.
"""
import json
from pathlib import Path

import pytest

from jester.config import Thresholds
from jester.llm import OllamaCriticLLM, OllamaLLM, OllamaSynthesizerLLM
from jester.models import Idea, IdeaScores, Nugget
from jester.store import open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


class StubClient:
    """Ollama-shaped transport returning a canned message content."""

    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def chat(self, model, messages):
        self.calls.append({"model": model, "messages": messages})
        return {"message": {"content": self.content}}


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: config fields + factories ---------------------------------------

def test_factories_default_to_fake():
    from jester.embed import FakeEmbedding, select_embedding
    from jester.llm import (
        FakeCriticLLM,
        FakeLLM,
        FakeSynthesizerLLM,
        select_critic_llm,
        select_extractor_llm,
        select_synthesizer_llm,
    )

    cfg = Thresholds()
    assert isinstance(select_extractor_llm(cfg), FakeLLM)
    assert isinstance(select_synthesizer_llm(cfg), FakeSynthesizerLLM)
    assert isinstance(select_critic_llm(cfg), FakeCriticLLM)
    assert isinstance(select_embedding(cfg), FakeEmbedding)


def test_factory_rejects_unknown_provider():
    from jester.llm import select_extractor_llm

    with pytest.raises(ValueError) as exc:
        select_extractor_llm(Thresholds(llm_provider="chatgpt"))
    assert "fake" in str(exc.value) and "ollama" in str(exc.value)


# --- Task 2: OllamaLLM.extract -------------------------------------------------

def _extractor(client):
    from jester.llm import OllamaLLM
    return OllamaLLM(model="test-model", client=client)


def test_ollama_extract_parses_clean_json():
    llm = _extractor(StubClient(json.dumps({"category": "feature_request", "insight": "wants auto backup"})))
    draft = llm.extract("please build auto backup")
    assert draft.category == "feature_request"
    assert draft.extracted_insight == "wants auto backup"


def test_ollama_extract_parses_fenced_json():
    fenced = '```json\n{"category": "pain_point", "insight": "sync drops"}\n```'
    draft = _extractor(StubClient(fenced)).extract("sync drops every night")
    assert draft.category == "pain_point"
    assert draft.extracted_insight == "sync drops"


def test_ollama_extract_coerces_disallowed_category():
    draft = _extractor(StubClient('{"category": "rant", "insight": "x"}')).extract("x")
    assert draft.category == "pain_point"


def test_ollama_extract_falls_back_on_invalid_json():
    body = "I lose my config every weekend"
    draft = _extractor(StubClient("not json at all")).extract(body)
    # Deterministic fallback mirrors FakeLLM: insight = body[:200].
    assert draft.extracted_insight == body.strip()[:200]
    assert draft.category in {"pain_point", "feature_request"}


# --- Task 3: synth + critic clients ---------------------------------------------

def _synth(client):
    from jester.llm import OllamaSynthesizerLLM
    return OllamaSynthesizerLLM(model="test-model", client=client)


def test_ollama_synth_parses_and_falls_back():
    nuggets = [Nugget(unique_key="k1", platform="reddit", extracted_insight="backups vanish")]
    good = _synth(StubClient(json.dumps({
        "title": "Pre-update snapshots",
        "problem_statement": "configs lost",
        "proposed_solution": "snapshot first",
    }))).synthesize(nuggets)
    assert good.title == "Pre-update snapshots"

    fallback = _synth(StubClient("garbage")).synthesize(nuggets)
    assert "backups vanish" in fallback.problem_statement  # mirrors FakeSynthesizerLLM


def _critic(client):
    from jester.llm import OllamaCriticLLM
    return OllamaCriticLLM(model="test-model", client=client)


def _idea():
    return Idea(title="t", supporting_nuggets=["a", "b"], scores=IdeaScores())


def test_ollama_critic_parses_clamps_and_keeps_competition_none():
    out = _critic(StubClient(json.dumps({
        "demand_signal": 42.0, "feasibility": -3.0,
    }))).score(_idea())
    assert out.demand_signal == 10.0   # clamped to rubric range
    assert out.feasibility == 1.0      # clamped to rubric range
    assert out.competition is None     # R29: never fabricate


def test_ollama_critic_accepts_explicit_competition():
    out = _critic(StubClient(json.dumps({
        "demand_signal": 7.0, "feasibility": 6.0, "competition": 5.0,
    }))).score(_idea())
    assert out.competition == 5.0


def test_ollama_critic_falls_back_on_malformed():
    out = _critic(StubClient("nope")).score(_idea())
    assert out.competition is None
    assert out.feasibility == 5.0      # mirrors FakeCriticLLM


# --- Task 4: embedding + cmd_run wiring ------------------------------------------

class StubEmbedClient:
    def __init__(self):
        self.seen = None

    def embed(self, model, input):
        self.seen = list(input)
        return {"embeddings": [[0.1] * 768 for _ in input]}


def test_ollama_embedding_passthrough_and_factory():
    from jester.embed import OllamaEmbedding, select_embedding

    stub = StubEmbedClient()
    emb = OllamaEmbedding(model="nomic-embed-text", client=stub)
    vecs = emb.embed(["a", "b"])
    assert stub.seen == ["a", "b"]
    assert len(vecs) == 2 and len(vecs[0]) == 768 and emb.dim == 768
    assert isinstance(select_embedding(Thresholds(embedding_provider="ollama")), OllamaEmbedding)


def test_cmd_run_wiring_reaches_factory(tmp_path, monkeypatch):
    from jester.cli import cmd_run

    # Prove cmd_run consults the factory: a bogus provider must explode there.
    monkeypatch.setattr(
        "jester.cli.load_config",
        lambda _p: _ns(thresholds=Thresholds(llm_provider="bogus")),
    )
    with pytest.raises(ValueError):
        cmd_run(argparse_namespace(db=str(tmp_path / "w.db"), run="wire"))


def argparse_namespace(**kw):
    from argparse import Namespace
    return Namespace(config=str(REPO_CONFIG), **kw)


# --- §37.16: response-shape compatibility (pydantic SDK vs dict) -----------------

class _Msg:
    def __init__(self, content):
        self.content = content


class PydanticStyleClient:
    """Mimics ollama >=0.4: ChatResponse is a pydantic model — attribute access only."""

    def __init__(self, content):
        self.content = content

    def chat(self, model, messages):
        outer = type("R", (), {})
        r = outer()
        setattr(r, "message", _Msg(self.content))
        return r


class PydanticEmbedClient:
    class _Resp:
        def __init__(self, embeddings):
            self.embeddings = embeddings

    def __init__(self, dim=768, n=1):
        self.dim, self.n = dim, n

    def embed(self, model, input):
        return self._Resp([[0.1] * self.dim for _ in input])


def test_ollama_extract_accepts_pydantic_style_response():
    llm = OllamaLLM(model="m", client=PydanticStyleClient('{"category": "pain_point", "insight": "x"}'))
    draft = llm.extract("body")
    assert draft.category == "pain_point"


def test_ollama_synth_accepts_pydantic_style_response():
    import json as _json
    payload = _json.dumps({"title": "T", "problem_statement": "P", "proposed_solution": "S"})
    out = OllamaSynthesizerLLM(model="m", client=PydanticStyleClient(payload)).synthesize(
        [Nugget(unique_key="k", extracted_insight="i")]
    )
    assert out.title == "T"


def test_ollama_critic_accepts_pydantic_style_response():
    import json as _json
    payload = _json.dumps({"demand_signal": 6.0, "feasibility": 5.0})
    out = OllamaCriticLLM(model="m", client=PydanticStyleClient(payload)).score(_idea())
    assert out.demand_signal == 6.0


def test_ollama_embedding_accepts_pydantic_style_response():
    from jester.embed import OllamaEmbedding

    emb = OllamaEmbedding(model="nomic-embed-text", client=PydanticEmbedClient())
    vecs = emb.embed(["a"])
    assert len(vecs[0]) == 768
