"""Tests for the chat tool surface (python/jester/chat/).

Covers the boundary at both ends: the strict-subset schema validator that
refuses malformed model arguments, and the tool registry whose write half
can ONLY mint proposals. The proposal → human apply → revert flows through
thresholds.yaml / sources.yaml are driven here end to end on a temp config
copy, exactly the way the console will drive them in S4.
"""
import ast
from pathlib import Path

import pytest

from jester import edits
from jester.chat import schemas
from jester.chat.schemas import realize
from jester.chat.tools import TOOLS, ToolContext, manifest, run_tool
from jester.config import load_thresholds
from jester.models import Idea, IdeaScores, Nugget
from jester.sources import Source, load_sources, save_sources
from jester.store import insert_idea, insert_nugget, open_db

N_KEYS = [f"reddit/r/test/t1_{i}" for i in range(4)]


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
    yield ToolContext(conn, str(offline_config), actor_kind="chat",
                      actor="fake-chat", models_used=["fake-chat"])
    conn.close()


@pytest.fixture()
def two_sources(ctx):
    """Two sources in the config copy: catalog (kept) + hn (to be parked)."""
    save_sources(Path(ctx.config_dir) / "sources.yaml", [
        Source(name="catalog", platform="catalog", url="https://x/catalog",
               kind="catalog"),
        Source(name="hn", platform="hackernews", url="https://news.ycombinator.com",
               kind="hackernews"),
    ])
    return ctx


# ── schema validator ─────────────────────────────────────────────────────

def test_realize_refuses_unsupported_schema_keys():
    with pytest.raises(schemas.SchemaError, match="unsupported schema keys"):
        schemas.realize({"type": "string", "pattern": "^a.*"})
    with pytest.raises(schemas.SchemaError, match="needs a known 'type'"):
        schemas.realize({"properties": {}})


def test_realize_refuses_unsupported_type_member():
    # a non-list bad type is refused as an unknown type; type LISTS cover
    # scalars-only members (see realize/_keys_for)
    with pytest.raises(schemas.SchemaError, match="needs a known 'type'"):
        schemas.realize({"type": "string|number"})


def test_validator_rejects_missing_required_and_names_the_path():
    schema = realize({"type": "object", "required": ["args"],
                      "properties": {"args": {
                          "type": "object", "required": ["query"],
                          "properties": {"query": {"type": "string"}},
                      }}})
    with pytest.raises(schemas.ToolArgError,
                       match="missing required argument 'query'"):
        schemas.validate({"args": {}}, schema)


def test_validator_rejects_unknown_property_when_strict():
    schema = realize({"type": "object",
                      "properties": {"a": {"type": "string"}},
                      "additionalProperties": False})
    with pytest.raises(schemas.ToolArgError, match="unknown argument"):
        schemas.validate({"a": "x", "b": 1}, schema)


def test_validator_allows_extra_keys_when_not_strict():
    schema = realize({"type": "object",
                      "properties": {"a": {"type": "string"}},
                      "additionalProperties": True})
    schemas.validate({"a": "x", "anything": [1, 2]}, schema)


def test_validator_rejects_type_and_range_and_enum_violations():
    schema = realize({"type": "object", "properties": {
        "i": {"type": "integer", "minimum": 1, "maximum": 5},
        "f": {"type": "number", "minimum": -1.0},
        "e": {"type": "string", "enum": ["a", "b"]},
        "arr": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    }, "additionalProperties": False})
    with pytest.raises(schemas.ToolArgError, match="expected integer"):
        schemas.validate({"i": 1.5}, schema)
    with pytest.raises(schemas.ToolArgError, match="above maximum"):
        schemas.validate({"i": 6}, schema)
    with pytest.raises(schemas.ToolArgError, match="below minimum"):
        schemas.validate({"f": -2.0}, schema)
    with pytest.raises(schemas.ToolArgError, match="not in enum"):
        schemas.validate({"e": "z"}, schema)
    with pytest.raises(schemas.ToolArgError, match="more than 2"):
        schemas.validate({"arr": ["a", "b", "c"]}, schema)
    with pytest.raises(schemas.ToolArgError, match="expected string"):
        schemas.validate({"arr": [1]}, schema)


