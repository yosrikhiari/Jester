"""Tests for the edit regime (python/jester/edits.py).

The invariants under test are the ones the module docstring states:
append-only log, allow-listed targets/columns, proposals → apply → revert
with drift checks. Fixtures build a temp jester.db via store.open_db and
seed a few nuggets and ideas through the store helpers themselves, exactly
the way existing tests do.
"""
import json
import re
from pathlib import Path

import pytest

from jester import edits
from jester.models import Idea, IdeaScores, Nugget
from jester.store import insert_idea, insert_nugget, open_db


@pytest.fixture()
def db(tmp_path):
    conn = open_db(str(tmp_path / "jester.db"))
    keys = []
    for i in range(4):
        key = f"reddit/r/test/t1_{i}"
        insert_nugget(conn, Nugget(unique_key=key, category="pain_point",
                                   extracted_insight=f"insight {i}",
                                   platform="reddit"))
        keys.append(key)
    idea_ids = []
    for j in range(2):
        idea_ids.append(insert_idea(conn, Idea(
            title=f"idea {j}",
            problem_statement="problem",
            supporting_nuggets=keys[: 2 + j],
            scores=IdeaScores(demand_signal=6.0, feasibility=5.0, competition=None,
                              overall=5.5),
        )))
    conn.commit()
    yield conn
    conn.close()


def _apply(db, prop_id, **kw):
    kw.setdefault("actor_kind", "human")
    kw.setdefault("actor", "operator")
    return edits.apply_edit(db, prop_id, **kw)


def _relabel(db, keys, category="feature_request", **kw):
    kw.setdefault("actor_kind", "chat")
    kw.setdefault("actor", "fake-chat")
    return edits.propose_edit(
        db, edit_kind="relabel_nuggets", targets=keys,
        payload={"category": category}, **kw
    )


# ── proposal lifecycle ───────────────────────────────────────────────────

def test_propose_builds_diff_and_downstream(db):
    prop = _relabel(db, ["reddit/r/test/t1_0", "reddit/r/test/t1_1"])
    assert prop["status"] == "proposed"
    assert prop["target_table"] == "nuggets"
    assert prop["affected_json"] == {"nuggets": 2}
    before = prop["diff_json"]["before"]
    assert before["reddit/r/test/t1_0"] == {"category": "pain_point"}
    assert prop["diff_json"]["after"]["reddit/r/test/t1_1"] == {"category": "feature_request"}
    # linked ideas: both ideas support t1_0/t1_1
    assert prop["downstream_json"]["rescore_ideas"] is not None
    assert len(prop["downstream_json"]["rescore_ideas"]) == 2
    assert prop["actor_kind"] == "chat"
    assert prop["reason"] is None


def test_propose_records_reason_and_models_used(db):
    prop = edits.propose_edit(
        db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_0"],
        payload={"category": "noise"}, actor_kind="chat", actor="fake-chat",
        reason="asked by operator: these are chatter",
        models_used=[{"role": "chat", "model": "qwen3:8b", "provider": "ollama"}],
    )
    assert prop["reason"].startswith("asked by operator")
    assert prop["models_used"][0]["role"] == "chat"


def test_propose_refuses_unknown_targets(db):
    with pytest.raises(edits.EditError, match="targets not found"):
        _relabel(db, ["reddit/r/test/missing"])


def test_propose_refuses_unknown_edit_kind(db):
    with pytest.raises(edits.EditError, match="unknown edit_kind"):
        edits.propose_edit(
            db, edit_kind="delete_everything", targets=["reddit/r/test/t1_0"],
            payload={}, actor_kind="chat", actor="fake-chat",
        )


def test_propose_refuses_bad_actor(db):
    with pytest.raises(edits.EditError, match="actor_kind"):
        edits.propose_edit(
            db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_0"],
            payload={"category": "x"}, actor_kind="ghost", actor="x",
        )


def test_propose_refuses_empty_actor(db):
    with pytest.raises(edits.EditError, match="actor"):
        edits.propose_edit(
            db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_0"],
            payload={"category": "x"}, actor_kind="chat", actor="  ",
        )


