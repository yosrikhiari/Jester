"""The console's chat routes: the model may ask, only the console decides.

Driven at the API level (exactly what the server's route lambdas call),
with one real `jobs.start_job` thread per turn — the polling loop the UI
uses is simulated with `jobs.get_job`, not a mock.

Pinned here:
- the gate: /api/chat refuses to start while chat_enabled is false; the
  human apply/reject/revert endpoints keep working after the flag goes off;
- the audit discipline: applies/rejects/reverts read actor_kind='human',
  actor='console-operator', and every one of them is an edit_log row;
- ids that cannot be real sessions are refused without being echoed;
- the full loop: chat turn (scripted stand-in) → pending proposal in the
  transcript → apply → revert with the archive restored → reject path.
"""
import shutil
import time
from pathlib import Path

import pytest

from jester.chat.client import FakeChatClient
from jester.config import save_thresholds
from jester.console.api import ConsoleAPI
from jester.jobs import get_job
from jester.models import Idea, IdeaScores, Nugget
from jester.store import insert_idea, insert_nugget

SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "config"
KEY0 = "reddit/r/test/t1_0"
KEY1 = "reddit/r/test/t1_1"
KEY2 = "reddit/r/test/t1_2"
GROUNDED = "Relabeling " + KEY0 + " as agreed; it waits for you."


@pytest.fixture()
def api(tmp_path):
    """A console on a temp archive + a shipped-config copy, chat enabled."""
    dst = tmp_path / "config"
    shutil.copytree(SHIPPED_CONFIG, dst)
    save_thresholds(dst / "thresholds.yaml", {"chat_enabled": True})
    a = ConsoleAPI(db_path=str(tmp_path / "jester.db"), config_dir=str(dst))
    db = a.db
    for key in (KEY0, KEY1, KEY2):
        insert_nugget(db, Nugget(unique_key=key, category="pain_point",
                                 extracted_insight=f"insight {key}",
                                 platform="reddit"))
    insert_idea(db, Idea(title="idea 0", problem_statement="p",
                         supporting_nuggets=[KEY0, KEY1],
                         scores=IdeaScores(demand_signal=6.0, feasibility=5.0,
                                           competition=None, overall=5.5)))
    db.commit()
    return a


def wait_job(api, job_id, timeout=10.0):
    """The UI's poll loop, at test speed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = get_job(api.db, job_id)
        if got and got["status"] in ("done", "error"):
            return got
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def script_client(monkeypatch, steps):
    """Route the console's client resolution to a scripted stand-in."""
    monkeypatch.setattr(
        "jester.chat.client.select_chat_client",
        lambda cfg: FakeChatClient(script=list(steps)),
    )


# ---- the gate -------------------------------------------------------------

def test_chat_start_refuses_while_disabled(tmp_path):
    dst = tmp_path / "config"
    shutil.copytree(SHIPPED_CONFIG, dst)
    a = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(dst))
    res = a.chat_start("hello")
    assert res["ok"] is False
    assert "chat_enabled" in res["error"]


def test_human_endpoints_work_after_the_flag_goes_off(api):
    """A proposal on file needs its disposition regardless of the switch."""
    save_thresholds(Path(api.config_dir) / "thresholds.yaml",
                    {"chat_enabled": False})
    assert api._cfg().thresholds.chat_enabled is False
    # a bogus id comes back as the id's own error, not the gate's
    res = api.chat_reject("P-not-real", reason="gate-proof")
    assert res["ok"] is False
    assert "unknown proposal" in res["error"]


# ---- ids ------------------------------------------------------------------

def test_bad_session_ids_are_refused_without_echo(api):
    for bad in ("../../etc", "S-123", "; DROP TABLE nuggets"):
        res = api.chat_session(bad)
        assert res["ok"] is False
        assert bad not in res["error"], "refusals must not repeat the sting"


def test_start_refuses_empty_and_overlong_but_starts_a_turn(api):
    assert api.chat_start("   ")["ok"] is False
    assert api.chat_start("x" * 4100)["ok"] is False
    assert api.chat_start("one two three")["ok"] is True  # a job row exists


# ---- one full turn as a real job ------------------------------------------