def test_validator_nullable_lists_allow_none():
    schema = realize({"type": "object",
                      "properties": {"v": {"type": ["number", "null"]}},
                      "additionalProperties": False})
    schemas.validate({"v": None}, schema)
    schemas.validate({"v": 3.5}, schema)
    with pytest.raises(schemas.ToolArgError):
        schemas.validate({"v": "3.5"}, schema)


# ── registry integrity ───────────────────────────────────────────────────

def test_registry_is_complete_and_strict():
    names = list(TOOLS)
    assert names == [
        "list_sources", "search_nuggets", "get_nugget", "get_idea",
        "list_ideas", "list_proposals", "get_proposal", "list_edit_log",
        "propose_edit", "propose_threshold", "propose_source_toggle",
        "propose_signal_rule",
    ]
    for tool in TOOLS.values():
        assert tool.schema["type"] == "object"
        assert tool.description.strip(), tool.name
        assert tool.kind in ("read", "write"), tool.name
        # every write tool is a proposal minter — the model never mutates
        if tool.kind == "write":
            assert tool.name.startswith("propose_"), tool.name


def test_manifest_lists_expectations():
    m = manifest()
    assert [t["name"] for t in m] == list(TOOLS)
    for t in m:
        assert set(t) == {"name", "kind", "description", "schema"}


def test_chat_package_never_imports_journal_or_signals():
    """G3/G4 structural guard: imports, not mentions — docstrings may speak."""
    checked = list(Path(schemas.__file__).parent.glob("*.py"))
    checked.append(Path(edits.__file__))
    for path in checked:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        for mod in mods:
            parts = mod.split(".")
            top = parts[1] if mod.startswith("jester.") else parts[0]
            assert top not in {"journal", "signals"}, f"{path.name}: imports {mod}"


# ── run_tool boundary ────────────────────────────────────────────────────

def test_run_tool_refuses_unknown_and_non_object_args(ctx):
    assert run_tool("delete_everything", {}, ctx)["ok"] is False
    assert run_tool("search_nuggets", ["not", "a", "dict"], ctx)["ok"] is False


def test_run_tool_reports_validation_errors_as_observations(ctx):
    # 'limit' above the manifest maximum is refused before any handler runs
    out = run_tool("search_nuggets", {"query": "x", "limit": 99}, ctx)
    assert out["ok"] is False
    assert "above maximum" in out["error"]
    # an unknown edit_kind enum value never reaches edits.py
    out = run_tool("propose_edit",
                   {"edit_kind": "drop_table", "targets": ["x"], "payload": {}},
                   ctx)
    assert out["ok"] is False and "not in enum" in out["error"]


# ── read tools ground in the real archive ────────────────────────────────

def test_search_nuggets_is_literal_not_a_pattern(ctx):
    insert_nugget(ctx.db, Nugget(unique_key="reddit/r/test/t_wild",
                                 category="pain_point",
                                 extracted_insight="ins%ight wildcard",
                                 platform="reddit"))
    ctx.db.commit()
    # the model's % must match a literal percent, not act as a wildcard
    out = run_tool("search_nuggets", {"query": "ins%ight"}, ctx)
    assert out["count"] == 1
    assert out["hits"][0]["unique_key"] == "reddit/r/test/t_wild"


def test_search_nuggets_filters_and_orders(ctx):
    insert_nugget(ctx.db, Nugget(unique_key="hn/x/y", category="feature_request",
                                 extracted_insight="insight hn", platform="hackernews"))
    ctx.db.commit()
    out = run_tool("search_nuggets", {"query": "insight", "platform": "hackernews"}, ctx)
    assert out["count"] == 1 and out["hits"][0]["platform"] == "hackernews"
    out = run_tool("search_nuggets", {"query": "insight", "category": "pain_point"}, ctx)
    assert out["count"] == 4
    assert all(h["platform"] == "reddit" for h in out["hits"])


def test_get_nugget_and_get_idea(ctx):
    out = run_tool("get_nugget", {"unique_key": N_KEYS[0]}, ctx)
    assert out["ok"] and out["nugget"]["category"] == "pain_point"
    assert run_tool("get_nugget", {"unique_key": "nope"}, ctx)["ok"] is False
    out = run_tool("get_idea", {"idea_id": 1}, ctx)
    assert out["ok"] and out["idea"]["supporting_nuggets"] == N_KEYS[:2]


