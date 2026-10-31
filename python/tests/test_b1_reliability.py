"""B1 — "a day that completes": honest status, single flight, a draining backlog.

Test-first suite for the 2026-10-31 strategy report's B1 batch:

* S-02  a run cut off by the provider quota is recorded as `quota_stopped`
        (with the model and the reset time), not as `completed`.
* S-03  a second tick that overlaps a live run stands down without a run row.
* S-04  the re-score pass runs even when synthesis hit the quota, respects
        its own budget, and leaves stalled ideas alone.
* S-13  no shipped threshold key is dead.

Everything here drives the real `cmd_run` / `cmd_treat` over a real
temporary SQLite database. No model server and no network.
"""
import json
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

import jester.cli as cli
from jester.agents.critic import rescore_pending
from jester.config import load_config, unused_settings
from jester.llm import CriticDraft, FakeCriticLLM, FakeSynthesizerLLM
from jester.models import Idea, IdeaScores
from jester.roles import resolve_role
from jester.store import (
    ConcurrentRunError,
    enqueue_batch,
    insert_idea,
    open_db,
    pending_batches,
    start_run,
)

CRITIC_KEY = "groq/openai/gpt-oss-120b"
SYNTH_KEY = "groq/openai/gpt-oss-120b"

COMMENTS = [
    {"body": "The nightly backup job fails silently after every upgrade and "
             "nobody notices for weeks until a restore is needed", "fingerprint": "c1",
     "author": "a1", "upvotes": 5},
    {"body": "Our restore drill showed half of the backups were empty files "
             "and the dashboard still reported every job as green", "fingerprint": "c2",
     "author": "a2", "upvotes": 4},
]


