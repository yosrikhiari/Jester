"""Model roles: one resolver for every job's provider, model and settings (S4).

Pins the plan's gates:
  G1  every role resolves, per profile, to a known table
  G2  a file without `roles:` behaves exactly as before, down to the request
  G5  every setting in `roles:` reaches the wire, for both providers
  G6  a setting no code reads is reported
plus precedence, overrides, declared providers and the load-time refusals.
"""
import io
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

from jester.agents.labeller import GroqLabeller, OllamaLabeller, select_labeller
from jester.cli import main as cli_main
from jester.config import ConfigError, Thresholds, load_thresholds, unused_settings
from jester.embed import FakeEmbedding, OllamaEmbedding, select_embedding
from jester.llm import (
    FakeCriticLLM,
    GroqCriticLLM,
    GroqLLM,
    OllamaLLM,
    select_critic_llm,
    select_extractor_llm,
    select_synthesizer_llm,
)
from jester.models import Idea
from jester.roles import ENV_PREFIX, ROLES, RoleError, parse_override, resolve_all, resolve_role

ROOT = Path(__file__).resolve().parents[2]
IDEA = Idea(title="t", problem_statement="p", supporting_nuggets=["a", "b"])
CRITIC_REPLY = json.dumps({"demand_signal": 8, "feasibility": 7, "competition": None})


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for role in ROLES:
        monkeypatch.delenv(ENV_PREFIX + role.upper(), raising=False)


def _profile(name):
    return load_thresholds(ROOT / name / "thresholds.yaml")


def _t(**kw):
    t = Thresholds(**kw)
    return t.validate()


def _row(r):
    return (r.provider, r.kind, r.model, r.temperature, r.json_mode, r.timeout_s, r.think)


# ---- G1: the tables -------------------------------------------------------------

def test_the_default_profile_resolves_as_it_ran_before():
    got = {role: _row(r) for role, r in resolve_all(_profile("config")).items()}
    assert got == {
        "extractor": ("ollama", "ollama", "qwen3:8b", None, None, 180.0, None),
        "synthesizer": ("groq", "openai", "openai/gpt-oss-120b", 0.4, True, 60.0, None),
        "critic": ("groq", "openai", "openai/gpt-oss-120b", 0.2, True, 60.0, None),
        "labeller": ("groq", "openai", "openai/gpt-oss-120b", 0.4, True, 60.0, None),
        "embedding": ("ollama", "ollama", "nomic-embed-text", None, None, 600.0, None),
    }


def test_the_live_profile_resolves_everything_to_local_qwen():
    got = {role: _row(r) for role, r in resolve_all(_profile("config-live")).items()}
    assert got == {
        "extractor": ("ollama", "ollama", "qwen3:8b", None, None, 180.0, None),
        "synthesizer": ("ollama", "ollama", "qwen3:8b", None, None, 300.0, None),
        "critic": ("ollama", "ollama", "qwen3:8b", None, None, 180.0, None),
        "labeller": ("ollama", "ollama", "qwen3:8b", None, None, 300.0, None),
        "embedding": ("ollama", "ollama", "nomic-embed-text", None, None, 600.0, None),
    }


def test_the_offline_profile_resolves_everything_to_the_stand_ins():
    assert {r.kind for r in resolve_all(_profile("docker/config")).values()} == {"fake"}


def test_jester_models_prints_the_table_and_the_unused_key(capsys):
    cli_main(["models", "--config", str(ROOT / "config")])
    out = capsys.readouterr().out
    assert "critic       groq (openai)  openai/gpt-oss-120b" in out
    assert "temperature = 0.2  (built-in)" in out
    assert "unused: models.archivist" in out


def test_jester_models_diff_of_a_profile_with_itself_is_empty(capsys):
    cli_main(["models", "--config", str(ROOT / "config"), "--diff", str(ROOT / "config")])
    assert "identically" in capsys.readouterr().out


# ---- G2: nothing changes on the wire without a roles: block ---------------------

class _OllamaSpy:
    def __init__(self, reply):
        self.reply, self.kwargs = reply, None

    def chat(self, **kw):
        self.kwargs = kw
        return {"message": {"content": self.reply}}


