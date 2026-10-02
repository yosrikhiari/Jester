"""The console's Agent models card, backend side (plan S6, gate G9).

The card now shows all five model jobs (the labeller included, the dead
archivist slot gone), each fully resolved with the line behind every value;
saves per-job settings into the profile's `roles:` block; and has a Test
button that makes one real call and says whether the model answered.

Also pins a fix found on the way: the archivist's same-embedding-model guard
(R32) read the old `embedding_model` key directly, so a `roles:` block that
set the embedding model would have been compared against the wrong one.
"""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from jester.config import ConfigError, load_thresholds, save_roles
from jester.console.api import ConsoleAPI
from jester.roles import ENV_PREFIX, ROLES, embedding_model_of, resolve_role

#: The shipped config, by its own path: conftest swaps any module-level
#: REPO_CONFIG for an all-offline copy, and these tests need the real roles
#: (no call leaves the machine: Ollama and Groq are stubbed per test).
SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for role in ROLES:
        monkeypatch.delenv(ENV_PREFIX + role.upper(), raising=False)


@pytest.fixture
def config_dir(tmp_path):
    dst = tmp_path / "config"
    shutil.copytree(SHIPPED_CONFIG, dst)
    return dst


@pytest.fixture
def api(tmp_path, config_dir, monkeypatch):
    a = ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(config_dir))
    monkeypatch.setattr(a, "ollama_models", lambda: ["qwen3:8b", "nomic-embed-text:latest"])
    import jester.console.api as api_mod
    monkeypatch.setattr(api_mod.GroqClient, "list_models", lambda self: [])
    return a


# ---- the card's data ----------------------------------------------------------

def test_every_job_comes_back_resolved_with_its_sources(api):
    res = api.models()
    assert res["roles"] == ["extractor", "synthesizer", "critic", "labeller", "embedding"]
    critic = res["resolved"]["critic"]
    assert (critic["provider"], critic["model"], critic["temperature"]) == \
        ("groq", "openai/gpt-oss-120b", 0.2)
    assert critic["source"]["temperature"] == "built-in"
    assert res["resolved"]["labeller"]["source"]["model"].startswith("the synthesizer")
    assert {"name": "groq", "kind": "openai"} in res["providers"]
    assert "temperature" in res["settings"]
    assert res["unused"] == ["models.archivist: no code reads this model"]


def test_a_declared_provider_is_offered(api, config_dir):
    path = config_dir / "thresholds.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "providers:\n  agentops:\n"
                    "    kind: openai\n    base_url: http://localhost:8080/v1\n",
                    encoding="utf-8")
    assert {"name": "agentops", "kind": "openai"} in api.models()["providers"]


# ---- saving -------------------------------------------------------------------------

def test_saving_writes_the_roles_block_and_keeps_the_rest_of_the_file(api, config_dir):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")

    res = api.update_roles({"labeller": {"provider": "ollama", "model": "qwen3:8b"},
                            "critic": {"temperature": "0.1", "think": "low", "seed": 7}})

    assert res["ok"], res
    t = load_thresholds(path)
    assert resolve_role(t, "labeller").model == "qwen3:8b"
    critic = resolve_role(t, "critic")
    assert (critic.temperature, critic.think, critic.seed) == (0.1, "low", 7)
    after = path.read_text(encoding="utf-8")
    # Everything outside the block is byte-for-byte what it was.
    assert after.startswith(before.rstrip("\n"))
    assert "# How many threads/videos the live worker walks per source" in after


def test_a_blank_value_removes_the_setting_and_an_empty_job_disappears(api, config_dir):
    api.update_roles({"critic": {"temperature": 0.1}})
    api.update_roles({"critic": {"temperature": None}})
    data = yaml.safe_load((config_dir / "thresholds.yaml").read_text(encoding="utf-8"))
    assert "roles" not in data


def test_saving_twice_edits_the_same_block(api, config_dir):
    api.update_roles({"critic": {"seed": 1}})
    api.update_roles({"synthesizer": {"seed": 2}})
    text = (config_dir / "thresholds.yaml").read_text(encoding="utf-8")
    assert text.count("\nroles:") == 1
    data = yaml.safe_load(text)
    assert data["roles"] == {"critic": {"seed": 1}, "synthesizer": {"seed": 2}}


