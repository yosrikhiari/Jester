"""Every live agent fails the same way, whichever provider serves it (plan S2).

The rule: a call that errors, times out, hits a limit, or replies with
something unusable is COUNTED, its reason kept, and the deterministic stand-in
answers under its own name. Callers key on that name to refuse archiving it.

The Ollama agents used to have none of this. A transport error ended the run;
a bad reply came back stamped with the real model's name and was archived (3
ideas in the live archive carry the stand-in's "Build a focused tool that
addresses:" solution under `qwen3:8b`); and the labeller ignored the model it
was configured with. The grid below is 4 jobs x 2 providers x 4 failures.
"""
import json
from pathlib import Path

import pytest

from jester.agents.labeller import (
    FakeLabeller,
    GroqLabeller,
    OllamaLabeller,
    select_labeller,
)
from jester.agents.synthesizer import Synthesizer
from jester.config import load_config
from jester.embed import OllamaEmbedding
from jester.llm import (
    FakeCriticLLM,
    FakeLLM,
    FakeSynthesizerLLM,
    GroqCriticLLM,
    GroqLLM,
    GroqRateLimited,
    GroqSynthesizerLLM,
    OllamaCriticLLM,
    OllamaLLM,
    OllamaSynthesizerLLM,
)
from jester.models import Idea, Nugget
from jester.store import insert_nugget, open_db, unprocessed_nuggets

ROOT = Path(__file__).resolve().parents[2]


class _Client:
    """Provider-shaped transport: one canned reply, or one exception."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"message": {"content": self.reply}}


def _ollama_busy():
    """What a refusing Ollama raises. Ollama has no quota, so it is an
    ordinary failure there, never a rate-limit stop."""
    from ollama import ResponseError
    return ResponseError("server busy", 429)


NUGGETS = [Nugget(unique_key=f"k{i}", platform="reddit", thread_id="t",
                  extracted_insight=f"backups fail silently {i}") for i in range(2)]
IDEA = Idea(title="Backup Sentinel", problem_statement="Backups fail silently.",
            supporting_nuggets=["k0", "k1"])

# job -> (call, stand-in name, a good reply, a parsed-but-unusable reply)
JOBS = {
    "extractor": (lambda a: a.extract("backups fail silently after upgrades"),
                  FakeLLM.name,
                  json.dumps({"category": "pain_point", "insight": "backups fail"}),
                  None),  # every parsed extractor reply is usable (coerced)
    "synthesizer": (lambda a: a.synthesize(NUGGETS),
                    FakeSynthesizerLLM.name,
                    json.dumps({"title": "Backup Sentinel",
                                "problem_statement": "Backups fail silently.",
                                "proposed_solution": "Watches every backup job and "
                                                     "restores a sample file nightly."}),
                    json.dumps({"title": "x", "problem_statement": "Backups fail.",
                                "proposed_solution": "Build a focused tool that "
                                                     "addresses: backups fail."})),
    "critic": (lambda a: a.score(IDEA),
               FakeCriticLLM.name,
               json.dumps({"demand_signal": 8, "feasibility": 7, "competition": None}),
               json.dumps({"demand_signal": "high"})),
    "labeller": (lambda a: a.label(["backups fail", "restore broke"]),
                 FakeLabeller.name,
                 json.dumps({"label": "Backups fail silently",
                             "problem_statement": "People lose data.", "coherent": True}),
                 json.dumps({"label": "", "problem_statement": "p"})),
}

AGENTS = {
    ("extractor", "ollama"): OllamaLLM, ("extractor", "groq"): GroqLLM,
    ("synthesizer", "ollama"): OllamaSynthesizerLLM,
    ("synthesizer", "groq"): GroqSynthesizerLLM,
    ("critic", "ollama"): OllamaCriticLLM, ("critic", "groq"): GroqCriticLLM,
    ("labeller", "ollama"): OllamaLabeller, ("labeller", "groq"): GroqLabeller,
}

FAILURES = {
    "transport": lambda provider: ConnectionError("connection refused"),
    "timeout": lambda provider: TimeoutError("read timed out"),
    "bad-json": lambda provider: "this is not json",
    "rate-limit": lambda provider: (GroqRateLimited("groq HTTP 429", retry_after=3600)
                                    if provider == "groq" else _ollama_busy()),
}


def _agent(job, provider, reply):
    return AGENTS[(job, provider)](model="test-model", client=_Client(reply))


@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("provider", ["ollama", "groq"])
@pytest.mark.parametrize("job", sorted(JOBS))
def test_a_failed_call_is_counted_and_signed_by_the_stand_in(job, provider, failure):
    call, standin, _good, _bad = JOBS[job]
    agent = _agent(job, provider, FAILURES[failure](provider))

    out = call(agent)  # never raises

    assert out.model == standin
    assert agent.fallbacks == 1 and agent.last_error
    quota_stop = provider == "groq" and failure == "rate-limit"
    assert agent.rate_limited == (1 if quota_stop else 0), (
        "only a provider quota is a reason to stop the run; Ollama has none")


@pytest.mark.parametrize("provider", ["ollama", "groq"])
@pytest.mark.parametrize("job", sorted(JOBS))
def test_a_reply_that_fails_the_output_check_is_a_fallback(job, provider):
    call, standin, _good, bad = JOBS[job]
    if bad is None:
        pytest.skip("every parsed extractor reply is usable")
    agent = _agent(job, provider, bad)
    assert call(agent).model == standin
    assert agent.fallbacks == 1


@pytest.mark.parametrize("provider", ["ollama", "groq"])
@pytest.mark.parametrize("job", sorted(JOBS))
def test_a_good_reply_is_signed_by_the_model(job, provider):
    call, _standin, good, _bad = JOBS[job]
    agent = _agent(job, provider, good)
    assert call(agent).model == "test-model"
    assert agent.fallbacks == 0 and agent.last_error is None


# ---- the run-level consequences (bug B2) ----------------------------------

def _db(tmp_path, groups=3):
    db = open_db(str(tmp_path / "j.db"))
    for g in range(groups):
        for i in range(2):
            insert_nugget(db, Nugget(unique_key=f"t{g}-n{i}", platform="reddit",
                                     thread_id=f"thread-{g}", category="pain_point",
                                     extracted_insight=f"insight {g}.{i}", run_id="r"))
    return db


def _cfg():
    return load_config(ROOT / "config").thresholds


def test_ollama_down_no_longer_ends_the_run(tmp_path):
    db = _db(tmp_path)
    synth = OllamaSynthesizerLLM(model="qwen3:8b", client=_Client(ConnectionError("down")))
    syn = Synthesizer(db, _cfg())
    ideas = syn.run(synth, FakeCriticLLM(), run_id="r")
    assert ideas == []
    assert syn.skipped_fallback == 3
    assert len(unprocessed_nuggets(db)) == 6, "the groups stay queued for a later run"


def test_a_bad_ollama_reply_is_no_longer_archived_as_an_idea(tmp_path):
    """The 3 live ideas with the stand-in's solution under `qwen3:8b`."""
    db = _db(tmp_path)
    synth = OllamaSynthesizerLLM(model="qwen3:8b", client=_Client("not json"))
    ideas = Synthesizer(db, _cfg()).run(synth, FakeCriticLLM(), run_id="r")
    assert ideas == []
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0


