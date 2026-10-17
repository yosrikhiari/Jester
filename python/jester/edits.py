"""The edit regime: one append-only log, typed proposals, revert-by-new-row.

Every change a chat session, an autonomous agent or the scheduler makes to
the archive goes through this module — never a direct UPDATE from those
callers. The shape is deliberately boring:

    propose_edit -> apply_edit -> (edit_log row) -> [ revert_edit -> new row ]

Three invariants, all guarded in tests/test_edits.py:

1. `edit_log` is append-only. No UPDATE and no DELETE against it exists in
   this file (a test greps for that). A revert is a NEW row whose
   `reverts_edit` column names the edit it undoes; the original row is
   never touched, so "was this edit reverted?" is a query (`was_reverted`),
   not a state.

2. Writes are bounded by an allow-list. SQL appliers only ever touch
   `nuggets.category` and the declared per-kind subset of `ideas` columns
   for the opened archive. Provenance columns (comment_id, comment_url,
   source fields), evidence tables (`ingested_comments`, `ingest_batch`),
   run records (`runs`, `meta`) and everything outside the target table are
   structurally unreachable: a payload key outside its kind's column set is
   refused at proposal time, before any SQL exists. Config edits are the
   second arm of the same allow-list: they target `thresholds.yaml` /
   `sources.yaml` and route through the audited config writers
   (`config.save_thresholds` / `sources.save_sources`), so the registry
   constraints and the atomic-write guarantees those modules already own
   keep applying.

3. Applies are drift-checked. apply_edit recomputes the pre-image from the
   live rows and refuses to proceed when the proposal's description no
   longer matches the world, and revert_edit refuses when the post-image no
   longer holds. Nothing in this regime can silently stomp a value that
   changed underneath it.

Canonical JSON shapes (the tests pin these):

    diff_json    (proposals)  {"before": {key: {col: val, ...}}, "after": {...}}
    before_json  (edit_log)   the same "before" dict
    after_json   (edit_log)   the same "after" dict
    downstream_json           {"rescore_ideas": [idea_id, ...]}  — informational
                              (what the caller may want to do next), never
                              applied as a side effect. Chaining "relabel and
                              re-score the linked ideas" is TWO proposals,
                              each with its own diff, confirm and undo.

Config targets (thresholds.yaml / sources.yaml) share the proposal and
edit_log rows with a FLAT image shape — target_keys = [key-or-source-name],
images {"<key>": <value>} — because a config file has no row keys.

`edit_proposals` is the one table here whose rows receive status UPDATEs
(proposed → applied | rejected | expired) — working state, like
`ingest_batch`. The audit record of a proposal's life is the edit_log row
created on apply. Legacy databases predating this module get the tables via
the idempotent `ensure_edits_schema` executed at every entry point, because
the console opens its archive with `migrate=False` and would otherwise never
see BASE_SCHEMA's CREATE statements.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from jester.config import (
    ConfigError,
    Thresholds,
    coerce_thresholds_patch,
    load_thresholds,
    save_thresholds,
)
from jester.sources import load_sources, save_sources

EDITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS edit_log (
    id            TEXT PRIMARY KEY,
    created_utc   TEXT NOT NULL,
    actor_kind    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    tool_name     TEXT,
    edit_kind     TEXT NOT NULL,
    target_table  TEXT NOT NULL,
    target_keys   TEXT NOT NULL,
    before_json   TEXT NOT NULL,
    after_json    TEXT NOT NULL,
    reason        TEXT,
    models_used   TEXT,
    proposal_id   TEXT,
    reverts_edit  TEXT,
    outcome       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edit_proposals (
    proposal_id     TEXT PRIMARY KEY,
    created_utc     TEXT NOT NULL,
    actor_kind      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    edit_kind       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    target_keys     TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    diff_json       TEXT NOT NULL,
    affected_json   TEXT NOT NULL,
    downstream_json TEXT NOT NULL,
    reason          TEXT,
    models_used     TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed',
    decided_utc     TEXT,
    decision_note   TEXT,
    edit_id         TEXT
);
CREATE TABLE IF NOT EXISTS signal_rule_proposals (
    id            TEXT PRIMARY KEY,
    created_utc   TEXT NOT NULL,
    actor_kind    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    justification TEXT,
    status        TEXT NOT NULL DEFAULT 'proposed',
    decided_utc   TEXT,
    models_used   TEXT
);
"""

ACTOR_KINDS = ("chat", "human", "agent", "scheduler")
STATUS_VALUES = ("proposed", "applied", "rejected", "expired")

#: Config-file targets: proposals whose target_table is one of these are
#: applied/reverted through the audited config mutators
#: (config.save_thresholds, sources.save_sources) instead of row UPDATEs.
#: coerce_thresholds_patch refuses unknown or out-of-range keys, so a
#: proposal can never introduce a key the §4.3.1 registry does not describe.
CONFIG_TARGETS = ("thresholds.yaml", "sources.yaml")