def test_turn_runs_as_a_job_and_lands_in_the_transcript(api, monkeypatch):
    script_client(monkeypatch, [
        {"tool": "search_nuggets", "args": {"query": "insight"}},
        {"tool": "propose_edit",
         "args": {"edit_kind": "relabel_nuggets", "targets": [KEY0],
                  "payload": {"category": "noise"}, "reason": "operator asked"}},
        {"text": GROUNDED},
    ])
    res = api.chat_start("relabel insight 0 to noise", session_id=None)
    assert res["ok"] is True
    job = wait_job(api, res["job_id"])
    assert job["status"] == "done"
    out = job["result"]
    assert out["ok"] is True and out["rounds"] == 3
    assert out["reply"] == GROUNDED
    assert len(out["proposals"]) == 1

    transcript = api.chat_session(out["session_id"])
    assert [m["role"] for m in transcript["messages"]] == ["user", "chat"]
    assert [c["tool_name"] for c in transcript["tool_calls"]] == \
        ["search_nuggets", "propose_edit"]
    assert len(transcript["pending"]) == 1
    proposal = transcript["pending"][0]
    assert proposal["edit_kind"] == "relabel_nuggets"
    assert proposal["actor_kind"] == "chat"

    # while it waits, the archive is untouched
    assert api.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (KEY0,)
    ).fetchone()[0] == "pain_point"


# ---- the human decisions ---------------------------------------------------

def _one_relabel_turn(api, monkeypatch, key=KEY0):
    script_client(monkeypatch, [
        {"tool": "propose_edit",
         "args": {"edit_kind": "relabel_nuggets", "targets": [key],
                  "payload": {"category": "noise"}}},
        {"text": "proposed " + key},
    ])
    started = api.chat_start("make it " + key)
    job = wait_job(api, started["job_id"])
    sid = job["result"]["session_id"]
    pending = api.chat_session(sid)["pending"]
    assert pending, "expected a pending proposal from the turn"
    return pending[0]


def test_apply_then_revert_restores_the_archive(api, monkeypatch):
    proposal = _one_relabel_turn(api, monkeypatch)
    res = api.chat_apply(proposal["proposal_id"])
    assert res["ok"] is True
    assert api.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (KEY0,)
    ).fetchone()[0] == "noise"

    # who: the human actor, on an edit_log row written at apply time — via
    # the transcript index (chat.db), not raw SQL against the archive
    sessions = api.chat_sessions()["sessions"]
    sid = sessions[0]["session_id"]
    log = api.chat_session(sid)["edits"]
    assert log[0]["actor"] == "console-operator"
    assert log[0]["actor_kind"] == "human"
    assert log[0]["edit_kind"] == "relabel_nuggets"

    undo = api.chat_revert(res["edit_id"], reason="changed my mind")
    assert undo["ok"] is True
    assert api.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (KEY0,)
    ).fetchone()[0] == "pain_point"
    log = api.chat_session(sid)["edits"]
    assert log[0]["edit_kind"] == "revert"
    assert log[0]["reverts_edit"] == res["edit_id"]
    # and the transcript still shows the applied edit as no longer pending
    assert api.chat_session(sid)["pending"] == []


def test_reject_leaves_the_archive_unchanged(api, monkeypatch):
    proposal = _one_relabel_turn(api, monkeypatch, key=KEY1)
    res = api.chat_reject(proposal["proposal_id"],
                          reason="disagreed with the model")
    assert res["ok"] is True and res["status"] == "rejected"
    assert not api.db.execute(
        "SELECT 1 FROM edit_log WHERE proposal_id=?", (proposal["proposal_id"],)
    ).fetchone()


def test_stale_proposal_refuses_to_apply(api, monkeypatch):
    """The diff says what the world WAS; the world moved on meanwhile."""
    proposal = _one_relabel_turn(api, monkeypatch, key=KEY2)
    # a human edits the same row by hand while the proposal waits
    api.db.execute("UPDATE nuggets SET category='complaint' WHERE unique_key=?",
                   (KEY2,))
    api.db.commit()
    res = api.chat_apply(proposal["proposal_id"])
    assert res["ok"] is False
    assert api.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (KEY2,)
    ).fetchone()[0] == "complaint"


# ---- transcript index ------------------------------------------------------

def test_sessions_index_lists_them(api):
    res = api.chat_sessions()
    assert res["ok"] is True
    assert isinstance(res["sessions"], list)