def test_a_stand_in_draft_is_caught_by_its_stamp_alone(tmp_path):
    """An agent with no counters at all still cannot slip a stand-in draft
    past the synthesizer: the draft says who wrote it."""
    class NoCounters:
        name = "some-model"

        def synthesize(self, nuggets):
            return FakeSynthesizerLLM().synthesize(nuggets)

    db = _db(tmp_path, groups=1)
    assert Synthesizer(db, _cfg()).run(NoCounters(), FakeCriticLLM(), run_id="r") == []


# ---- timeouts (gate G4) ------------------------------------------------------

@pytest.mark.parametrize("cls", [OllamaLLM, OllamaSynthesizerLLM, OllamaCriticLLM,
                                 OllamaLabeller, OllamaEmbedding])
def test_every_ollama_client_is_built_with_a_finite_timeout(cls, monkeypatch):
    import ollama

    seen = {}

    class Spy:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(ollama, "Client", Spy)
    cls(model="m")._ensure_client()
    assert 0 < seen.get("timeout", 0) < float("inf")


# ---- the labeller follows the synthesizer (bug B3) -------------------------

def test_the_labeller_uses_the_configured_synthesizer_model():
    t = _cfg()
    t.synthesizer_model = "qwen/qwen3.6-27b"
    lab = select_labeller(t)
    assert isinstance(lab, GroqLabeller)
    assert lab.name == "qwen/qwen3.6-27b"


def test_an_all_ollama_profile_gets_a_real_labeller():
    """config-live sets llm_provider: ollama and no per-role override. The
    labeller read only synthesizer_provider, so it fell back to keywords."""
    t = load_config(ROOT / "config-live").thresholds
    lab = select_labeller(t)
    assert isinstance(lab, OllamaLabeller)
    assert lab.name == t.synthesizer_model


def test_an_offline_profile_still_gets_the_keyword_labeller():
    t = load_config(ROOT / "docker" / "config").thresholds
    assert isinstance(select_labeller(t), FakeLabeller)