def test_an_ollama_role_still_sends_only_model_and_messages():
    llm = select_extractor_llm(_profile("config"))
    spy = _OllamaSpy(json.dumps({"category": "pain_point", "insight": "x"}))
    llm._client = spy
    assert llm.extract("backups fail").model == "qwen3:8b"
    assert set(spy.kwargs) == {"model", "messages"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def wire(monkeypatch):
    """Capture what an OpenAI-compatible agent puts on the wire."""
    sent = []

    def urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "headers": dict(req.header_items()),
                     "body": json.loads(req.data or b"{}"), "timeout": timeout})
        return _Response(json.dumps(
            {"choices": [{"message": {"content": CRITIC_REPLY}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return sent


def test_a_groq_role_still_sends_the_same_request(wire):
    critic = select_critic_llm(_profile("config"))
    assert critic.score(IDEA).model == "openai/gpt-oss-120b"
    req = wire[0]
    assert req["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["timeout"] == 60.0
    assert set(req["body"]) == {"model", "messages", "temperature", "response_format"}
    assert req["body"]["temperature"] == 0.2
    assert req["body"]["response_format"] == {"type": "json_object"}


# ---- G5: every setting reaches the wire ------------------------------------------

ALL_KNOBS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5,
             "seed": 7, "think": False, "json_mode": True, "num_ctx": 8192,
             "keep_alive": "10m", "timeout_s": 42}


def test_every_ollama_setting_is_sent(monkeypatch):
    import ollama

    built = {}

    class Client:
        def __init__(self, **kw):
            built.update(kw)

        def chat(self, **kw):
            built["chat"] = kw
            return {"message": {"content": json.dumps(
                {"category": "pain_point", "insight": "x"})}}

    monkeypatch.setattr(ollama, "Client", Client)
    t = _t(llm_provider="fake", roles={"extractor": {"provider": "ollama",
                                                     "model": "qwen3:8b", **ALL_KNOBS}},
           providers={"ollama": {"host": "http://gpu-box:11434"}})
    select_extractor_llm(t).extract("backups fail")

    assert built["host"] == "http://gpu-box:11434" and built["timeout"] == 42
    chat = built["chat"]
    assert chat["options"] == {"temperature": 0.7, "top_p": 0.8, "top_k": 20,
                               "presence_penalty": 1.5, "seed": 7, "num_ctx": 8192}
    assert chat["think"] is False
    assert chat["format"] == "json"
    assert chat["keep_alive"] == "10m"


def test_every_openai_setting_is_sent(wire):
    t = _t(llm_provider="fake", roles={"critic": {
        "provider": "groq", "model": "openai/gpt-oss-20b", "temperature": 0.1, "top_p": 0.9,
        "presence_penalty": 0.5, "seed": 3, "think": "low", "json_mode": False,
        "timeout_s": 30, "max_retries": 1}})
    critic = select_critic_llm(t)
    critic.score(IDEA)
    body = wire[0]["body"]
    assert body["model"] == "openai/gpt-oss-20b"
    assert (body["temperature"], body["top_p"], body["presence_penalty"], body["seed"]) == \
        (0.1, 0.9, 0.5, 3)
    assert body["reasoning_effort"] == "low"
    assert "response_format" not in body
    assert wire[0]["timeout"] == 30
    assert critic._client.max_retries == 1


# ---- precedence and sources ------------------------------------------------------

def test_roles_beat_the_old_keys_and_say_so():
    t = _t(llm_provider="fake", critic_provider="groq", critic_model="openai/gpt-oss-120b",
           roles={"critic": {"provider": "ollama", "model": "qwen3:8b", "temperature": 0.3}})
    r = resolve_role(t, "critic")
    assert (r.provider, r.model, r.temperature) == ("ollama", "qwen3:8b", 0.3)
    assert r.source["provider"] == "roles.critic.provider"
    assert r.source["temperature"] == "roles.critic"


def test_defaults_sit_between_a_role_and_its_provider():
    t = _t(llm_provider="ollama", roles={"defaults": {"temperature": 0.5, "think": False},
                                         "critic": {"temperature": 0.1}},
           providers={"ollama": {"temperature": 0.9, "num_ctx": 4096}})
    critic, synth = resolve_role(t, "critic"), resolve_role(t, "synthesizer")
    assert critic.temperature == 0.1 and synth.temperature == 0.5
    assert synth.think is False and synth.num_ctx == 4096
    assert synth.source["num_ctx"] == "providers.ollama"


def test_an_env_override_beats_everything(monkeypatch):
    monkeypatch.setenv("JESTER_ROLE_CRITIC", "ollama:qwen3:8b")
    r = resolve_role(_profile("config"), "critic")
    assert (r.provider, r.model) == ("ollama", "qwen3:8b")
    assert "JESTER_ROLE_CRITIC" in r.source["model"]


def test_the_role_flag_sets_the_override(monkeypatch, capsys):
    cli_main(["models", "--config", str(ROOT / "config"), "--role", "critic=ollama:qwen3:8b"])
    out = capsys.readouterr().out
    assert "critic       ollama (ollama)  qwen3:8b" in out


def test_a_bad_role_flag_is_refused():
    with pytest.raises(SystemExit):
        cli_main(["models", "--config", str(ROOT / "config"), "--role", "archivist=ollama"])


def test_override_parsing_keeps_the_models_own_colon():
    assert parse_override("ollama:qwen3:8b") == ("ollama", "qwen3:8b")
    assert parse_override("fake") == ("fake", None)


def test_the_labeller_follows_the_synthesizer_until_given_its_own():
    t = _t(llm_provider="fake", roles={"synthesizer": {"provider": "groq",
                                                       "model": "qwen/qwen3.6-27b"}})
    assert resolve_role(t, "labeller").model == "qwen/qwen3.6-27b"
    t = _t(llm_provider="fake", roles={"synthesizer": {"provider": "groq"},
                                       "labeller": {"provider": "ollama", "model": "phi4-mini"}})
    lab = select_labeller(t)
    assert isinstance(lab, OllamaLabeller) and lab.name == "phi4-mini"


def test_a_jury_list_is_accepted_and_kept_for_later():
    t = _t(llm_provider="fake", roles={"critic": {
        "provider": "ollama", "model": "qwen3:8b", "models": ["qwen3:8b", "phi4-mini"]}})
    assert resolve_role(t, "critic").models == ("qwen3:8b", "phi4-mini")


# ---- declared providers (plan D4) ------------------------------------------------

def test_a_declared_openai_compatible_server_gets_the_request_and_no_groq_key(wire):
    t = _t(llm_provider="fake",
           providers={"agentops": {"kind": "openai", "base_url": "http://localhost:8080/v1"}},
           roles={"critic": {"provider": "agentops", "model": "qwen3:8b"}})
    critic = select_critic_llm(t)
    assert isinstance(critic, GroqCriticLLM)
    assert critic.score(IDEA).model == "qwen3:8b"
    assert wire[0]["url"] == "http://localhost:8080/v1/chat/completions"
    assert "Authorization" not in wire[0]["headers"], "a Groq key must never leave for elsewhere"


def test_a_declared_provider_can_name_its_own_key(wire, monkeypatch):
    monkeypatch.setenv("AGENTOPS_KEY", "gw-key")
    t = _t(llm_provider="fake",
           providers={"agentops": {"kind": "openai", "base_url": "http://gw/v1",
                                   "key_env": "AGENTOPS_KEY"}},
           roles={"critic": {"provider": "agentops", "model": "m"}})
    select_critic_llm(t).score(IDEA)
    assert wire[0]["headers"]["Authorization"] == "Bearer gw-key"


def test_the_extractor_guard_treats_a_declared_provider_as_real():
    from jester.llm import kind_for

    t = _t(llm_provider="fake",
           providers={"local": {"kind": "openai", "base_url": "http://x/v1"}},
           roles={"extractor": {"provider": "local", "model": "m"}})
    assert kind_for(t, "extractor") == "openai"
    assert isinstance(select_extractor_llm(t), GroqLLM)


# ---- refusals at load time ---------------------------------------------------------

@pytest.mark.parametrize("roles,providers,needle", [
    ({"archivist": {"model": "x"}}, {}, "not a role"),
    ({"critic": {"temprature": 0.2}}, {}, "unknown setting"),
    ({"critic": {"temperature": 5}}, {}, "out of range"),
    ({"critic": {"think": "maybe"}}, {}, "not true, false, low"),
    ({"critic": {"provider": "nowhere"}}, {}, "not defined"),
    ({"embedding": {"provider": "groq"}}, {}, "only ollama or fake"),
    ({"critic": {"provider": "gw"}}, {"gw": {"kind": "openai", "base_url": "http://g/v1"}},
     "needs a model"),
    ({}, {"gw": {"kind": "openai"}}, "needs base_url"),
    ({}, {"gw": {"kind": "smoke-signals"}}, "kind"),
])
def test_a_bad_block_fails_at_load_time(roles, providers, needle):
    with pytest.raises(ConfigError) as e:
        _t(llm_provider="fake", roles=roles, providers=providers)
    assert needle in str(e.value)


def test_no_claude_names_are_left_as_defaults():
    t = Thresholds()
    assert not any("claude" in (getattr(t, f) or "")
                   for f in ("extractor_model", "synthesizer_model", "critic_model",
                             "archivist_model"))
    assert "claude" not in OllamaLLM().name


# ---- G6: unused settings ----------------------------------------------------------

def test_unused_settings_are_reported_and_go_keys_are_not():
    data = yaml.safe_load((ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    assert unused_settings(data) == ["models.archivist: no code reads this model"]
    data["temprature"] = 0.2
    assert "temprature: no code reads this key" in unused_settings(data)


def test_the_warning_is_logged_once_per_file(caplog, tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("models:\n  archivist: phi4-mini\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="jester.config"):
        load_thresholds(path)
        load_thresholds(path)
    assert sum("models.archivist" in r.message for r in caplog.records) == 1


# ---- embedding ----------------------------------------------------------------------

def test_embedding_takes_its_host_and_timeout_from_the_roles_block():
    t = _t(llm_provider="fake", embedding_provider="ollama",
           roles={"embedding": {"timeout_s": 900}},
           providers={"ollama": {"host": "http://gpu:11434"}})
    emb = select_embedding(t)
    assert isinstance(emb, OllamaEmbedding)
    assert (emb._timeout, emb._host, emb._model) == (900.0, "http://gpu:11434",
                                                     "nomic-embed-text")
    assert isinstance(select_embedding(_t(llm_provider="fake")), FakeEmbedding)