@pytest.mark.parametrize("patch,needle", [
    ({"critic": {"temperature": 9}}, "out of range"),
    ({"critic": {"provider": "nowhere"}}, "not defined"),
    ({"embedding": {"provider": "groq"}}, "only ollama or fake"),
    ({"archivist": {"model": "x"}}, "not a role"),
    ({"critic": {"temprature": 0.2}}, "unknown setting"),
])
def test_a_bad_edit_is_refused_and_the_file_is_untouched(api, config_dir, patch, needle):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")
    res = api.update_roles(patch)
    assert res["ok"] is False and needle in res["error"]
    assert path.read_text(encoding="utf-8") == before


def test_save_roles_creates_the_block_in_a_bare_file(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("llm_provider: fake\n", encoding="utf-8")
    save_roles(path, {"critic": {"provider": "ollama", "model": "qwen3:8b"}})
    assert resolve_role(load_thresholds(path), "critic").provider == "ollama"


def test_save_roles_refuses_without_writing(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("llm_provider: fake\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        save_roles(path, {"critic": {"top_k": 0}})
    assert path.read_text(encoding="utf-8") == "llm_provider: fake\n"


# ---- the Test button -----------------------------------------------------------

class _Ollama:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, **kw):
        return {"message": {"content": self.reply}}

    def embed(self, model, input):
        return {"embeddings": [[0.1] * 768 for _ in input]}


def test_test_reports_a_model_that_answered(api, monkeypatch):
    import ollama
    monkeypatch.setattr(ollama, "Client", lambda **kw: _Ollama(json.dumps(
        {"category": "pain_point", "insight": "backups fail silently"})))
    res = api.test_role("extractor")
    assert res["ok"] and res["answered"] is True
    assert res["model"] == "qwen3:8b" and res["error"] is None
    assert res["sample"] == "backups fail silently"
    assert isinstance(res["latency_ms"], int)


def test_test_reports_why_a_model_did_not_answer(api, monkeypatch):
    import ollama
    monkeypatch.setattr(ollama, "Client", lambda **kw: _Ollama("not json"))
    res = api.test_role("extractor")
    assert res["ok"] and res["answered"] is False
    assert "malformed" in res["error"]


def test_test_the_embedding_job(api, monkeypatch):
    import ollama
    monkeypatch.setattr(ollama, "Client", lambda **kw: _Ollama(""))
    res = api.test_role("embedding")
    assert res["answered"] is True and res["sample"] == "768-dimension vector"


def test_test_a_groq_job_without_a_key_says_so(api, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    res = api.test_role("critic")
    assert res["answered"] is False and "GROQ_API_KEY" in res["error"]


def test_test_refuses_an_unknown_job(api):
    assert api.test_role("archivist")["ok"] is False


# ---- the embedding guard reads the same model the client uses ----------------------

def test_the_embedding_guard_follows_a_roles_block(tmp_path):
    from jester.agents.archivist import Archivist, ModelMismatch
    from jester.config import Thresholds
    from jester.store import meta_set, open_db

    t = Thresholds(embedding_provider="ollama",
                   roles={"embedding": {"model": "mxbai-embed-large"}}).validate()
    assert embedding_model_of(t) == "mxbai-embed-large"

    db = open_db(str(tmp_path / "j.db"))
    meta_set(db, "corpus_embedding_model", "nomic-embed-text")
    with pytest.raises(ModelMismatch) as e:
        Archivist(db, None, t).run([])
    assert "mxbai-embed-large" in str(e.value), (
        "the guard must compare against the model the client will actually use")


# ---- unscored ideas in the Ideas list ---------------------------------------------

@pytest.mark.parametrize("direction", ["desc", "asc"])
def test_unscored_ideas_sort_last_in_both_directions(tmp_path, config_dir, direction):
    """Found in the end-to-end pass: with 603 ideas marked needs_score, the
    default Ideas view (overall, descending) opened on rows with no score."""
    from jester.models import Idea, IdeaScores
    from jester.store import insert_idea, open_db

    path = str(tmp_path / "ideas.db")
    db = open_db(path)
    for n, overall in (("low", 4.0), ("none", None), ("high", 8.0)):
        scores = IdeaScores(demand_signal=None, feasibility=None, competition=None, overall=None) \
            if overall is None else IdeaScores(demand_signal=5, feasibility=5, overall=overall)
        insert_idea(db, Idea(title=n, supporting_nuggets=["k"], scores=scores,
                             needs_score=overall is None))
    api = ConsoleAPI(db_path=path, config_dir=str(config_dir))
    titles = [i["title"] for i in api.ideas(sort="overall", dir=direction)["ideas"]]
    assert titles[-1] == "none"
    assert titles[:2] == (["high", "low"] if direction == "desc" else ["low", "high"])
