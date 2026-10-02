"""Clearing the scores the stand-in critic left behind before S1 (plan S3).

Until S1, a critic call that failed left FakeCriticLLM's numbers on the idea
under the real model's name. They are recognisable only by shape (the
stand-in is a formula), so `jester rescore --standin` finds them by that
shape, reports without writing, and with --apply backs up, records and
clears them so the normal re-score path gives them real numbers.

The dry run is pointed at the live archive, so it must not write anything,
including the schema migration that adds `needs_score`.
"""
import csv
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jester.agents.critic import find_standin_scored, mark_standin_scored
from jester.cli import cmd_rescore
from jester.models import Idea, IdeaScores
from jester.scoring import compute_overall
from jester.store import get_idea, insert_idea, open_db

ROOT = Path(__file__).resolve().parents[2]


def _idea(db, n, demand, feas, comp, critic="openai/gpt-oss-120b", status="new"):
    idea = Idea(title=f"idea {n}-{demand}", problem_statement="p", proposed_solution="s",
                supporting_nuggets=[f"k{i}" for i in range(n)],
                scores=IdeaScores(demand_signal=demand, feasibility=feas, competition=comp,
                                  overall=compute_overall(demand, feas, comp)),
                critic_model=critic, status=status)
    return insert_idea(db, idea)


@pytest.fixture
def archive(tmp_path):
    path = str(tmp_path / "jester.db")
    db = open_db(path)
    ids = {
        "standin_small": _idea(db, 3, 5.0, 5.0, None),             # 2 + 3
        "standin_capped": _idea(db, 12, 10.0, 5.0, None, status="reviewed"),  # min(10, 14)
        "real_feas5": _idea(db, 3, 7.0, 5.0, None),                 # demand off-formula
        "real_comp": _idea(db, 3, 5.0, 5.0, 6.0),                   # competition checked
        "real": _idea(db, 4, 8.0, 7.0, 4.0),
        "fake_on_purpose": _idea(db, 3, 5.0, 5.0, None, critic="fake-critic"),
    }
    return path, db, ids