def _set(config_dir, **keys):
    path = config_dir / "thresholds.yaml"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.split(":", 1)[0].strip() not in keys]
    lines += [f"{k}: {v}" for k, v in keys.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_dir


def _queue(db_path, batches=1):
    db = open_db(str(db_path))
    for b in range(batches):
        enqueue_batch(db, "reddit", f"https://reddit.test/t/{b}", f"t{b}",
                      [dict(c, fingerprint=f"{c['fingerprint']}-{b}") for c in COMMENTS])
    return db


def _waiting_idea(db, title="waiting"):
    return insert_idea(db, Idea(
        title=title,
        supporting_nuggets=["k1", "k2"],
        scores=IdeaScores(demand_signal=None, feasibility=None,
                          competition=None, overall=None),
        needs_score=True,
        run_id="seed",
    ))


class RefusingCritic:
    """The critic that was asked did not answer: its quota is gone."""

    name = "refusing-critic"

    def __init__(self, settings):
        self.settings = settings
        self.calls = 0
        self.fallbacks = 0
        self.rate_limited = 0
        self.retry_after = 900.0

    def score(self, idea):
        self.calls += 1
        self.rate_limited += 1
        self.fallbacks += 1
        return CriticDraft(demand_signal=None, feasibility=None,
                           competition=None, model="fake-critic")


class QuotaSynthesizer:
    """The synthesizer makes one call, is refused, and the run must stop."""

    name = "quota-synth"

    def __init__(self, settings):
        self.settings = settings
        self.calls = 0
        self.fallbacks = 0
        self.rate_limited = 0
        self.retry_after = 900.0

    def synthesize(self, nuggets):
        self.calls += 1
        self.rate_limited += 1
        return FakeSynthesizerLLM().synthesize(nuggets)


# ---- S-02: the status is honest ------------------------------------------------

def test_run_status_quota_stopped(tmp_path, offline_config, monkeypatch, capsys):
    _set(offline_config, critic_provider="groq")
    settings = resolve_role(load_config(offline_config).thresholds, "critic")
    refusing = RefusingCritic(settings)
    monkeypatch.setattr(cli, "select_critic_llm", lambda cfg: refusing)

    db_path = tmp_path / "j.db"
    _queue(db_path)

    res = cli.cmd_run(Namespace(config=str(offline_config), db=str(db_path), run="r1"))

    row = open_db(str(db_path)).execute(
        "SELECT status, models_used FROM runs WHERE run_id='r1'").fetchone()
    assert row[0] == "quota_stopped", (
        "a run the provider cut off must not be filed as completed")
    used = json.loads(row[1])
    stop = used["quota_stop"]
    assert stop["models"]["critic"]["key"] == CRITIC_KEY
    assert stop["models"]["critic"]["blocked_until"]
    assert res["status"] == "quota_stopped"
    assert "the provider's quota ran out mid-run" in capsys.readouterr().out


# ---- S-03: one owner at a time --------------------------------------------------

def test_overlapping_tick_stands_down_without_a_run_row(tmp_path, offline_config,
                                                        capsys):
    db_path = tmp_path / "j.db"
    db = _queue(db_path)
    start_run(db, "live")

    res = cli.cmd_treat(Namespace(config=str(offline_config), db=str(db_path),
                                  run=None, force=False))

    assert res["skipped"] is True
    rows = open_db(str(db_path)).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert rows == 1, "a tick that stood down must not write a run row"
    assert "still in progress" in capsys.readouterr().out


def test_start_run_refuses_a_second_live_run(tmp_path):
    path = str(tmp_path / "j.db")
    first, second = open_db(path), open_db(path)
    start_run(first, "one")
    with pytest.raises(ConcurrentRunError):
        start_run(second, "two")


# ---- S-04: the re-score pass has its own budget --------------------------------

def test_rescore_runs_when_synthesis_stopped_on_quota(tmp_path, offline_config,
                                                       monkeypatch):
    _set(offline_config, synthesizer_provider="groq")
    synth_settings = resolve_role(load_config(offline_config).thresholds, "synthesizer")
    quota_synth = QuotaSynthesizer(synth_settings)
    monkeypatch.setattr(cli, "select_synthesizer_llm", lambda cfg: quota_synth)

    db_path = tmp_path / "j.db"
    db = _queue(db_path)
    waiting_id = _waiting_idea(db)

    cli.cmd_run(Namespace(config=str(offline_config), db=str(db_path), run="r1"))

    row = open_db(str(db_path)).execute(
        "SELECT needs_score FROM ideas WHERE id=?", (waiting_id,)).fetchone()
    assert row[0] == 0, "the waiting idea must still be scored after a quota stop"


def test_rescore_respects_its_budget_and_attempt_cap(tmp_path, offline_config):
    db = open_db(str(tmp_path / "j.db"))
    for i in range(10):
        _waiting_idea(db, f"waiting {i}")
    stalled_id = _waiting_idea(db, "stalled")
    db.execute("UPDATE ideas SET rescore_attempts=5 WHERE id=?", (stalled_id,))
    db.commit()

    cfg = load_config(offline_config).thresholds
    out = rescore_pending(db, cfg, FakeCriticLLM(), limit=3)

    assert out["scored"] == 3, "no run may exceed its per-run budget"
    assert out["still_waiting"] == 8, "7 budget-starved + 1 stalled"
    row = db.execute("SELECT rescore_attempts FROM ideas WHERE id=?",
                     (stalled_id,)).fetchone()
    assert row[0] == 5, "a stalled idea must not be retried again"
    assert out["stalled"] == 1


def test_backlog_drains_monotonically(tmp_path, offline_config):
    db = open_db(str(tmp_path / "j.db"))
    for i in range(607):
        _waiting_idea(db, f"waiting {i}")
    cfg = load_config(offline_config).thresholds

    counts = []
    for _ in range(20):
        out = rescore_pending(db, cfg, FakeCriticLLM(), limit=31)
        counts.append(out["still_waiting"])

    assert counts[0] == 576
    assert counts[-1] == 0, "a large budget over bounded runs reaches zero"
    assert all(a >= b for a, b in zip(counts, counts[1:])), counts


# ---- S-13: no dead shipped setting ---------------------------------------------

def test_shipped_thresholds_have_no_unread_keys():
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    assert unused_settings(data) == []
