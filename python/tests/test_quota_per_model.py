"""Provenance and quota per model (plan S5, gate G7, decision D9).

Two changes, driven through the real `cmd_run` / `cmd_treat` over a real
database:

* A run records everything each job was asked to run with (provider, model,
  sampling, timeouts, any --role override, prompt versions with the labeller)
  and, at the end, what happened (calls, fallbacks, rate limits per job).

* Quota blocks are filed per provider AND model. Groq's free tier counts each
  model separately, so one model running out used to stop every job, the
  local extractor included. Now only the jobs on that model sit out; an old
  provider-wide "groq" row is still honoured until it expires.
"""
import json
import urllib.request
from argparse import Namespace
from types import SimpleNamespace

import pytest

from jester import llm_quota
from jester.cli import cmd_run, cmd_treat
from jester.config import load_config
from jester.llm import GroqLLM, GroqRateLimited
from jester.roles import ENV_PREFIX, ROLES, resolve_role
from jester.store import enqueue_batch, open_db, pending_batches, unprocessed_nuggets

CRITIC_KEY = "groq/openai/gpt-oss-120b"
EXTRACTOR_KEY = "groq/qwen3:8b"

COMMENTS = [
    {"body": "The nightly backup job fails silently after every upgrade and "
             "nobody notices for weeks until a restore is needed", "fingerprint": "c1",
     "author": "a1", "upvotes": 5},
    {"body": "Our restore drill showed half of the backups were empty files "
             "and the dashboard still reported every job as green", "fingerprint": "c2",
     "author": "a2", "upvotes": 4},
]


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for role in ROLES:
        monkeypatch.delenv(ENV_PREFIX + role.upper(), raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every Groq call in this file must be avoided or stubbed."""
    def refuse(*a, **k):
        raise AssertionError("a paused or stubbed job made a network call")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def _set(config_dir, **keys):
    path = config_dir / "thresholds.yaml"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.split(":", 1)[0].strip() not in keys]
    lines += [f"{k}: {v}" for k, v in keys.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_dir


def _run(config_dir, db_path, run="r1", **kw):
    return cmd_run(Namespace(config=str(config_dir), db=str(db_path), run=run, **kw))


def _models_used(db_path, run="r1"):
    row = open_db(str(db_path)).execute(
        "SELECT models_used FROM runs WHERE run_id=?", (run,)).fetchone()
    return json.loads(row[0])


def _queue(db_path, batches=1):
    db = open_db(str(db_path))
    for b in range(batches):
        enqueue_batch(db, "reddit", f"https://reddit.test/t/{b}", f"t{b}",
                      [dict(c, fingerprint=f"{c['fingerprint']}-{b}") for c in COMMENTS])
    return db


# ---- G7: what a run records ----------------------------------------------------

def test_a_run_records_every_jobs_settings_and_what_happened(tmp_path, offline_config):
    db_path = tmp_path / "j.db"
    _run(offline_config, db_path)
    used = _models_used(db_path)

    assert set(used["roles"]) == set(ROLES)
    critic = used["roles"]["critic"]
    assert {"provider", "kind", "model"} <= set(critic)
    assert used["prompt_versions"]["labeller"] == 1
    assert len(used["models"]) == 3, "the old shape is kept for older readers"
    assert set(used["outcome"]) == {"extractor", "synthesizer", "critic"}
    assert all({"calls", "fallbacks", "rate_limited", "model"} <= set(v)
               for v in used["outcome"].values())


def test_a_role_override_is_recorded(tmp_path, offline_config, monkeypatch):
    monkeypatch.setenv("JESTER_ROLE_CRITIC", "fake")
    db_path = tmp_path / "j.db"
    _run(offline_config, db_path)
    assert _models_used(db_path)["overrides"] == {"critic": "fake"}


def test_settings_from_a_roles_block_are_recorded(tmp_path, offline_config):
    path = offline_config / "thresholds.yaml"
    path.write_text(path.read_text(encoding="utf-8")
                    + "roles:\n  critic:\n    provider: fake\n    seed: 11\n", encoding="utf-8")
    db_path = tmp_path / "j.db"
    _run(offline_config, db_path)
    assert _models_used(db_path)["roles"]["critic"]["seed"] == 11


# ---- D9: a job sits out only when ITS model is blocked -----------------------------

def test_a_blocked_critic_model_files_ideas_unscored_without_a_call(tmp_path, offline_config):
    _set(offline_config, critic_provider="groq")
    db_path = tmp_path / "j.db"
    llm_quota.record_block(open_db(str(db_path)), CRITIC_KEY, 3600, "test")

    res = _run(offline_config, db_path)

    assert res["paused"] == {"critic": CRITIC_KEY}
    rows = open_db(str(db_path)).execute(
        "SELECT needs_score, overall FROM ideas").fetchall()
    assert rows and all(r[0] == 1 and r[1] is None for r in rows)
    used = _models_used(db_path)
    assert used["paused"] == {"critic": CRITIC_KEY}
    assert used["outcome"]["critic"]["calls"] == 0


def test_a_blocked_synthesizer_model_leaves_the_nuggets_queued(tmp_path, offline_config,
                                                               capsys):
    _set(offline_config, synthesizer_provider="groq")
    db_path = tmp_path / "j.db"
    llm_quota.record_block(open_db(str(db_path)), CRITIC_KEY, 3600, "test")

    res = _run(offline_config, db_path)

    assert res["ideas"] == 0 and res["paused"] == {"synthesizer": CRITIC_KEY}
    assert len(unprocessed_nuggets(open_db(str(db_path)))) > 0
    assert "synthesis paused" in capsys.readouterr().out


def test_jobs_with_no_quota_are_never_paused(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    llm_quota.record_block(db, "groq", 3600, "an old provider-wide block")
    cfg = load_config(_config_live()).thresholds  # everything on Ollama
    assert llm_quota.blocked_roles(db, {r: resolve_role(cfg, r) for r in ROLES}) == {}


def test_an_old_groq_row_still_blocks_every_groq_model(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    llm_quota.record_block(db, "groq", 3600, "old")
    assert llm_quota.blocked_for(db, CRITIC_KEY) > 3500
    llm_quota.clear_block(db, CRITIC_KEY)
    assert llm_quota.blocked_for(db, "groq") == 0.0, "a model answered: the old row is stale"


def test_settle_records_per_model_and_never_clears_a_model_that_ran_out(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    cfg = load_config(_repo_config()).thresholds
    synth = SimpleNamespace(settings=resolve_role(cfg, "synthesizer"),
                            calls=3, fallbacks=0, rate_limited=0, retry_after=None)
    critic = SimpleNamespace(settings=resolve_role(cfg, "critic"),
                             calls=1, fallbacks=1, rate_limited=1, retry_after=1800.0)
    extractor = SimpleNamespace(settings=resolve_role(cfg, "extractor"),
                                calls=9, fallbacks=0, rate_limited=0, retry_after=None)

    limited = llm_quota.settle(db, {"synthesizer": synth, "critic": critic,
                                    "extractor": extractor}, "r")

    assert limited == {"critic": CRITIC_KEY}
    assert llm_quota.blocked_for(db, CRITIC_KEY) > 1700, (
        "the synthesizer answered on the same model earlier; that must not "
        "clear the block the critic just recorded")
    assert [b["provider"] for b in llm_quota.active_blocks(db)] == [CRITIC_KEY]


# ---- treat -------------------------------------------------------------------------

def test_treat_keeps_extracting_while_only_the_synthesizer_model_is_blocked(
        tmp_path, offline_config):
    _set(offline_config, synthesizer_provider="groq")
    db_path = tmp_path / "j.db"
    db = _queue(db_path)
    llm_quota.record_block(db, CRITIC_KEY, 3600, "test")

    res = cmd_treat(Namespace(config=str(offline_config), db=str(db_path), run=None,
                              force=False))

    assert res["treated"] == 1 and res["pending"] == 0
    assert res["paused"] == {"synthesizer": CRITIC_KEY}


def test_treat_exits_when_it_can_neither_extract_nor_synthesize(tmp_path, offline_config):
    _set(offline_config, extractor_provider="groq", synthesizer_provider="groq")
    db_path = tmp_path / "j.db"
    db = _queue(db_path)
    llm_quota.record_block(db, "groq", 3600, "both models")

    res = cmd_treat(Namespace(config=str(offline_config), db=str(db_path), run=None,
                              force=False))
    assert res == {"ok": True, "treated": 0, "pending": 1, "blocked": True}


def test_extraction_stops_at_the_extractors_first_quota_refusal(
        tmp_path, offline_config, monkeypatch):
    import jester.cli as cli

    _set(offline_config, extractor_provider="groq")
    calls = []

    class Refuses:
        def chat(self, **kw):
            calls.append(1)
            raise GroqRateLimited("groq HTTP 429", retry_after=900)

    monkeypatch.setattr(cli, "select_extractor_llm", lambda cfg: GroqLLM(
        client=Refuses(), settings=resolve_role(cfg, "extractor")))
    db_path = tmp_path / "j.db"
    _queue(db_path, batches=2)

    res = _run(offline_config, db_path)

    assert len(calls) == 1, "four comments queued, one refused call made"
    assert res["limited"] == {"extractor": EXTRACTOR_KEY}
    db = open_db(str(db_path))
    assert len(pending_batches(db)) == 2, "both batches stay queued"
    assert llm_quota.blocked_for(db, EXTRACTOR_KEY) > 800


def _repo_config():
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "config"


def _config_live():
    return _repo_config().parent / "config-live"
