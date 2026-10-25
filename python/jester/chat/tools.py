"""The chat's tool surface: code-owned descriptions, typed args, proposal-only writes.

Everything here is deterministic and boring by construction:

- The DESCRIPTIONS and SCHEMAS in the registry are source constants — a model
  can influence their ARGUMENTS, never their text (the tool-poisoning defence;
  see the Invariant MCP tool-poisoning write-up in the Phase 0 ledger).
- Read tools ground the model in the real archive: the SQLite read paths the
  rest of Jester already uses. No file handles, no SQL, no store internals
  cross the boundary — only JSON-shaped arguments and results.
- Write tools can ONLY mint proposals through ``jester.edits``. Applying,
  rejecting and reverting are human actions taken in the console, which call
  those edits functions directly with actor_kind='human'. The model has no
  tool that mutates the world.

Grounding note: v1 search is SQLite text search (substring over the extracted
insight and raw text, newest first). Semantic/vector search arrives with the
session's embedder wiring and falls back to this same tool — see session.py.

The registry never imports jester.journal or jester.signals (a test parses
the package's imports for that): journal gates and the deterministic signal
acceptance path are structurally unreachable from anything a model can call.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

from jester import edits
from jester import sources as sources_mod
from jester.chat import schemas
from jester.chat.schemas import ToolArgError, realize
from jester.config import ConfigError, Thresholds

SQL_EDIT_KINDS = ("relabel_nuggets", "edit_idea", "rescore_idea")

#: Scalar _EDITABLE keys a chat may propose (mapping keys are operator work).
THRESHOLD_KEYS = sorted(
    k for k, caster in Thresholds._EDITABLE.items() if caster is not dict
)

_SIGNAL_RULE_ROLES = edits.SIGNAL_RULE_ROLES


class ToolContext:
    """What a tool call may touch, and who is driving.

    `db` is the ALREADY-OPEN archive connection (the console's or a CLI's;
    row factory is normalized here because CLI-opened connections ship
    without one). `config_dir` is where audited config writes land.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        config_dir: str,
        *,
        actor_kind: str = "chat",
        actor: str = "chat-model",
        models_used: Any = None,
    ) -> None:
        db.row_factory = sqlite3.Row
        self.db = db
        self.config_dir = str(Path(config_dir))
        self.actor_kind = actor_kind
        self.actor = actor
        self.models_used = models_used


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    kind: str  # "read" | "write"
    handler: Callable[[Dict[str, Any], ToolContext], Dict[str, Any]]


# ── read handlers ────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    for jk in ("supporting_nuggets", "source_platforms"):
        if isinstance(d.get(jk), str):
            try:
                d[jk] = json.loads(d[jk])
            except (TypeError, ValueError):
                pass
    return d


