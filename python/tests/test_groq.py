"""Groq provider: transport, per-role selection, and the synthesis prompt.

Everything here runs offline — the transport takes an injected opener, the
agents take an injected client. A test that needs the network to tell you the
JSON shape changed is a test nobody runs.
"""
import io
import json
import urllib.error

import pytest

from jester.config import ConfigError, LLM_PROVIDERS, Thresholds
from jester.env import load_env, parse_env
from jester.llm import (
    GROQ_CHAT_MODELS,
    FakeSynthesizerLLM,
    GroqClient,
    GroqCriticLLM,
    GroqError,
    GroqLLM,
    GroqSynthesizerLLM,
    model_name,
    provider_for,
    select_critic_llm,
    select_extractor_llm,
    select_synthesizer_llm,
)
from jester.models import Idea, IdeaScores, Nugget


# ---- fake transport ------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(content, capture=None):
    """An opener that answers every chat call with `content` as the message."""
    def _open(req, timeout=None):
        if capture is not None:
            capture["req"] = req
            if req.data:
                capture["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"choices": [{"message": {"content": content}}]})
    return _open


def opener_raising(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


def _nuggets():
    return [
        Nugget(unique_key="a", platform="reddit", thread_id="t",
               category="pain_point", extracted_insight="docker upgrade ate my mounts"),
        Nugget(unique_key="b", platform="reddit", thread_id="t",
               category="pain_point", extracted_insight="backups fail silently"),
    ]


def _idea():
    return Idea(title="t", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["a", "b"], source_threads=1,
                source_platforms=["reddit"], status="new",
                scores=IdeaScores(demand_signal=5, feasibility=5,
                                  competition=None, overall=5))


# ---- transport -----------------------------------------------------------

def test_missing_key_is_a_named_error_not_a_401(monkeypatch):
    """Nothing should reach the network without a key — the operator gets told
    which file to put it in instead of a Groq auth error."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(GroqError) as e:
        GroqClient(opener=opener_raising(AssertionError("must not be called"))).chat(
            "m", [{"role": "user", "content": "x"}])
    assert "GROQ_API_KEY" in str(e.value) and ".env" in str(e.value)


def test_request_carries_a_user_agent():
    """Groq sits behind Cloudflare, which 403s the default `Python-urllib/3.x`
    with error 1010. Without this header every live call fails."""
    cap = {}
    GroqClient(api_key="k", opener=opener_returning("{}", cap)).chat(
        "m", [{"role": "user", "content": "x"}])
    ua = cap["req"].get_header("User-agent") or ""
    assert ua and "python-urllib" not in ua.lower()
    assert cap["req"].get_header("Authorization") == "Bearer k"


def test_json_object_mode_is_requested():
    """Removes the whole class of 'model wrapped its JSON in prose' failures."""
    cap = {}
    GroqClient(api_key="k", opener=opener_returning("{}", cap)).chat(
        "m", [{"role": "user", "content": "x"}])
    assert cap["body"]["response_format"] == {"type": "json_object"}


def test_http_error_detail_is_surfaced():
    err = urllib.error.HTTPError(
        "u", 429, "Too Many Requests", {},
        io.BytesIO(json.dumps({"error": {"message": "rate limit reached"}}).encode()))
    with pytest.raises(GroqError) as e:
        GroqClient(api_key="k", opener=opener_raising(err)).chat("m", [])
    assert "429" in str(e.value) and "rate limit reached" in str(e.value)


def test_empty_choices_is_an_error_not_an_empty_string():
    def _open(req, timeout=None):
        return _Resp({"choices": []})
    with pytest.raises(GroqError):
        GroqClient(api_key="k", opener=_open).chat("m", [])


def test_list_models_ranks_known_chat_models_and_drops_non_chat():
    served = {"data": [{"id": i} for i in (
        "whisper-large-v3", "openai/gpt-oss-20b", "openai/gpt-oss-120b",
        "meta-llama/llama-prompt-guard-2-22m", "some/new-chat-model",
    )]}

    def _open(req, timeout=None):
        return _Resp(served)

    got = GroqClient(api_key="k", opener=_open).list_models()
    assert got[0] == "openai/gpt-oss-120b", "best-first ordering"
    assert "openai/gpt-oss-20b" in got
    assert "some/new-chat-model" in got, "a model added upstream still belongs"
    assert not any("whisper" in m or "prompt-guard" in m for m in got), \
        "audio and classifier models cannot serve a chat role"


def test_list_models_returns_empty_when_groq_is_unreachable():
    """This feeds the console's model picker; a dead network greys out a
    dropdown, it does not 500 the config page."""
    assert GroqClient(api_key="k", opener=opener_raising(OSError("no route"))).list_models() == []


# ---- agents --------------------------------------------------------------

def test_synthesizer_returns_the_models_three_fields():
    payload = json.dumps({"title": "Mount Guard",
                          "problem_statement": "Mounts vanish on upgrade.",
                          "proposed_solution": "It snapshots volume config and restores it."})
    llm = GroqSynthesizerLLM(client=_stub(payload))
    out = llm.synthesize(_nuggets())
    assert out.title == "Mount Guard"
    assert out.proposed_solution.startswith("It snapshots")
    assert llm.last_error is None


@pytest.mark.parametrize("payload", [
    "not json at all",
    json.dumps({"title": "t"}),                                    # missing keys
    json.dumps({"title": "t", "problem_statement": "p", "proposed_solution": ""}),
    json.dumps({"title": "t", "problem_statement": "p", "proposed_solution": "   "}),
])
def test_synthesizer_falls_back_on_unusable_output(payload):
    """A bad model response must not kill a run — but it must produce the
    deterministic stand-in, not a half-filled idea."""
    out = GroqSynthesizerLLM(client=_stub(payload)).synthesize(_nuggets())
    assert out == FakeSynthesizerLLM().synthesize(_nuggets())


def test_synthesizer_records_why_it_fell_back():
    """`last_error` is how a caller tells 'the model wrote this' from 'the
    model was unreachable' — the two produce very different text."""
    llm = GroqSynthesizerLLM(client=_raising_stub(GroqError("groq HTTP 429: slow down")))
    out = llm.synthesize(_nuggets())
    assert out == FakeSynthesizerLLM().synthesize(_nuggets())
    assert "429" in llm.last_error


def test_synthesis_prompt_forbids_restating_the_problem():
    """The bug this provider exists to fix: the stand-in fills the solution
    with the problem text under a 'Build a focused tool that addresses:'
    prefix. If the prompt stops asserting otherwise, the model will do the
    same thing in more words."""
    sys_prompt = GroqSynthesizerLLM.SYNTH_SYSTEM.lower()
    assert "never restate the problem" in sys_prompt
    assert "build a tool that" in sys_prompt, "the failure mode is named explicitly"
    assert "mechanism" in sys_prompt


def test_extractor_coerces_an_unknown_category():
    payload = json.dumps({"category": "not_a_category", "insight": "x"})
    out = GroqLLM(client=_stub(payload)).extract("body")
    assert out.category == "pain_point"
    assert out.extracted_insight == "x"


def test_critic_never_fabricates_competition():
    """R29: competition stays None unless the model supplied a number."""
    payload = json.dumps({"demand_signal": 7, "feasibility": 6, "competition": None})
    out = GroqCriticLLM(client=_stub(payload)).score(_idea())
    assert out.competition is None
    assert (out.demand_signal, out.feasibility) == (7.0, 6.0)


def test_critic_clamps_to_the_rubric():
    payload = json.dumps({"demand_signal": 99, "feasibility": -4, "competition": 40})
    out = GroqCriticLLM(client=_stub(payload)).score(_idea())
    assert out.demand_signal == 10.0 and out.feasibility == 1.0 and out.competition == 10.0


def test_extractor_default_is_the_cheaper_model():
    """The extractor runs once per comment; the synthesizer once per cluster.
    Defaulting both to the 120b model is a rate-limit waiting to happen."""
    assert GroqLLM()._model == "openai/gpt-oss-20b"
    assert GroqSynthesizerLLM()._model == "openai/gpt-oss-120b"


def _stub(content):
    class C:
        def chat(self, **kw):
            return {"message": {"content": content}}
    return C()


def _raising_stub(exc):
    class C:
        def chat(self, **kw):
            raise exc
    return C()


# ---- per-role provider selection ----------------------------------------

def _t(**kw):
    t = Thresholds()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_groq_is_a_valid_llm_provider():
    assert "groq" in LLM_PROVIDERS
    _t(llm_provider="groq").validate()


def test_groq_is_refused_as_an_embedding_provider():
    """Groq serves no embedding model; accepting it here would validate a
    config that dies at the first vector write, minutes into a run."""
    with pytest.raises(ConfigError) as e:
        _t(embedding_provider="groq").validate()
    assert "embedding_provider" in str(e.value)


def test_role_override_beats_llm_provider():
    t = _t(llm_provider="fake", synthesizer_provider="groq")
    t.validate()
    assert provider_for(t, "synthesizer") == "groq"
    assert provider_for(t, "extractor") == "fake"
    assert isinstance(select_synthesizer_llm(t), GroqSynthesizerLLM)
    assert not isinstance(select_extractor_llm(t), GroqLLM)


def test_empty_override_follows_llm_provider():
    """An untouched config must behave exactly as it did before overrides."""
    t = _t(llm_provider="groq", synthesizer_provider="", critic_provider="   ")
    t.validate()
    for role in ("extractor", "synthesizer", "critic"):
        assert provider_for(t, role) == "groq"
    assert isinstance(select_critic_llm(t), GroqCriticLLM)


def test_invalid_role_override_is_refused():
    with pytest.raises(ConfigError) as e:
        _t(synthesizer_provider="bogus").validate()
    assert "synthesizer_provider" in str(e.value)


def test_role_providers_are_editable_from_the_console():
    for key in ("extractor_provider", "synthesizer_provider", "critic_provider"):
        assert key in Thresholds._EDITABLE


# ---- provenance ----------------------------------------------------------

def test_every_agent_reports_what_actually_ran():
    """The archive used to record `cfg.synthesizer_model` whatever ran, so an
    idea written by the deterministic stand-in was filed under a frontier
    model name — the one field you would trust to explain a score shift."""
    from jester.llm import FakeCriticLLM, FakeLLM
    assert model_name(FakeSynthesizerLLM()) == "fake-synth"
    assert model_name(FakeLLM()) == "fake-llm"
    assert model_name(FakeCriticLLM()) == "fake-critic"
    assert model_name(GroqSynthesizerLLM(model="openai/gpt-oss-120b")) == "openai/gpt-oss-120b"
    # An object with no opinion falls back to the configured name.
    assert model_name(object(), "configured") == "configured"


def test_chat_model_catalogue_excludes_non_chat_families():
    for m in GROQ_CHAT_MODELS:
        assert not any(b in m for b in ("whisper", "orpheus", "prompt-guard"))


# ---- .env loader ---------------------------------------------------------

def test_parse_env_handles_the_shapes_a_dotfile_actually_has():
    got = parse_env(
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        "export EXPORTED=v2\n"
        'QUOTED="has spaces"\n'
        "SINGLE='sq'\n"
        "EMPTY=\n"
        "SPACED = padded \n"
        "NO_EQUALS_SIGN\n"
    )
    assert got == {"PLAIN": "value", "EXPORTED": "v2", "QUOTED": "has spaces",
                   "SINGLE": "sq", "EMPTY": "", "SPACED": "padded"}
    assert "NO_EQUALS_SIGN" not in got


def test_dollar_signs_in_secrets_are_literal():
    assert parse_env("K=a$bc${d}")["K"] == "a$bc${d}"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    """A shell that exported a key for one command is making a deliberate
    override; a dotfile silently outranking it costs an afternoon."""
    (tmp_path / ".env").write_text("GROQ_API_KEY=from-file\nOTHER=from-file\n")
    monkeypatch.setenv("GROQ_API_KEY", "from-shell")
    applied = load_env(tmp_path)
    import os
    assert os.environ["GROQ_API_KEY"] == "from-shell"
    assert os.environ["OTHER"] == "from-file"
    assert "GROQ_API_KEY" not in applied


def test_override_flag_lets_the_file_win(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("K=from-file\n")
    monkeypatch.setenv("K", "from-shell")
    load_env(tmp_path, override=True)
    import os
    assert os.environ["K"] == "from-file"


def test_missing_dotenv_is_not_an_error(tmp_path):
    assert load_env(tmp_path) == {}