def _args(path, **kw):
    base = dict(db=path, config=str(ROOT / "config"), standin=True, pending=False,
                apply=False, limit=50, force=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_finder_matches_the_formula_and_nothing_else(archive):
    _path, db, ids = archive
    found = {r["id"] for r in find_standin_scored(db)}
    assert found == {ids["standin_small"], ids["standin_capped"]}


def test_the_dry_run_writes_nothing_not_even_the_migration(tmp_path):
    """Built on an archive that predates `needs_score`, as the live one does
    until S1's code runs against it."""
    path = str(tmp_path / "old.db")
    db = open_db(path)
    _idea(db, 3, 5.0, 5.0, None)
    db.execute("ALTER TABLE ideas DROP COLUMN needs_score")
    db.commit()
    db.close()
    before = Path(path).read_bytes()

    res = cmd_rescore(_args(path))

    assert res == {"ok": True, "found": 1, "applied": False}
    assert Path(path).read_bytes() == before
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(ideas)")]
    assert "needs_score" not in cols


def test_the_report_says_what_it_found(archive, capsys):
    path, _db, _ids = archive
    cmd_rescore(_args(path))
    out = capsys.readouterr().out
    assert "2 of 6 idea(s) carry the stand-in critic's scores" in out
    assert "left alone: 1 idea(s)" in out, "the near miss is reported, not marked"
    assert "nothing was written" in out


def test_apply_backs_up_records_and_clears(archive, tmp_path):
    path, db, ids = archive
    res = cmd_rescore(_args(path, apply=True))

    assert res["applied"] and res["found"] == 2
    assert Path(res["backup"]).is_file()
    with open(res["audit"], encoding="utf-8") as fh:
        audit = list(csv.DictReader(fh))
    assert {int(r["id"]) for r in audit} == {ids["standin_small"], ids["standin_capped"]}
    assert all(r["critic_model"] == "openai/gpt-oss-120b" for r in audit)
    assert {r["feasibility"] for r in audit} == {"5.0"}

    db = open_db(path)
    capped = get_idea(db, ids["standin_capped"])
    assert capped["needs_score"] == 1
    assert capped["overall"] is None and capped["demand_signal"] is None
    assert capped["critic_model"] == ""
    assert capped["status"] == "reviewed", "the operator's marking survives"
    for key in ("real_feas5", "real_comp", "real", "fake_on_purpose"):
        row = get_idea(db, ids[key])
        assert row["needs_score"] == 0 and row["overall"] is not None, key


def test_apply_twice_finds_nothing_the_second_time(archive):
    path, _db, _ids = archive
    cmd_rescore(_args(path, apply=True))
    assert cmd_rescore(_args(path, apply=True))["found"] == 0


def test_apply_refuses_without_a_verified_backup(archive, monkeypatch):
    import jester.backup as backup

    path, _db, ids = archive
    monkeypatch.setattr(backup, "backup_db", lambda *a, **k: {"ok": False, "error": "disk full"})
    with pytest.raises(SystemExit):
        cmd_rescore(_args(path, apply=True))
    row = get_idea(open_db(path), ids["standin_small"])
    assert row["needs_score"] == 0 and row["overall"] is not None


def test_marking_nothing_writes_no_audit_file(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    audit = tmp_path / "audit.csv"
    assert mark_standin_scored(db, [], audit) == 0
    assert not audit.exists()


# ---- --pending ---------------------------------------------------------------

class _Critic:
    """Groq-shaped critic transport for GroqCriticLLM."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"message": {"content": self.reply}}


#: Where the default profile's critic files its quota block (plan D9).
CRITIC_KEY = "groq/openai/gpt-oss-120b"


def _with_critic(monkeypatch, reply):
    import jester.cli as cli
    from jester.llm import GroqCriticLLM
    from jester.roles import resolve_role

    transport = _Critic(reply)
    monkeypatch.setattr(cli, "select_critic_llm", lambda cfg: GroqCriticLLM(
        client=transport, settings=resolve_role(cfg, "critic")))
    return transport


def test_pending_rescores_the_marked_ideas(archive, monkeypatch):
    path, _db, ids = archive
    cmd_rescore(_args(path, apply=True))
    transport = _with_critic(monkeypatch, json.dumps(
        {"demand_signal": 6, "feasibility": 8, "competition": 3}))

    res = cmd_rescore(_args(path, standin=False, pending=True, limit=1))
    assert res["scored"] == 1 and res["still_waiting"] == 1
    assert transport.calls == 1
    row = get_idea(open_db(path), ids["standin_small"])
    assert row["needs_score"] == 0
    assert (row["demand_signal"], row["feasibility"]) == (6.0, 8.0)
    assert row["critic_model"] == "openai/gpt-oss-120b"


@pytest.mark.parametrize("key", [CRITIC_KEY, "groq"])
def test_pending_honours_a_recorded_quota_block(archive, monkeypatch, key):
    """The critic's own model block, and an old provider-wide "groq" row."""
    from jester import llm_quota

    path, db, _ids = archive
    cmd_rescore(_args(path, apply=True))
    llm_quota.record_block(open_db(path), key, 3600, "test")
    transport = _with_critic(monkeypatch, "{}")

    res = cmd_rescore(_args(path, standin=False, pending=True))
    assert res["blocked"] and transport.calls == 0


def test_pending_records_a_block_when_the_quota_runs_out(archive, monkeypatch):
    from jester import llm_quota
    from jester.llm import GroqRateLimited

    path, _db, _ids = archive
    cmd_rescore(_args(path, apply=True))
    transport = _with_critic(monkeypatch, GroqRateLimited("429", retry_after=1800))

    res = cmd_rescore(_args(path, standin=False, pending=True))
    assert res["stopped_on_quota"] and transport.calls == 1
    db = open_db(path)
    assert llm_quota.blocked_for(db, CRITIC_KEY) > 1700
    assert llm_quota.blocked_for(db, "groq/openai/gpt-oss-20b") == 0.0, (
        "another model of the same provider is not blocked")