def _tool_list_sources(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    srcs = sources_mod.load_sources(Path(ctx.config_dir) / "sources.yaml")
    return {
        "ok": True,
        "count": len(srcs),
        "sources": [
            {
                "name": s.name,
                "platform": s.platform,
                "kind": s.kind,
                "url": s.url,
                "enabled": bool(s.enabled),
                "notes": s.notes,
            }
            for s in srcs
        ],
    }


def _tool_search_nuggets(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Substring search over insight + raw text, newest first.

    Wildcards are escaped, so the model's text is literally-match, never a
    pattern it composed. Deterministic ordering ties break on unique_key.
    """
    query = args["query"]
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = (
        "SELECT unique_key, extracted_insight, category, platform, created_at "
        "FROM nuggets WHERE (extracted_insight LIKE ? ESCAPE '\\' "
        "OR raw_text LIKE ? ESCAPE '\\')"
    )
    params: List[Any] = [pattern, pattern]
    if args.get("platform"):
        sql += " AND platform = ?"
        params.append(args["platform"])
    if args.get("category"):
        sql += " AND category = ?"
        params.append(args["category"])
    sql += " ORDER BY created_at DESC, unique_key DESC LIMIT ?"
    params.append(args.get("limit", 10))
    rows = ctx.db.execute(sql, params).fetchall()
    hits = [_row_to_dict(r) for r in rows]
    return {"ok": True, "count": len(hits), "hits": hits,
            "note": "text search; keys are unique_key values"}


def _tool_get_nugget(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    row = ctx.db.execute(
        "SELECT * FROM nuggets WHERE unique_key=?", (args["unique_key"],)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": f"no nugget {args['unique_key']!r}"}
    return {"ok": True, "nugget": _row_to_dict(row)}


def _tool_get_idea(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    from jester.store import get_idea

    row = get_idea(ctx.db, int(args["idea_id"]))
    if row is None:
        return {"ok": False, "error": f"no idea {args['idea_id']}"}
    return {"ok": True, "idea": _row_to_dict(row)}


def _tool_list_ideas(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    limit = args.get("limit", 20)
    rows = ctx.db.execute(
        "SELECT id, title, overall, status, needs_score, created_at "
        "FROM ideas ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    ideas = [_row_to_dict(r) for r in rows]
    return {"ok": True, "count": len(ideas), "ideas": ideas}


def _tool_list_proposals(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    props = edits.pending_proposals(ctx.db)
    return {"ok": True, "proposals": props, "count": len(props)}


def _tool_get_proposal(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    prop = edits.proposal_row(ctx.db, args["proposal_id"])
    if prop is None:
        return {"ok": False, "error": f"no proposal {args['proposal_id']!r}"}
    return {"ok": True, "proposal": prop}


def _tool_list_edit_log(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    rows = edits.edit_rows(ctx.db, limit=args.get("limit", 50))
    return {"ok": True, "edits": rows, "count": len(rows)}


# ── write handlers: proposal-only ────────────────────────────────────────

def _tool_propose_edit(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    prop = edits.propose_edit(
        ctx.db,
        edit_kind=args["edit_kind"],
        targets=args["targets"],
        payload=args.get("payload") or {},
        actor_kind=ctx.actor_kind,
        actor=ctx.actor,
        reason=args.get("reason", ""),
        models_used=ctx.models_used,
    )
    return {"ok": True, "proposal": prop,
            "note": "nothing applied until the operator confirms"}


def _tool_propose_threshold(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    prop = edits.propose_threshold(
        ctx.db,
        key=args["key"],
        value=args.get("value"),
        config_dir=ctx.config_dir,
        actor_kind=ctx.actor_kind,
        actor=ctx.actor,
        reason=args.get("reason", ""),
        models_used=ctx.models_used,
    )
    return {"ok": True, "proposal": prop,
            "note": "nothing applied until the operator confirms"}


def _tool_propose_source_toggle(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    prop = edits.propose_source_toggle(
        ctx.db,
        source=args["source"],
        enabled=args["enabled"],
        config_dir=ctx.config_dir,
        actor_kind=ctx.actor_kind,
        actor=ctx.actor,
        reason=args.get("reason", ""),
        models_used=ctx.models_used,
    )
    return {"ok": True, "proposal": prop,
            "note": "nothing applied until the operator confirms"}


def _tool_propose_signal_rule(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    rule = edits.propose_signal_rule(
        ctx.db,
        family={
            "name": args["name"],
            "role": args["role"],
            "weight": args["weight"],
            "phrases": args["phrases"],
        },
        justification=args.get("justification", ""),
        actor_kind=ctx.actor_kind,
        actor=ctx.actor,
        models_used=ctx.models_used,
    )
    return {"ok": True, "rule": rule,
            "note": "staged for human promotion; the acceptance path is untouched"}


# ── the registry ─────────────────────────────────────────────────────────

TOOLS: Dict[str, Tool] = {}
for _name, _kind, _desc, _schema, _handler in [
    ("list_sources", "read",
     "List configured scraping sources (name, platform, kind, url, enabled).",
     {"type": "object", "properties": {}, "additionalProperties": False},
     _tool_list_sources),
    ("search_nuggets", "read",
     "Text-search nuggets (substring match over the extracted insight and "
     "raw text, newest first). Returns unique_key values usable as edit "
     "targets.",
     {"type": "object", "required": ["query"],
      "properties": {
          "query": {"type": "string", "minLength": 1, "maxLength": 400},
          "limit": {"type": "integer", "minimum": 1, "maximum": 20},
          "platform": {"type": "string", "minLength": 1, "maxLength": 40},
          "category": {"type": "string", "minLength": 1, "maxLength": 40},
      }, "additionalProperties": False},
     _tool_search_nuggets),
    ("get_nugget", "read",
     "Fetch one nugget's full row by unique_key.",
     {"type": "object", "required": ["unique_key"],
      "properties": {"unique_key": {"type": "string", "minLength": 1,
                                    "maxLength": 200}},
      "additionalProperties": False},
     _tool_get_nugget),
    ("get_idea", "read",
     "Fetch one idea's full row by numeric id (includes its supporting "
     "nugget keys).",
     {"type": "object", "required": ["idea_id"],
      "properties": {"idea_id": {"type": "integer", "minimum": 1}},
      "additionalProperties": False},
     _tool_get_idea),
    ("list_ideas", "read",
     "List the newest ideas with id, title, overall score, status and the "
     "needs_score flag.",
     {"type": "object",
      "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
      "additionalProperties": False},
     _tool_list_ideas),
    ("list_proposals", "read",
     "List pending edit proposals (diff, affected counts, downstream "
     "rescore candidates).",
     {"type": "object", "properties": {}, "additionalProperties": False},
     _tool_list_proposals),
    ("get_proposal", "read",
     "Fetch one proposal in full by proposal_id, including its diff.",
     {"type": "object", "required": ["proposal_id"],
      "properties": {"proposal_id": {"type": "string", "minLength": 1,
                                     "maxLength": 64}},
      "additionalProperties": False},
     _tool_get_proposal),
    ("list_edit_log", "read",
     "List the newest edit_log rows — what changed in this archive, who "
     "made it and why.",
     {"type": "object",
      "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
      "additionalProperties": False},
     _tool_list_edit_log),
    ("propose_edit", "write",
     "Request a change to archive rows: relabel nuggets' category, edit an "
     "idea's title/problem/solution, or rescore an idea (mode 'queue' marks "
     "it for the critic, mode 'manual' proposes explicit scores). NOTHING "
     "is applied: the returned proposal carries the diff and awaits human "
     "confirmation. Targets are unique_key strings for nugget kinds and "
     "id strings for idea kinds.",
     {"type": "object",
      "required": ["edit_kind", "targets", "payload"],
      "properties": {
          "edit_kind": {"type": "string", "enum": list(SQL_EDIT_KINDS)},
          "targets": {
              "type": "array", "items": {"type": "string", "minLength": 1,
                                         "maxLength": 200},
              "minItems": 1, "maxItems": 200,
          },
          "payload": {"type": "object", "additionalProperties": True},
          "reason": {"type": "string", "minLength": 1, "maxLength": 500},
      }, "additionalProperties": False},
     _tool_propose_edit),
    ("propose_threshold", "write",
     "Propose changing one thresholds.yaml setting (scalar keys only; "
     "ranges enforced). Nothing is applied until the operator confirms.",
     {"type": "object", "required": ["key", "value"],
      "properties": {
          "key": {"type": "string", "enum": THRESHOLD_KEYS},
          "value": {"type": ["string", "number", "integer", "boolean", "null"]},
          "reason": {"type": "string", "minLength": 1, "maxLength": 500},
      }, "additionalProperties": False},
     _tool_propose_threshold),
    ("propose_source_toggle", "write",
     "Propose enabling or disabling one EXISTING source from list_sources "
     "by exact name. Adding sources is operator work with spec review "
     "attached and has no tool. Nothing is applied until confirmation.",
     {"type": "object", "required": ["source", "enabled"],
      "properties": {
          "source": {"type": "string", "minLength": 1, "maxLength": 200},
          "enabled": {"type": "boolean"},
          "reason": {"type": "string", "minLength": 1, "maxLength": 500},
      }, "additionalProperties": False},
     _tool_propose_source_toggle),
    ("propose_signal_rule", "write",
     "Stage a drafted signal family (name, role, weight, phrases) for "
     "HUMAN promotion into signal_rules.yaml. The deterministic acceptance "
     "path is never touched by this tool.",
     {"type": "object", "required": ["name", "role", "weight", "phrases"],
      "properties": {
          "name": {"type": "string", "minLength": 1, "maxLength": 80},
          "role": {"type": "string", "enum": list(_SIGNAL_RULE_ROLES)},
          "weight": {"type": "number", "minimum": -1.0, "maximum": 1.0},
          "phrases": {
              "type": "array",
              "items": {"type": "string", "minLength": 1, "maxLength": 120},
              "minItems": 1, "maxItems": 30,
          },
          "justification": {"type": "string", "minLength": 1, "maxLength": 600},
      }, "additionalProperties": False},
     _tool_propose_signal_rule),
]:
    TOOLS[_name] = Tool(_name, _desc, realize(_schema), _kind, _handler)

del _name, _kind, _desc, _schema, _handler


def manifest() -> List[Dict[str, Any]]:
    """Tool manifest for the system prompt and the console — stable order."""
    return [
        {
            "name": t.name,
            "kind": t.kind,
            "description": t.description,
            "schema": t.schema,
        }
        for t in TOOLS.values()
    ]


def run_tool(name: str, args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Validate args at the boundary and run one tool.

    Validation failures and known domain errors return {"ok": False,
    "error": ...} as an observation the model can act on; anything else
    propagates to the caller (a crash is a real bug, not model input).
    """
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": f"{name}: arguments must be an object"}
    try:
        schemas.validate(args, tool.schema)
    except ToolArgError as exc:
        return {"ok": False, "error": f"{name}: {exc}"}
    try:
        return tool.handler(args, ctx)
    except (edits.EditError, ConfigError, OSError) as exc:
        return {"ok": False, "error": f"{name}: {exc}"}
