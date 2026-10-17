"""Tests for the chat session engine, its clients and its roles (plan S3).

Coverage map:
- bounded ReAct loop: rounds, observations, proposals, warnings, stopping
  reasons (final answer / round budget / quota / unreachable model);
- proposal-only discipline: an abusive or unknown tool call is an
  observation the turn survives, never a mutation;
- chat.db ADD-only store: sessions/messages/tool_calls, insert-only;
- chat + specifier roles resolve through the standard rung machinery;
- the console's Test button covers the chat probe and reports that the
  specifier has no scripted probe yet.
"""
import ast
from pathlib import Path

import pytest

from jester.chat.client import (
    ChatClientError,
    FakeChatClient,
    select_chat_client,
)
from jester.chat.session import (
    HISTORY_WINDOW,
    TurnResult,
    _keys_from_trace,
)
from jester.chat.store import ChatStore, chat_db_path
from jester.chat.tools import ToolContext
from jester.config import Thresholds, load_thresholds
from jester.console.api import ConsoleAPI
from jester.models import Idea, IdeaScores, Nugget
from jester.roles import ROLES, resolve_all, resolve_role
from jester.store import insert_idea, insert_nugget, open_db

N_KEYS = [f"reddit/r/test/t1_{i}" for i in range(3)]
GROUNDED = "Relabeling " + N_KEYS[0] + " as agreed; the proposal waits for you."


def _client_cli_class(script=None):
    # subclass with rate-limit counters, isolated per test
    class Fake(FakeChatClient):
        rate_limited = 0
        fallbacks = 0

        def __init__(self):
            super().__init__(script=script)

    return Fake


@pytest.fixture()
def ctx(offline_config, tmp_path):
    conn = open_db(str(tmp_path / "jester.db"))
    for i, key in enumerate(N_KEYS):
        insert_nugget(conn, Nugget(unique_key=key, category="pain_point",
                                   extracted_insight=f"insight {i}",
                                   platform="reddit"))
    insert_idea(conn, Idea(title="idea 0", problem_statement="p",
                           supporting_nuggets=N_KEYS[:2],
                           scores=IdeaScores(demand_signal=6.0, feasibility=5.0,
                                             competition=None, overall=5.5)))
    conn.commit()
    tc = ToolContext(conn, str(offline_config), actor_kind="chat", actor="fake-chat")
    yield tc
    conn.close()


@pytest.fixture()
def chat_db(tmp_path):
    store = ChatStore(str(tmp_path / "chat.db"))
    yield chat_db_path(str(tmp_path / "jester.db")), store
    store.close()


# ---- client selection & roles ------------------------------------------------

def test_offline_profiles_resolve_the_stand_in(offline_config):
    thresholds = load_thresholds(Path(offline_config) / "thresholds.yaml")
    client = select_chat_client(thresholds)
    assert isinstance(client, FakeChatClient)


def test_chat_role_rolls_a_block_like_every_job():
    t = Thresholds(roles={"chat": {"provider": "ollama"}}).validate()
    r = resolve_role(t, "chat")
    assert (r.provider, r.model, r.timeout_s) == ("ollama", "qwen3:8b", 600.0)


def test_assistant_roles_inherit_llm_provider_without_a_block():
    t = Thresholds(llm_provider="fake").validate()
    resolved = resolve_all(t)
    assert resolved["chat"].kind == "fake"
    assert resolved["specifier"].kind == "fake"


# ---- one bounded turn ----------------------------------------------------------