def test_list_ideas_log_and_proposals_reads(ctx):
    out = run_tool("list_ideas", {}, ctx)
    assert out["count"] == 1 and out["ideas"][0]["overall"] == 5.5
    assert run_tool("list_edit_log", {}, ctx) == {"ok": True, "edits": [], "count": 0}
    # after a proposal exists, the reads show it
    run_tool("propose_edit", {"edit_kind": "relabel_nuggets",
                              "targets": [N_KEYS[0]], "payload": {"category": "noise"}},
             ctx)
    out = run_tool("list_proposals", {}, ctx)
    assert out["count"] == 1
    pid = out["proposals"][0]["proposal_id"]
    got = run_tool("get_proposal", {"proposal_id": pid}, ctx)
    assert got["proposal"]["edit_kind"] == "relabel_nuggets"
    assert got["proposal"]["diff"][0]["key"] == N_KEYS[0]


# ── write tools mint proposals; humans apply ─────────────────────────────

def test_propose_edit_tool_writes_nothing_by_itself(ctx):
    out = run_tool("propose_edit", {
        "edit_kind": "relabel_nuggets",
        "targets": [N_KEYS[0], N_KEYS[1]],
        "payload": {"category": "feature_request"},
        "reason": "operator asked",
    }, ctx)
    assert out["ok"] is True
    prop = out["proposal"]
    assert prop["status"] == "proposed"
    assert prop["actor"] == "fake-chat" and prop["actor_kind"] == "chat"
    assert prop["models_used"] == ["fake-chat"]
    # nothing moved in the archive
    row = ctx.db.execute(
        "SELECT category FROM nuggets WHERE unique_key=?", (N_KEYS[0],)
    ).fetchone()
    assert row[0] == "pain_point"
    # edits-level validation surfaces as an observation, not a crash
    out = run_tool("propose_edit", {
        "edit_kind": "relabel_nuggets", "targets": [N_KEYS[2]],
        "payload": {"comment_url": "https://evil"},
    }, ctx)
    assert out["ok"] is False and "not writable" in out["error"]


def rescore_queue_via_tool(ctx):
    return run_tool("propose_edit", {
        "edit_kind": "rescore_idea",
        "targets": ["1"],
        "payload": {"mode": "queue"},
    }, ctx)


def test_rescore_queue_proposal_through_tool(ctx):
    out = rescore_queue_via_tool(ctx)
    assert out["ok"] is True
    prop = out["proposal"]
    assert prop["edit_kind"] == "rescore_idea"
    assert prop["diff_json"]["after"]["1"] == {"needs_score": 1}
    # downstream lists linked ideas for NUGGET edits; an idea edit carries none
    assert prop["downstream_json"] == {"rescore_ideas": []}


def test_threshold_proposal_apply_and_revert_end_to_end(ctx):
    """The exact console flow of S4: chat proposes, human applies, undo works."""
    path = Path(ctx.config_dir) / "thresholds.yaml"
    initial = load_thresholds(path).max_comments_per_thread
    new_value = 250 if initial != 250 else 251

    out = run_tool("propose_threshold",
                   {"key": "max_comments_per_thread", "value": new_value,
                    "reason": "operator wants a heavier first sweep"}, ctx)
    assert out["ok"] is True
    pid = out["proposal"]["proposal_id"]
    diff = out["proposal"]["diff_json"]
    assert diff["after"]["max_comments_per_thread"] == new_value
    assert diff["before"]["max_comments_per_thread"] == initial

    res = edits.apply_edit(ctx.db, pid, actor_kind="human", actor="operator",
                           config_dir=ctx.config_dir)
    assert res["ok"] is True
    assert load_thresholds(path).max_comments_per_thread == new_value

    log = edits.edit_rows(ctx.db)
    assert log[0]["edit_kind"] == "set_threshold"
    assert log[0]["actor"] == "operator"          # who pressed the button
    assert log[0]["models_used"] == ["fake-chat"]  # whose proposal it was
    assert log[0]["proposal_id"] == pid

    out = edits.revert_edit(ctx.db, res["edit_id"], actor_kind="human",
                            actor="operator", config_dir=ctx.config_dir)
    assert out["ok"] is True
    assert load_thresholds(path).max_comments_per_thread == initial
    assert edits.was_reverted(ctx.db, res["edit_id"])