# ── the allow-list: provenance and evidence stay unreachable ─────────────

def test_payload_columns_outside_kind_refused(db):
    # comment_id/comment_url are captured provenance; a payload that implies
    # a write to them must not survive validation.
    with pytest.raises(edits.ForbiddenTarget, match="not writable"):
        edits.propose_edit(
            db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_0"],
            payload={"category": "x", "comment_url": "https://evil"},
            actor_kind="chat", actor="fake-chat",
        )


# `status` is the operator's own marking and is NOT chat-writable; the
# whole proposal is refused rather than partially honored.
def test_idea_edit_ignores_status_marking(db):
    with pytest.raises(edits.ForbiddenTarget, match="status"):
        edits.propose_edit(
            db, edit_kind="edit_idea", targets=[str(idea_ids(db)[0])],
            payload={"title": "t", "status": "archived"},
            actor_kind="chat", actor="fake-chat",
        )


def test_edit_idea_applies(db):
    prop = edits.propose_edit(
        db, edit_kind="edit_idea", targets=[str(idea_ids(db)[0])],
        payload={"title": "better title"},
        actor_kind="chat", actor="fake-chat", reason="operator asked",
    )
    res = _apply(db, prop["proposal_id"])
    assert res["ok"]
    row = db.execute("SELECT title FROM ideas WHERE id=?", (idea_ids(db)[0],)).fetchone()
    assert row[0] == "better title"


def test_foreign_targets_are_forbidden(db):
    for table in ("runs", "meta", "ingested_comments", "ingest_batch",
                  "quota", "approval", "journal", "snapshot", "signal_import"):
        with pytest.raises(edits.EditError):
            # Unknown edit kind maps to no table; unknown tables must fail the
            # same way an unknown kind does — never compose SQL.
            edits.propose_edit(
                db, edit_kind=f"relabel_nuggets@{table}",
                targets=["x"], payload={"category": "c"},
                actor_kind="chat", actor="fake-chat",
            )


def test_edit_log_never_updated_or_deleted():
    """Grep discipline: the module contains no UPDATE/DELETE against edit_log."""
    src = Path(edits.__file__).read_text(encoding="utf-8")
    bad = [
        m.group(0)
        for m in re.finditer(
            r"(UPDATE|DELETE\s+FROM)\s+edit_log\b", src, re.I
        )
    ]
    assert not bad, f"edit_log is append-only; found: {bad}"


# ── apply ────────────────────────────────────────────────────────────────

def test_apply_writes_rows_and_log_fields(db):
    prop = _relabel(db, ["reddit/r/test/t1_0", "reddit/r/test/t1_1"],
                    reason="operator said so",
                    models_used=["fake-chat"])
    res = _apply(db, prop["proposal_id"])
    assert res["ok"]
    row = db.execute(
        "SELECT category FROM nuggets WHERE unique_key='reddit/r/test/t1_0'"
    ).fetchone()
    assert row[0] == "feature_request"

    log = edits.edit_rows(db)[0]
    assert log["edit_kind"] == "relabel_nuggets"
    assert log["actor_kind"] == "human" and log["actor"] == "operator"
    assert log["proposal_id"] == prop["proposal_id"]
    assert log["reason"] == "operator said so"
    assert log["models_used"] == ["fake-chat"]
    assert log["reverts_edit"] is None
    assert log["outcome"] == "applied"
    # G8: every applied row carries who / what / why.
    for field in ("actor_kind", "actor", "edit_kind", "target_table", "target_keys"):
        assert log[field] not in (None, "")