def test_scripted_turn_retrieves_proposes_and_answers(ctx, chat_db):
    db_path, store = chat_db
    client = _client_cli_class([
        {"tool": "search_nuggets", "args": {"query": "insight"}},
        {"tool": "propose_edit",
         "args": {"edit_kind": "relabel_nuggets", "targets": [N_KEYS[0]],
                  "payload": {"category": "noise"},
                  "reason": "operator asked"}},
        {"text": GROUNDED},
    ])()
    runner = _runner(ctx, client, db_path, max_rounds=6)
    turn = runner.run_turn("relabel insight 0 as noise")

    assert turn.ok and turn.rounds == 3
    assert [step["tool"] for step in turn.trace] == \
        ["search_nuggets", "propose_edit"]
    assert len(turn.proposals) == 1
    assert turn.proposals[0]["tool"] == "propose_edit"
    assert turn.proposals[0]["status"] == "proposed"
    assert turn.warnings == []

    # the search-fetched key passed through the observation path
    assert N_KEYS[0] in _keys_from_trace(turn.trace)

    # nothing changed in the archive even now the turn is over
    row = ctx.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (N_KEYS[0],)
    ).fetchone()
    assert row[0] == "pain_point"

    # the transcript is complete and the tool calls are logged
    msgs = store.messages(turn.session_id)
    assert [m["role"] for m in msgs] == ["user", "chat"]
    assert msgs[0]["content"] == "relabel insight 0 as noise"
    assert msgs[1]["models_used"] == ["fake-chat"]
    calls = store.tool_calls(turn.session_id)
    assert [c["tool_name"] for c in calls] == ["search_nuggets", "propose_edit"]
    assert [c["seq"] for c in calls] == [1, 2]

    # and the proposal exists exactly once, via edits.py
    from jester import edits
    assert len(edits.pending_proposals(ctx.db)) == 1


def _runner(ctx, client, db_path, max_rounds=6, session_id=None):
    from jester.chat.session import SessionRunner

    return SessionRunner(ctx, client, chat_db=db_path,
                         session_id=session_id, max_rounds=max_rounds)


# ---- stopping reasons and honesty ---------------------------------------------

def test_final_answer_without_citations_gets_a_warning_row(ctx, chat_db):
    db_path, store = chat_db
    client = _client_cli_class([
        {"tool": "search_nuggets", "args": {"query": "insight"}},
        {"text": "I found things you might like."},
    ])()
    turn = _runner(ctx, client, db_path).run_turn("what did you find")
    assert turn.ok
    assert any("cites none" in w for w in turn.warnings)
    msgs = store.messages(turn.session_id)
    assert msgs[-1]["role"] == "chat"
    assert any(m["role"] == "system-note" for m in msgs)


def test_round_budget_stops_an_endless_tool_talker(ctx, chat_db):
    db_path, store = chat_db
    script = [{"tool": "search_nuggets", "args": {"query": "insight"}},
              {"tool": "list_ideas", "args": {}}]
    turn = _runner(ctx, _client_cli_class(script)(), db_path,
                   max_rounds=2).run_turn("keep going")
    assert turn.ok is False
    assert turn.rounds == 2
    assert any("stopped after 2 rounds" in w for w in turn.warnings)
    assert turn.reply == "(no final answer)"
    # no final chat row carries a fake answer; the (no final answer) row is
    # the honest one
    msgs = store.messages(turn.session_id)
    assert msgs[-1]["content"] == "(no final answer)"


def test_quota_exhaustion_stops_the_turn(ctx, chat_db):
    db_path, store = chat_db

    class RateLimited(_client_cli_class([])):
        rate_limited = 1

    rl = RateLimited()
    rl.rate_limited = 1
    rl.script = [{"tool": "search_nuggets", "args": {"query": "a"}}]
    turn = _runner(ctx, rl, db_path).run_turn("anything")
    assert turn.ok is False
    assert any("quota" in w for w in turn.warnings)
    assert not turn.trace  # the round never even started


def test_unreachable_model_records_a_warning(ctx, chat_db):
    db_path, store = chat_db

    class Unreachable:
        name = "broken"
        rate_limited = 0

        def chat(self, *a, **k):
            raise ChatClientError("connection refused")

    turn = _runner(ctx, Unreachable(), db_path).run_turn("hello?")
    assert turn.ok is False
    assert any("model unreachable" in w for w in turn.warnings)