def test_threshold_proposal_refuses_unknown_key_and_out_of_range(ctx):
    out = run_tool("propose_threshold", {"key": "not_a_threshold", "value": 1}, ctx)
    assert out["ok"] is False and "not in enum" in out["error"]
    out = run_tool("propose_threshold", {"key": "max_comments_per_thread", "value": 0}, ctx)
    assert out["ok"] is False  # _RANGES range violation, surfaced as an observation


def test_threshold_drift_refused_at_apply(ctx):
    """A hand edit during review voids the proposal instead of stomping it."""
    path = Path(ctx.config_dir) / "thresholds.yaml"
    out = run_tool("propose_threshold", {"key": "max_comments_per_thread", "value": 250}, ctx)
    pid = out["proposal"]["proposal_id"]
    # the operator hand-edits the file while the proposal sits in review
    from jester.config import save_thresholds
    save_thresholds(path, {"max_comments_per_thread": 999})
    with pytest.raises(edits.StaleProposal):
        edits.apply_edit(ctx.db, pid, actor_kind="human", actor="operator",
                         config_dir=ctx.config_dir)
    # the refusal changed nothing: the hand edit stands, the proposal waits
    assert load_thresholds(path).max_comments_per_thread == 999
    assert edits.proposal_row(ctx.db, pid)["status"] == "proposed"
    save_thresholds(path, {"max_comments_per_thread": 500})


def test_source_toggle_apply_and_revert_end_to_end(two_sources):
    path = Path(two_sources.config_dir) / "sources.yaml"
    assert all(s.enabled for s in load_sources(path))
    out = run_tool("propose_source_toggle",
                   {"source": "hn", "enabled": False, "reason": "dead API"},
                   two_sources)
    assert out["ok"] is True
    pid = out["proposal"]["proposal_id"]

    res = edits.apply_edit(two_sources.db, pid, actor_kind="human",
                           actor="operator", config_dir=two_sources.config_dir)
    assert res["ok"] is True
    after = {s.name: s.enabled for s in load_sources(path)}
    assert after["hn"] is False and after["catalog"] is True

    out = edits.revert_edit(two_sources.db, res["edit_id"], actor_kind="human",
                            actor="operator", config_dir=two_sources.config_dir)
    assert out["ok"] is True
    assert {s.name: s.enabled for s in load_sources(path)}["hn"] is True


def test_source_toggle_refuses_noop_and_missing(two_sources):
    out = run_tool("propose_source_toggle", {"source": "catalog", "enabled": True},
                   two_sources)
    assert out["ok"] is False and "already enabled" in out["error"]
    out = run_tool("propose_source_toggle", {"source": "ghost", "enabled": False},
                   two_sources)
    assert out["ok"] is False and "not in sources.yaml" in out["error"]


def test_signal_rule_staging_never_touches_acceptance_path(ctx):
    out = run_tool("propose_signal_rule", {
        "name": "design tool wishes", "role": "buyer_required", "weight": 0.9,
        "phrases": ["we'd pay for", "would  pay  for", "We'd Pay For"],
        "justification": "seen in 20+ pricing complaints (run r42)",
    }, ctx)
    assert out["ok"] is True
    rule = out["rule"]
    assert rule["status"] == "proposed"
    payload = rule["payload"]
    assert payload["name"] == "design tool wishes"
    # phrases stripped + case-deduplicated, order kept
    assert payload["phrases"] == ["we'd pay for", "would  pay  for"]

    out = run_tool("propose_signal_rule", {
        "name": "bad negative", "role": "negative", "weight": 0.5,
        "phrases": ["meh"],
    }, ctx)
    assert out["ok"] is False and "weight < 0" in out["error"]

    rows = edits.signal_rule_rows(ctx.db)
    assert len(rows) == 1 and rows[0]["payload"]["role"] == "buyer_required"