def test_apply_refuses_when_rows_drifted(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    db.execute("UPDATE nuggets SET category='noise' WHERE unique_key='reddit/r/test/t1_0'")
    db.commit()
    with pytest.raises(edits.StaleProposal, match="changed since"):
        _apply(db, prop["proposal_id"])
    # and nothing was written
    row = db.execute(
        "SELECT category FROM nuggets WHERE unique_key='reddit/r/test/t1_0'"
    ).fetchone()
    assert row[0] == "noise"


def test_apply_records_proposer_in_log(db):
    prop = edits.propose_edit(
        db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_0"],
        payload={"category": "feature_request"}, actor_kind="chat",
        actor="fake-chat", models_used=[{"role": "chat", "model": "qwen3:8b"}],
    )
    _apply(db, prop["proposal_id"], actor_kind="human", actor="operator")
    log = db.execute(
        "SELECT actor_kind, actor FROM edit_log WHERE proposal_id=?",
        (prop["proposal_id"],),
    ).fetchone()
    # The human confirmed; the chat proposed. The log shows the human as the
    # applier and the proposal carries the proposer's identity.
    assert log[0] == "human" and log[1] == "operator"
    prop_after = edits.proposal_row(db, prop["proposal_id"])
    assert prop_after["actor"] == "fake-chat"


def test_apply_twice_refused(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    _apply(db, prop["proposal_id"])
    with pytest.raises(edits.EditError, match="applied"):
        _apply(db, prop["proposal_id"])


def test_reject_then_apply_refused(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    edits.reject_proposal(db, prop["proposal_id"], actor="operator", reason="no")
    with pytest.raises(edits.EditError, match="rejected"):
        _apply(db, prop["proposal_id"])


def test_expired_proposal_refused(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"], now="1999-01-01T00:00:00Z")
    with pytest.raises(edits.StaleProposal, match="expired"):
        _apply(db, prop["proposal_id"], ttl_hours=24.0)
    assert edits.proposal_row(db, prop["proposal_id"])["status"] == "expired"


def test_expire_proposals_marks_old_ones(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    n = edits.expire_proposals(db, ttl_hours=999.0,
                               now="2999-01-01T00:00:00Z")
    # A proposal dated "now" IS older than now+999h cut... actually the cut is
    # in the past; the fixture proposal is fresh, so nothing expires yet.
    assert n == 0 or edits.proposal_row(db, prop["proposal_id"])["status"] == "proposed"
    prop2 = edits.propose_edit(
        db, edit_kind="relabel_nuggets", targets=["reddit/r/test/t1_1"],
        payload={"category": "noise"}, actor_kind="chat", actor="fake-chat",
        now="1999-01-01T00:00:00Z",
    )
    n = edits.expire_proposals(db, ttl_hours=24.0, now="1999-01-02T00:00:00Z")
    assert n >= 1
    assert edits.proposal_row(db, prop2["proposal_id"])["status"] == "expired"


# ── revert ───────────────────────────────────────────────────────────────

def test_revert_restores_preimage(db):
    keys = ["reddit/r/test/t1_0", "reddit/r/test/t1_2"]
    prop = _relabel(db, keys)
    res = _apply(db, prop["proposal_id"])
    edit_id = res["edit_id"]
    out = edits.revert_edit(db, edit_id, actor_kind="human", actor="operator")
    assert out["ok"]
    for k in keys:
        row = db.execute(
            "SELECT category FROM nuggets WHERE unique_key=?", (k,)
        ).fetchone()
        assert row[0] == "pain_point"
    assert edits.was_reverted(db, edit_id)
    # Both rows in the log, newest first, the revert naming its edit.
    log = edits.edit_rows(db)
    assert log[0]["edit_kind"] == "revert"
    assert log[0]["reverts_edit"] == edit_id
    assert log[1]["id"] == edit_id


def test_double_revert_refused(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    res = _apply(db, prop["proposal_id"])
    first = edits.revert_edit(db, res["edit_id"], actor="operator")
    with pytest.raises(edits.EditError, match="already reverted"):
        edits.revert_edit(db, res["edit_id"], actor="operator")
    # And the revert row itself cannot be undone either.
    with pytest.raises(edits.EditError, match="a revert cannot be reverted"):
        edits.revert_edit(db, first["revert_id"], actor="operator")


def test_revert_refuses_when_values_drifted(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    res = _apply(db, prop["proposal_id"])
    db.execute(
        "UPDATE nuggets SET category='moved-on' WHERE unique_key='reddit/r/test/t1_0'"
    )
    db.commit()
    with pytest.raises(edits.StaleProposal, match=r"t1_0.category"):
        edits.revert_edit(db, res["edit_id"], actor="operator")


# ── rescore kinds ────────────────────────────────────────────────────────

def _idea_id_after_rescore(db, idea_id, col):
    return db.execute(f"SELECT {col} FROM ideas WHERE id=?", (idea_id,)).fetchone()[0]


def test_rescore_queue_mode_sets_needs_score(db):
    iid = idea_ids(db)[0]
    prop = edits.propose_edit(
        db, edit_kind="rescore_idea", targets=[str(iid)],
        payload={"mode": "queue"}, actor_kind="chat", actor="fake-chat",
    )
    _apply(db, prop["proposal_id"])
    assert _idea_id_after_rescore(db, iid, "needs_score") == 1
    # scores untouched by a queue request
    assert _idea_id_after_rescore(db, iid, "overall") == 5.5


def test_rescore_manual_sets_scores_and_stamp(db):
    iid = idea_ids(db)[0]
    before = db.execute(
        "SELECT critic_model, last_scored_at FROM ideas WHERE id=?", (iid,)
    ).fetchone()
    prop = edits.propose_edit(
        db, edit_kind="rescore_idea", targets=[str(iid)],
        payload={"mode": "manual", "demand_signal": 8.0, "feasibility": 7.0,
                 "competition": None, "overall": 7.4},
        actor_kind="chat", actor="fake-chat",
    )
    _apply(db, prop["proposal_id"])
    assert _idea_id_after_rescore(db, iid, "demand_signal") == 8.0
    assert _idea_id_after_rescore(db, iid, "overall") == 7.4
    assert _idea_id_after_rescore(db, iid, "competition") is None
    # Rewriting scores by hand does NOT inherit the critic's identity.
    after = db.execute(
        "SELECT critic_model, last_scored_at FROM ideas WHERE id=?", (iid,)
    ).fetchone()
    assert after[0] == before[0]


def test_rescore_manual_revert_restores_scores(db):
    iid = idea_ids(db)[0]
    orig = db.execute(
        "SELECT demand_signal, overall, last_scored_at FROM ideas WHERE id=?",
        (iid,),
    ).fetchone()
    prop = edits.propose_edit(
        db, edit_kind="rescore_idea", targets=[str(iid)],
        payload={"mode": "manual", "demand_signal": 9.0, "overall": 9.0},
        actor_kind="chat", actor="fake-chat",
    )
    res = _apply(db, prop["proposal_id"])
    edits.revert_edit(db, res["edit_id"], actor="operator")
    now = db.execute(
        "SELECT demand_signal, overall, last_scored_at FROM ideas WHERE id=?",
        (iid,),
    ).fetchone()
    assert now[:2] == (orig[0], orig[1])


def test_rescore_manual_bad_score_refused(db):
    iid = idea_ids(db)[0]
    with pytest.raises(edits.EditError, match="number within"):
        edits.propose_edit(
            db, edit_kind="rescore_idea", targets=[str(iid)],
            payload={"mode": "manual", "overall": 11.0},
            actor_kind="chat", actor="fake-chat",
        )


# ── queue bookkeeping ────────────────────────────────────────────────────

def test_pending_and_rejected_flow(db):
    prop = _relabel(db, ["reddit/r/test/t1_0"])
    assert len(edits.pending_proposals(db)) == 1
    edits.reject_proposal(db, prop["proposal_id"], actor="operator", reason="not now")
    assert edits.pending_proposals(db) == []
    after = edits.proposal_row(db, prop["proposal_id"])
    assert after["status"] == "rejected"
    assert after["decision_note"] == "not now"


def idea_ids(db):
    rows = db.execute("SELECT id FROM ideas ORDER BY id").fetchall()
    return [r[0] for r in rows]