#: The tables a v1 edit kind may touch, each with its key column and the
#: per-kind writable column sets. The applier composes its SET clause only
#: from these; anything else fails before SQL exists.
WRITABLE: Dict[str, Dict[str, Any]] = {
    "nuggets": {
        "key": "unique_key",
        "kinds": {"relabel_nuggets": ("category",)},
    },
    "ideas": {
        "key": "id",
        "kinds": {
            "edit_idea": ("title", "problem_statement", "proposed_solution"),
            "rescore_idea": (
                "demand_signal",
                "feasibility",
                "competition",
                "overall",
                "needs_score",
            ),
        },
    },
}

TEXT_LIMITS = {"title": 300, "problem_statement": 5000, "proposed_solution": 5000,
               "category": 80}
SCORE_MIN, SCORE_MAX = 0.0, 10.0
_MAX_TARGETS = 1000

_EDIT_LOCK = threading.Lock()


class EditError(Exception):
    """A caller-visible mistake: bad payload, unknown edit, wrong state."""


class ForbiddenTarget(EditError):
    """Structurally out of bounds — raised before any SQL is composed."""


class StaleProposal(EditError):
    """The rows this change described changed underneath it."""


# ── small helpers ────────────────────────────────────────────────────────

def ensure_edits_schema(db: sqlite3.Connection) -> None:
    """Create the edit-regime tables (idempotent) and normalize row access.

    The row factory part matters because `open_db` hands out Rows to the
    console only (migrate=False); a CLI-opened connection comes back with
    plain tuples, so every reader must set it — following the same local
    pattern `store.py` itself uses in `pending_batches`, `list_clusters` et
    al. Idempotent; cheap enough to call at every entry point, which is
    also what keeps legacy archives (opened with migrate=False) working.
    """
    db.row_factory = sqlite3.Row
    db.executescript(EDITS_SCHEMA)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_to_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _mint(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def _check_actor(actor_kind: str, actor: str) -> None:
    if actor_kind not in ACTOR_KINDS:
        raise EditError(f"actor_kind must be one of {', '.join(ACTOR_KINDS)}")
    if not (actor or "").strip():
        raise EditError("actor must name who made the change (model, agent or operator)")


def _models_used_json(models_used: Any) -> Optional[str]:
    if models_used is None:
        return None
    if isinstance(models_used, str):
        return json.dumps([models_used])
    if isinstance(models_used, (list, tuple)):
        return json.dumps(list(models_used))
    return json.dumps([models_used])


def _norm_targets(targets: Sequence[Union[str, int]]) -> List[str]:
    out: List[str] = []
    for t in targets:
        s = str(t).strip()
        if not s:
            raise EditError("empty target key")
        if s not in out:
            out.append(s)
    if not out:
        raise EditError("targets must be a non-empty list")
    if len(out) > _MAX_TARGETS:
        raise EditError(f"too many targets: {len(out)} > {_MAX_TARGETS}")
    return out


def _reason_text(reason: Any) -> Optional[str]:
    if reason is None:
        return None
    txt = str(reason).strip()
    if not txt:
        return None
    return txt[:2000]


def _table_of(edit_kind: str) -> str:
    for table, spec in WRITABLE.items():
        if edit_kind in spec["kinds"]:
            return table
    raise EditError(f"unknown edit_kind {edit_kind!r}")


def _fetch_rows(
    db: sqlite3.Connection, table: str, keys: Sequence[str]
) -> List[sqlite3.Row]:
    key_col = WRITABLE[table]["key"]
    vals = [int(k) if key_col == "id" else k for k in keys]
    ph = ",".join("?" for _ in vals)
    rows = db.execute(
        f"SELECT * FROM {table} WHERE {key_col} IN ({ph})", vals
    ).fetchall()
    if len(rows) != len(vals):
        found_keys = set()
        for r in rows:
            found_keys.add(str(r[r.keys().index(key_col)]))
        missing = [k for k in keys if k not in found_keys]
        raise EditError(f"targets not found in {table}: {', '.join(missing[:5])}")
    return rows


def _cell(row: sqlite3.Row, col: str) -> Any:
    return row[row.keys().index(col)]


# ── payload validation + before/after images ─────────────────────────────

def _validated_payload(edit_kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a payload; refuses anything not in its column set."""
    table = _table_of(edit_kind)
    cols = WRITABLE[table]["kinds"][edit_kind]
    if not isinstance(payload, dict) or not payload:
        raise EditError("payload must be a non-empty object")
    if edit_kind == "relabel_nuggets":
        extra = [k for k in payload if k != "category"]
        if extra:
            raise ForbiddenTarget(
                f"payload fields not writable for {edit_kind}: {', '.join(sorted(extra))}"
            )
        cat = payload.get("category")
        if not isinstance(cat, str) or not cat.strip():
            raise EditError("relabel_nuggets needs a non-empty 'category' string")
        if len(cat.strip()) > TEXT_LIMITS["category"]:
            raise EditError(f"'category' is longer than {TEXT_LIMITS['category']} characters")
        return {"category": cat.strip()}
    if edit_kind == "edit_idea":
        unknown = [k for k in payload if k not in cols]
        if unknown:
            raise ForbiddenTarget(
                f"payload fields not writable for {edit_kind}: {', '.join(sorted(unknown))}"
            )
        fields: Dict[str, str] = {}
        for k, v in payload.items():
            if not isinstance(v, str) or not v.strip():
                raise EditError(f"'{k}' must be a non-empty string")
            if len(v.strip()) > TEXT_LIMITS[k]:
                raise EditError(f"'{k}' is longer than {TEXT_LIMITS[k]} characters")
            fields[k] = v.strip()
        return fields
    # rescore_idea: {"mode": "queue"} or {"mode": "manual", <scores>}
    mode = payload.get("mode", "queue")
    if mode == "queue":
        stray = [k for k in payload if k != "mode"]
        if stray:
            raise EditError("rescore_idea with mode 'queue' takes no other fields")
        return {"mode": "queue"}
    if mode != "manual":
        raise EditError("rescore_idea mode must be 'queue' or 'manual'")
    out: Dict[str, Any] = {}
    for col in ("demand_signal", "feasibility", "competition", "overall"):
        if col not in payload:
            continue
        v = payload[col]
        if v is not None:
            if not isinstance(v, (int, float)) or not (SCORE_MIN <= float(v) <= SCORE_MAX):
                raise EditError(
                    f"'{col}' must be a number within {SCORE_MIN:g}–{SCORE_MAX:g}"
                )
        out[col] = v
    if not out or all(v is None for v in out.values()):
        raise EditError("manual rescore needs at least one non-null score")
    return {"mode": "manual", **out}


def _after_fields(edit_kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Which writable columns change, with the proposed values."""
    if edit_kind == "rescore_idea" and payload.get("mode") == "queue":
        return {"needs_score": 1}
    if edit_kind == "rescore_idea":
        # last_scored_at is mechanical bookkeeping stamped at apply time, kept
        # out of the proposal's diff; the applied edit's after image has the
        # real stamp.
        return {k: v for k, v in payload.items() if k != "mode"}
    return dict(payload)


def _images(
    db: sqlite3.Connection,
    edit_kind: str,
    keys: Sequence[str],
    norm: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    table = _table_of(edit_kind)
    cols = WRITABLE[table]["kinds"][edit_kind]
    after_fields = {c: v for c, v in _after_fields(edit_kind, norm).items() if c in cols}
    before: Dict[str, Dict[str, Any]] = {}
    after: Dict[str, Dict[str, Any]] = {}
    for row in _fetch_rows(db, table, keys):
        k = str(_cell(row, WRITABLE[table]["key"]))
        before[k] = {c: _cell(row, c) for c in cols}
        after[k] = dict(after_fields)
    return before, after


def linked_ideas(db: sqlite3.Connection, nugget_keys: Sequence[str]) -> List[int]:
    """Idea ids whose supporting_nuggets reference any of these nugget keys."""
    wanted = set(nugget_keys)
    out: List[int] = []
    for row in db.execute("SELECT id, supporting_nuggets FROM ideas"):
        raw = _cell(row, "supporting_nuggets")
        if not raw:
            continue
        try:
            keys = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if wanted.intersection(k for k in keys if isinstance(k, str)):
            out.append(int(_cell(row, "id")))
    return out


# ── proposals ────────────────────────────────────────────────────────────

def propose_edit(
    db: sqlite3.Connection,
    *,
    edit_kind: str,
    targets: Sequence[Union[str, int]],
    payload: Dict[str, Any],
    actor_kind: str,
    actor: str,
    reason: str = "",
    models_used: Any = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a change, build its diff, store it. Nothing else is touched."""
    ensure_edits_schema(db)
    _check_actor(actor_kind, actor)
    table = _table_of(edit_kind)
    keys = _norm_targets(targets)
    norm = _validated_payload(edit_kind, dict(payload))
    before, after = _images(db, edit_kind, keys, norm)
    downstream = {"rescore_ideas": linked_ideas(db, keys) if table == "nuggets" else []}
    proposal_id = _mint("P")
    db.execute(
        "INSERT INTO edit_proposals(proposal_id, created_utc, actor_kind, actor, "
        "edit_kind, target_table, target_keys, payload_json, diff_json, "
        "affected_json, downstream_json, reason, models_used, status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'proposed')",
        (
            proposal_id, now or _utcnow(), actor_kind, actor, edit_kind, table,
            json.dumps(keys), json.dumps(norm),
            json.dumps({"before": before, "after": after}),
            json.dumps({table: len(keys)}), json.dumps(downstream),
            _reason_text(reason), _models_used_json(models_used),
        ),
    )
    db.commit()
    return proposal_row(db, proposal_id)


def proposal_row(db: sqlite3.Connection, proposal_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM edit_proposals WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    return _proposal_dict(row) if row else None


def _proposal_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    for jk in ("target_keys", "payload_json", "affected_json",
               "downstream_json", "diff_json", "models_used"):
        if isinstance(d.get(jk), str):
            try:
                d[jk] = json.loads(d[jk])
            except (TypeError, ValueError):
                pass
    diff = d.get("diff_json")
    if isinstance(diff, dict) and isinstance(diff.get("before"), dict):
        d["diff"] = [
            {"key": k, "before": diff["before"][k], "after": diff["after"][k]}
            for k in diff["before"]
        ]
    return d


def pending_proposals(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    ensure_edits_schema(db)
    rows = db.execute(
        "SELECT * FROM edit_proposals WHERE status='proposed' ORDER BY created_utc DESC, proposal_id DESC"
    ).fetchall()
    return [_proposal_dict(r) for r in rows]


def reject_proposal(
    db: sqlite3.Connection,
    proposal_id: str,
    *,
    actor_kind: str = "human",
    actor: str,
    reason: str = "",
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Decline a proposal; refuses anything that already left 'proposed'."""
    _check_actor(actor_kind, actor)
    row = db.execute(
        "SELECT status FROM edit_proposals WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    if not row:
        raise EditError(f"unknown proposal {proposal_id}")
    if row[0] != "proposed":
        raise EditError(f"proposal {proposal_id} is {row[0]}; only 'proposed' can be rejected")
    db.execute(
        "UPDATE edit_proposals SET status='rejected', decided_utc=?, decision_note=? "
        "WHERE proposal_id=?",
        (now or _utcnow(), _reason_text(reason), proposal_id),
    )
    db.commit()
    return {"ok": True, "proposal_id": proposal_id, "status": "rejected"}


def expire_proposals(db: sqlite3.Connection, *, ttl_hours: float,
                     now: Optional[str] = None) -> int:
    """Mark proposals older than ttl_hours 'expired'. Returns count marked."""
    cut = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _EDIT_LOCK:
        cur = db.execute(
            "UPDATE edit_proposals SET status='expired', decided_utc=? "
            "WHERE status='proposed' AND created_utc < ?",
            (now or _utcnow(), cut),
        )
        db.commit()
    return cur.rowcount


# ── config-file proposals (the second arm of the allow-list) ────────────

#: Family roles for staged signal rules. Mirrored from
#: signals/filters.py::load_rules ON PURPOSE: the proposal boundary must not
#: open an import path into the deterministic acceptance path it regulates.
#: If that rule set changes shape, update both — the tests tie them together.
SIGNAL_RULE_ROLES = ("buyer_required", "buyer_boost", "pain_marker", "negative")

_MISSING = object()


def propose_threshold(
    db: sqlite3.Connection,
    *,
    key: str,
    value: Any,
    config_dir: str,
    actor_kind: str = "chat",
    actor: str,
    reason: str = "",
    models_used: Any = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Mint a thresholds.yaml change proposal, registry-validated.

    `key` must be an editable scalar threshold (mapping keys are edited by
    hand — a chat proposes what a human can verify at a glance) and `value`
    must pass `coerce_thresholds_patch`, which enforces the _RANGES registry
    the same way the console's own settings writer does.
    """
    _check_actor(actor_kind, actor)
    key = (key or "").strip()
    if not key:
        raise EditError("key must be a non-empty threshold name")
    caster = Thresholds._EDITABLE.get(key)
    if caster is None:
        raise EditError(f"'{key}' is not an editable threshold")
    if caster is dict:
        raise EditError(f"'{key}' is a mapping key; edit config/thresholds.yaml by hand")
    current = load_thresholds(Path(config_dir) / "thresholds.yaml")
    try:
        coerced = coerce_thresholds_patch({key: value}, current)
    except ConfigError as exc:
        raise EditError(str(exc)) from None
    return _store_proposal(
        db,
        edit_kind="set_threshold",
        target_table="thresholds.yaml",
        target_keys=[key],
        payload={"key": key, "value": coerced[key]},
        diff={"before": {key: getattr(current, key)}, "after": {key: coerced[key]}},
        affected={"thresholds.yaml": 1},
        downstream={},
        actor_kind=actor_kind,
        actor=actor,
        reason=reason,
        models_used=models_used,
        now=now,
    )


def propose_source_toggle(
    db: sqlite3.Connection,
    *,
    source: str,
    enabled: bool,
    config_dir: str,
    actor_kind: str = "chat",
    actor: str,
    reason: str = "",
    models_used: Any = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Mint a sources.yaml enable/disable proposal for an EXISTING source.

    A chat never adds sources in v1 — adding a source is operator work with
    spec review attached (design §5 S6). It also never proposes a toggle to
    the state the source is already in.
    """
    _check_actor(actor_kind, actor)
    name = (source or "").strip()
    if not name:
        raise EditError("source must be a non-empty name")
    srcs = load_sources(Path(config_dir) / "sources.yaml")
    match = [s for s in srcs if s.name == name]
    if not match:
        raise EditError(f"source {name!r} is not in sources.yaml")
    old, new = bool(match[0].enabled), bool(enabled)
    if old == new:
        raise EditError(
            f"source {name!r} is already {'enabled' if new else 'disabled'}"
        )
    return _store_proposal(
        db,
        edit_kind="toggle_source",
        target_table="sources.yaml",
        target_keys=[name],
        payload={"source": name, "enabled": new},
        diff={"before": {name: old}, "after": {name: new}},
        affected={"sources.yaml": 1},
        downstream={},
        actor_kind=actor_kind,
        actor=actor,
        reason=reason,
        models_used=models_used,
        now=now,
    )


def propose_signal_rule(
    db: sqlite3.Connection,
    *,
    family: Dict[str, Any],
    justification: str = "",
    actor_kind: str = "chat",
    actor: str,
    models_used: Any = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage a drafted signal family for HUMAN promotion — nothing else.

    Writes only the signal_rule_proposals staging table. The acceptance path
    reads config/signal_rules.yaml and changes to it run through the 20-case
    fixture gate; a staged family reaches that file exclusively when a human
    promotes it by hand. This function never writes there.
    """
    _check_actor(actor_kind, actor)
    if not isinstance(family, dict):
        raise EditError("family must be an object with name/role/weight/phrases")
    name = str(family.get("name", "")).strip()
    if not name or len(name) > 80:
        raise EditError("family name must be 1–80 characters")
    role = str(family.get("role", "")).strip()
    if role not in SIGNAL_RULE_ROLES:
        raise EditError(f"role must be one of {', '.join(SIGNAL_RULE_ROLES)}")
    weight = family.get("weight")
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise EditError("weight must be a number")
    if role == "negative" and weight >= 0:
        raise EditError("a negative family must have weight < 0")
    if role != "negative" and not 0 < weight <= 1:
        raise EditError("non-negative families need 0 < weight <= 1")
    phrases = family.get("phrases")
    if not isinstance(phrases, (list, tuple)) or not phrases:
        raise EditError("phrases must be a non-empty list")
    cleaned: List[str] = []
    for raw in phrases[:30]:
        phrase = str(raw).strip()
        if not phrase or len(phrase) > 120:
            raise EditError("each phrase must be 1–120 characters")
        if not any(phrase.lower() == c.lower() for c in cleaned):
            cleaned.append(phrase)
    note = (str(justification).strip() or None)
    if note:
        note = note[:600]
    rule_id = _mint("R")
    db.execute(
        "INSERT INTO signal_rule_proposals(id, created_utc, actor_kind, actor, "
        "payload_json, justification, models_used, status) "
        "VALUES(?,?,?,?,?,?,?, 'proposed')",
        (
            rule_id, now or _utcnow(), actor_kind, actor,
            json.dumps({"name": name, "role": role, "weight": weight,
                        "phrases": cleaned}),
            note, _models_used_json(models_used),
        ),
    )
    db.commit()
    return _rule_row(db, rule_id)


def _rule_row(db: sqlite3.Connection, rule_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM signal_rule_proposals WHERE id=?", (rule_id,)
    ).fetchone()
    return _rule_dict(row) if row else None


def _rule_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    for jk in ("payload_json", "models_used"):
        if isinstance(d.get(jk), str):
            try:
                d["payload" if jk == "payload_json" else jk] = json.loads(d[jk])
            except (TypeError, ValueError):
                pass
    return d


def signal_rule_rows(
    db: sqlite3.Connection, *, status: str = "proposed", limit: int = 50
) -> List[Dict[str, Any]]:
    """Staging-table reads for the console and the tools."""
    ensure_edits_schema(db)
    rows = db.execute(
        "SELECT * FROM signal_rule_proposals WHERE status=? ORDER BY rowid DESC LIMIT ?",
        (status, limit),
    ).fetchall()
    return [_rule_dict(r) for r in rows]


def _store_proposal(
    db: sqlite3.Connection,
    *,
    edit_kind: str,
    target_table: str,
    target_keys: Sequence[str],
    payload: Dict[str, Any],
    diff: Dict[str, Any],
    affected: Dict[str, int],
    downstream: Dict[str, Any],
    actor_kind: str,
    actor: str,
    reason: str = "",
    models_used: Any = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared INSERT for SQL and config proposals; returns the parsed row."""
    proposal_id = _mint("P")
    db.execute(
        "INSERT INTO edit_proposals(proposal_id, created_utc, actor_kind, actor, "
        "edit_kind, target_table, target_keys, payload_json, diff_json, "
        "affected_json, downstream_json, reason, models_used, status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'proposed')",
        (
            proposal_id, now or _utcnow(), actor_kind, actor, edit_kind,
            target_table, json.dumps(list(target_keys)), json.dumps(payload),
            json.dumps(diff), json.dumps(affected), json.dumps(downstream),
            _reason_text(reason), _models_used_json(models_used),
        ),
    )
    db.commit()
    return proposal_row(db, proposal_id)


def _apply_config_edit(
    db: sqlite3.Connection,
    prop: Dict[str, Any],
    *,
    stamp: str,
    actor_kind: str,
    actor: str,
    config_dir: Optional[str],
) -> Dict[str, Any]:
    """Apply a config-target proposal through the audited config writers.

    Drift-checked like every apply: the live value must still be what the
    proposal's before-image recorded, or the mutator refuses. The file write
    and the log insert both happen inside the edit lock, so two racing
    appliers cannot both transition the proposal.
    """
    if not config_dir:
        raise EditError(
            f"applying {prop['target_table']} edits needs config_dir at apply time"
        )
    with _EDIT_LOCK:
        prop = proposal_row(db, prop["proposal_id"]) or prop
        if prop["status"] != "proposed":
            raise EditError(
                f"proposal {prop['proposal_id']} is {prop['status']}; "
                "only 'proposed' can be applied"
            )
        key = list(prop["target_keys"])[0]
        diff = prop["diff_json"]
        before_val, after_val = diff["before"][key], diff["after"][key]
        if prop["edit_kind"] == "set_threshold":
            path = Path(config_dir) / "thresholds.yaml"
            current = load_thresholds(path)
            if getattr(current, key, _MISSING) is _MISSING:
                raise StaleProposal(f"thresholds.yaml lost the key '{key}'")
            if getattr(current, key) != before_val:
                raise StaleProposal(
                    f"thresholds.yaml '{key}' changed since the proposal was built"
                )
            try:
                applied = coerce_thresholds_patch({key: after_val}, current)[key]
            except ConfigError as exc:
                raise StaleProposal(f"'{key}' is no longer a valid value: {exc}") from None
            save_thresholds(str(path), {key: applied})
        elif prop["edit_kind"] == "toggle_source":
            path = Path(config_dir) / "sources.yaml"
            srcs = load_sources(path)
            match = [s for s in srcs if s.name == key]
            if not match:
                raise StaleProposal(
                    f"source {key!r} left sources.yaml since the proposal was built"
                )
            if bool(match[0].enabled) != bool(before_val):
                raise StaleProposal(
                    f"sources.yaml '{key}' changed since the proposal was built"
                )
            match[0].enabled = bool(after_val)
            save_sources(str(path), srcs)
            applied = bool(after_val)
        else:
            raise EditError(f"unknown config edit kind {prop['edit_kind']!r}")
        edit_id = _mint("E")
        db.execute(
            "INSERT INTO edit_log(id, created_utc, actor_kind, actor, tool_name, "
            "edit_kind, target_table, target_keys, before_json, after_json, "
            "reason, models_used, proposal_id, reverts_edit, outcome) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, NULL, 'applied')",
            (
                edit_id, stamp, actor_kind, actor, "apply_edit",
                prop["edit_kind"], prop["target_table"],
                json.dumps(list(prop["target_keys"])),
                json.dumps({key: before_val}), json.dumps({key: applied}),
                prop.get("reason"),
                json.dumps(prop["models_used"]) if prop.get("models_used") else None,
                prop["proposal_id"],
            ),
        )
        db.execute(
            "UPDATE edit_proposals SET status='applied', decided_utc=?, edit_id=? "
            "WHERE proposal_id=?",
            (stamp, edit_id, prop["proposal_id"]),
        )
        db.commit()
    return {
        "ok": True,
        "edit_id": edit_id,
        "proposal_id": prop["proposal_id"],
        "affected": {prop["target_table"]: 1},
        "downstream": {},
    }


def _revert_config(
    db: sqlite3.Connection,
    orig: sqlite3.Row,
    *,
    stamp: str,
    actor_kind: str,
    actor: str,
    reason: str = "",
    config_dir: Optional[str],
) -> str:
    """Restore a config proposal's before-value; returns the new edit id.

    Runs inside the caller's _EDIT_LOCK, after the same-shape guards
    (not-a-revert, not-already-reverted) have passed in revert_edit.
    """
    if not config_dir:
        raise EditError(
            f"reverting {orig['target_table']} edits needs config_dir at revert time"
        )
    key = json.loads(orig["target_keys"])[0]
    before_map = json.loads(orig["before_json"])
    after_map = json.loads(orig["after_json"])
    if key not in after_map:
        raise StaleProposal(f"edit {orig['id']} carries no after-image for '{key}'")
    if orig["edit_kind"] == "set_threshold":
        path = Path(config_dir) / "thresholds.yaml"
        current = load_thresholds(path)
        if getattr(current, key, _MISSING) is _MISSING:
            raise StaleProposal(f"thresholds.yaml lost the key '{key}'")
        if getattr(current, key) != after_map[key]:
            raise StaleProposal(
                f"thresholds.yaml '{key}' changed since edit {orig['id']} was applied"
            )
        save_thresholds(str(path), {key: before_map[key]})
    elif orig["edit_kind"] == "toggle_source":
        srcs = load_sources(Path(config_dir) / "sources.yaml")
        match = [s for s in srcs if s.name == key]
        if not match:
            raise StaleProposal(
                f"source {key!r} left sources.yaml since edit {orig['id']} was applied"
            )
        if bool(match[0].enabled) != bool(after_map[key]):
            raise StaleProposal(
                f"sources.yaml '{key}' changed since edit {orig['id']} was applied"
            )
        match[0].enabled = bool(before_map[key])
        save_sources(Path(config_dir) / "sources.yaml", srcs)
    else:
        raise EditError(f"cannot revert config kind {orig['edit_kind']!r}")
    revert_id = _mint("E")
    db.execute(
        "INSERT INTO edit_log(id, created_utc, actor_kind, actor, tool_name, "
        "edit_kind, target_table, target_keys, before_json, after_json, "
        "reason, models_used, proposal_id, reverts_edit, outcome) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?, NULL, ?, 'applied')",
        (
            revert_id, stamp, actor_kind, actor, "revert_edit",
            "revert", orig["target_table"], orig["target_keys"],
            json.dumps(after_map), json.dumps(before_map),
            _reason_text(reason), None, orig["id"],
        ),
    )
    return revert_id


# ── apply + revert ───────────────────────────────────────────────────────

def _expire_one(db: sqlite3.Connection, proposal_id: str, now: Optional[str]) -> None:
    db.execute(
        "UPDATE edit_proposals SET status='expired', decided_utc=? "
        "WHERE proposal_id=? AND status='proposed'",
        (now or _utcnow(), proposal_id),
    )
    db.commit()


def apply_edit(
    db: sqlite3.Connection,
    proposal_id: str,
    *,
    actor_kind: str = "chat",
    actor: str,
    ttl_hours: Optional[float] = None,
    now: Optional[str] = None,
    config_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a proposal exactly as validated, and write the audit row.

    Refuses: unknown proposal, one already decided, one expired per
    `ttl_hours` (checked lazily, marked expired) — and one whose rows
    changed since it was built, which raises StaleProposal after zero
    writes. The log row keeps the PROPOSER in `models_used`/proposal actor
    and the APPLIER as actor: both sides of a confirmation flow, honestly.
    Config-file targets (thresholds.yaml / sources.yaml) apply through the
    audited config writers and need `config_dir` passed in.
    """
    ensure_edits_schema(db)
    _check_actor(actor_kind, actor)
    stamp = now or _utcnow()
    prop = proposal_row(db, proposal_id)
    if not prop:
        raise EditError(f"unknown proposal {proposal_id}")
    if prop["status"] == "proposed" and ttl_hours is not None:
        if _utc_to_dt(prop["created_utc"]) + timedelta(hours=ttl_hours) < _utc_to_dt(stamp):
            _expire_one(db, proposal_id, stamp)
            raise StaleProposal(
                f"proposal {proposal_id} expired after {ttl_hours:g}h; propose again"
            )
    if prop["status"] != "proposed":
        raise EditError(f"proposal {proposal_id} is {prop['status']}; only 'proposed' can be applied")
    if prop["target_table"] in CONFIG_TARGETS:
        return _apply_config_edit(
            db, prop, stamp=stamp, actor_kind=actor_kind, actor=actor,
            config_dir=config_dir,
        )
    edit_kind, table = prop["edit_kind"], prop["target_table"]
    keys = list(prop["target_keys"]) if isinstance(prop["target_keys"], list) else json.loads(prop["target_keys"])
    norm = prop["payload_json"] if isinstance(prop["payload_json"], dict) else json.loads(prop["payload_json"])
    before, _ = _images(db, edit_kind, keys, norm)
    stored = prop["diff_json"] if isinstance(prop["diff_json"], dict) else json.loads(prop["diff_json"])
    if stored.get("before") != before:
        raise StaleProposal(
            f"rows changed since proposal {proposal_id} was built; propose again"
        )
    after = stored["after"]
    edit_id = _mint("E")
    stamp_scores = isinstance(norm, dict) and norm.get("mode") == "manual"
    applied_after = _applied_after(after, stamp_scores=stamp_scores, stamp=stamp)
    with _EDIT_LOCK:
        try:
            _write_rows(db, table, edit_kind, keys, applied_after)
            models_used = None
            if prop.get("models_used"):
                models_used = json.dumps(prop["models_used"])
            db.execute(
                "INSERT INTO edit_log(id, created_utc, actor_kind, actor, tool_name, "
                "edit_kind, target_table, target_keys, before_json, after_json, "
                "reason, models_used, proposal_id, reverts_edit, outcome) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, NULL, 'applied')",
                (
                    edit_id, stamp, actor_kind, actor, "apply_edit", edit_kind,
                    table, json.dumps(keys), json.dumps(before),
                    json.dumps(applied_after),
                    prop.get("reason"), models_used, proposal_id,
                ),
            )
            db.execute(
                "UPDATE edit_proposals SET status='applied', decided_utc=?, edit_id=? "
                "WHERE proposal_id=?",
                (stamp, edit_id, proposal_id),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {
        "ok": True,
        "edit_id": edit_id,
        "proposal_id": proposal_id,
        "affected": prop["affected_json"],
        "downstream": prop["downstream_json"],
    }


def _applied_after(
    proposed_after: Dict[str, Dict[str, Any]],
    *,
    stamp_scores: bool,
    stamp: str,
) -> Dict[str, Dict[str, Any]]:
    """The as-applied after image: a manual rescore also gets its stamp.

    `stamp_scores` is true only for rescore_idea manual mode, where
    last_scored_at is bookkeeping set at apply time (critic_model is
    deliberately untouched — the log row's models_used says who set this).
    """
    rows = {k: dict(v) for k, v in proposed_after.items()}
    if stamp_scores:
        for image in rows.values():
            image["last_scored_at"] = stamp
    return rows


def _write_rows(
    db: sqlite3.Connection,
    table: str,
    edit_kind: str,
    keys: Sequence[str],
    after: Dict[str, Dict[str, Any]],
) -> None:
    """The ONLY scalar write path here.

    The SET clause is composed strictly from the proposal's validated
    after-image, whose columns were already constrained to WRITABLE by
    _validated_payload. Guarded again here so that a future bug above this
    line still cannot reach a provenance column.
    """
    spec = WRITABLE[table]
    key_col = spec["key"]
    allowed = set(spec["kinds"][edit_kind])
    for k in keys:
        image = after.get(k)
        if not image:
            continue
        bad = set(image) - allowed - {"last_scored_at"}
        if bad:
            raise ForbiddenTarget(
                f"columns not writable for {edit_kind}: {', '.join(sorted(bad))}"
            )
        val = int(k) if key_col == "id" else k
        sets = ", ".join(f"{c}=?" for c in image)
        db.execute(
            f"UPDATE {table} SET {sets} WHERE {key_col}=?", (*image.values(), val)
        )


def revert_edit(
    db: sqlite3.Connection,
    edit_id: str,
    *,
    actor_kind: str = "human",
    actor: str,
    reason: str = "",
    now: Optional[str] = None,
    config_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Undo a logged edit by writing the inverse as a NEW edit_log row.

    Refuses: unknown id; reverting a revert (apply the change forward again
    instead); an edit already reverted; an edit whose post-image no longer
    matches the live values (StaleProposal — the world moved on). Config
    targets (thresholds.yaml / sources.yaml) revert through the same audited
    mutators, so a config file keeps a single writer.
    """
    ensure_edits_schema(db)
    _check_actor(actor_kind, actor)
    with _EDIT_LOCK:
        orig = _edit_row(db, edit_id)
        if not orig:
            raise EditError(f"unknown edit {edit_id}")
        if orig["edit_kind"] == "revert":
            raise EditError(f"edit {edit_id} is itself a revert; a revert cannot be reverted")
        if orig["outcome"] != "applied":
            raise EditError(f"edit {edit_id} is {orig['outcome']}")
        already = db.execute(
            "SELECT id FROM edit_log WHERE reverts_edit=?", (edit_id,)
        ).fetchone()
        if already:
            raise EditError(f"edit {edit_id} was already reverted by {already[0]}")
        stamp = now or _utcnow()
        if orig["target_table"] in CONFIG_TARGETS:
            revert_id = _revert_config(
                db, orig, stamp=stamp, actor_kind=actor_kind, actor=actor,
                reason=reason, config_dir=config_dir,
            )
        else:
            before_map = json.loads(orig["before_json"])
            after_map = json.loads(orig["after_json"])
            table = orig["target_table"]
            keys = json.loads(orig["target_keys"])
            rows = _fetch_rows(db, table, keys)
            for row in rows:
                k = str(_cell(row, WRITABLE[table]["key"]))
                for col, expected in after_map.get(k, {}).items():
                    got = _cell(row, col)
                    if got != expected:
                        raise StaleProposal(
                            f"{table}.{k}.{col} is {got!r}; edit {edit_id} set {expected!r}"
                        )
            revert_id = _mint("E")
            db.execute(
                "INSERT INTO edit_log(id, created_utc, actor_kind, actor, tool_name, "
                "edit_kind, target_table, target_keys, before_json, after_json, "
                "reason, models_used, proposal_id, reverts_edit, outcome) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?, NULL, ?, 'applied')",
                (
                    revert_id, stamp, actor_kind, actor, "revert_edit", "revert",
                    # Images OF the revert itself: before it, the rows held
                    # the original edit's after-values; after, the originals.
                    table, json.dumps(keys), json.dumps(after_map),
                    json.dumps(before_map), _reason_text(reason), None, edit_id,
                ),
            )
            for k in keys:
                image = before_map[str(k)]
                key_col = WRITABLE[table]["key"]
                sets = ", ".join(f"{c}=?" for c in image)
                val = int(k) if key_col == "id" else k
                db.execute(
                    f"UPDATE {table} SET {sets} WHERE {key_col}=?", (*image.values(), val)
                )
        db.commit()
    return {"ok": True, "revert_id": revert_id, "reverted": edit_id}


def _edit_row(db: sqlite3.Connection, edit_id: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM edit_log WHERE id=?", (edit_id,)).fetchone()


# ── reads ────────────────────────────────────────────────────────────────

def edit_rows(
    db: sqlite3.Connection, *, limit: int = 50
) -> List[Dict[str, Any]]:
    """Newest-first edit_log rows with JSON fields parsed."""
    ensure_edits_schema(db)
    rows = db.execute(
        "SELECT * FROM edit_log ORDER BY rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        for jk in ("target_keys", "before_json", "after_json", "models_used"):
            if isinstance(d.get(jk), str):
                try:
                    d[jk] = json.loads(d[jk])
                except (TypeError, ValueError):
                    pass
        out.append(d)
    return out


#: Alias kept for the console layer.
edit_dicts = edit_rows


def was_reverted(db: sqlite3.Connection, edit_id: str) -> bool:
    """Append-only lookup: is there a revert row naming this edit?"""
    ensure_edits_schema(db)
    return db.execute(
        "SELECT 1 FROM edit_log WHERE reverts_edit=?", (edit_id,)
    ).fetchone() is not None