def test_abusive_or_nonsense_tool_calls_are_observations(ctx, chat_db):
    db_path, store = chat_db
    client = _client_cli_class([
        {"tool": "apply_edit", "args": {"proposal_id": "P-whatever"}},
        {"tool": "search_nuggets", "args": {"query": "zzz no such", "limit": 0}},
        {"text": "I could not do that yet."},
    ])()
    turn = _runner(ctx, client, db_path).run_turn("try something sneaky")
    assert turn.ok  # the turn survived, honestly reporting failure
    assert not turn.proposals
    refused = [step for step in turn.trace if not step["result"].get("ok")]
    assert len(refused) == 2
    # the archive is completely untouched
    from jester import edits
    assert edits.pending_proposals(ctx.db) == []
    assert edits.edit_rows(ctx.db) == []


def test_empty_turn_refuses_to_run(ctx, chat_db):
    db_path, _ = chat_db
    runner = _runner(ctx, _client_cli_class([])(), db_path)
    with pytest.raises(ValueError):
        runner.run_turn("   ")


def test_session_id_follows_across_turns(ctx, chat_db):
    db_path, store = chat_db
    runner = _runner(ctx, _client_cli_class([{"text": "first"}])(), db_path)
    t1 = runner.run_turn("hello")
    runner.client = _client_cli_class([{"text": GROUNDED}])()
    t2 = runner.run_turn("again, grounded this time")
    assert t1.session_id == t2.session_id
    assert [m["content"] for m in store.messages(t2.session_id)] == \
        ["hello", "first", "again, grounded this time", GROUNDED]


# ---- the store is insert-only ---------------------------------------------------

def test_chat_store_never_issues_update_or_delete_statements():
    """AST walk over string constants: no UPDATE/DELETE statements exist.

    The module discipline is "rows are INSERTed once"; this guard survives
    refactorings the way the journal's ADD-only rule does.
    """
    path = Path(__file__).resolve().parents[1] / "jester" / "chat" / "store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    sql = [s for s in strings if s.lstrip().upper().startswith(("UPDATE ", "DELETE "))]
    assert sql == []


def test_history_window_caps_the_model_view(ctx, chat_db):
    db_path, store = chat_db
    sid = store.begin_session("greeting")
    for i in range(HISTORY_WINDOW + 6):
        store.add_message(sid, "user", f"filler {i}")
    from jester.chat.session import SessionRunner
    runner = SessionRunner(ctx, _client_cli_class([{"text": "done"}])(),
                           chat_db=db_path, session_id=sid)
    history = runner._model_history()
    assert len(history) == HISTORY_WINDOW
    assert history[0]["content"].startswith("filler")


# ---- registry rows are real, not just declared ---------------------------------

def test_chat_threshold_keys_exist_and_are_edited_at_the_console_gate():
    t = Thresholds()
    assert t.chat_max_rounds == 6 and t.chat_proposal_ttl_hours == 72.0
    assert "chat_max_rounds" in Thresholds._EDITABLE
    assert Thresholds._EDITABLE["chat_proposal_ttl_hours"] is float


# ---- the console Test button ----------------------------------------------------

@pytest.fixture()
def console_api(tmp_path):
    import shutil
    from pathlib import Path as _Path
    shipped = _Path(__file__).resolve().parents[2] / "config"
    dst = tmp_path / "config"
    shutil.copytree(shipped, dst)
    api = ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(dst))
    return api


def test_console_test_role_probes_the_chat_client(console_api):
    # shipped config resolves chat to FAKE: the stand-in answers deterministically
    res = console_api.test_role("chat")
    assert res["ok"] is True and res["kind"] == "fake"
    assert res["answered"] is True
    assert res["sample"] == "ok"


def test_console_test_role_says_specifier_has_no_probe_yet(console_api):
    res = console_api.test_role("specifier")
    assert res["ok"] is True and res["answered"] is None
    assert "no scripted probe" in res["error"]


def test_turnresult_is_a_plain_dataclass_contract():
    result = TurnResult(session_id="S-x", reply="", rounds=0, ok=False)
    assert result.proposals == [] and result.warnings == []
