"""The chat's transcript store: its own ADD-only database beside the archive.

`chat.db` lives NEXT TO the archive (default `data/chat.db`) and is a log of
WHAT WAS SAID AND PROPOSED, never part of the deterministic archive. Rows are
INSERTed once and never updated or deleted — a correction is a new row, the
same discipline edit_log keeps. Sessions persist for the console's history
view (S4); nothing here feeds scheduling, scoring or acceptance, and no
provenance link lives here: for what actually CHANGED in the archive,
edit_log remains the single audit trail.

ADD-only checklist (what the journal keeps for cited run data, mirrored
here so the two stores cannot drift in discipline):
- no UPDATE/DELETE statements against chat_* tables in this module, ever
- no schema version dance: CREATE TABLE IF NOT EXISTS is the whole contract
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    created_utc TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    models_used TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, seq);
CREATE TABLE IF NOT EXISTS chat_tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_tool_calls_session
    ON chat_tool_calls(session_id, seq);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chat_db_path(archive_path: str) -> str:
    """chat.db beside the archive, whatever directory it lives in."""
    return str(Path(archive_path).parent / "chat.db")


class ChatStore:
    """Insert-only transcript storage for chat sessions."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(CHAT_SCHEMA)
        self.db.commit()

    # -- sessions -----------------------------------------------------------

    def begin_session(self, title: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        session_id = f"S-{stamp}-{secrets.token_hex(3)}"
        self.db.execute(
            "INSERT INTO chat_sessions(session_id, created_utc, title) "
            "VALUES(?,?,?)",
            (session_id, _utcnow(), (title or "(empty turn)")[:80]),
        )
        self.db.commit()
        return session_id

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT s.session_id, s.created_utc, s.title, "
            "(SELECT COUNT(*) FROM chat_messages m "
            " WHERE m.session_id = s.session_id) AS messages "
            "FROM chat_sessions s ORDER BY s.created_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- messages -----------------------------------------------------------

    def add_message(self, session_id: str, role: str, content: str,
                    models_used: Optional[List[str]] = None) -> int:
        cur = self.db.execute(
            "INSERT INTO chat_messages(session_id, seq, role, content, "
            "created_utc, models_used) VALUES(?,?,?,?,?,?)",
            (
                session_id,
                self._next_seq(session_id),
                role,
                content,
                _utcnow(),
                json.dumps(models_used) if models_used else None,
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def messages(self, session_id: str, limit: int = 400) -> List[dict]:
        rows = self.db.execute(
            "SELECT seq, role, content, created_utc, models_used "
            "FROM chat_messages WHERE session_id=? ORDER BY seq LIMIT ?",
            (session_id, limit),
        ).fetchall()
        out: List[dict] = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("models_used"), str):
                try:
                    d["models_used"] = json.loads(d["models_used"])
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return out

    # -- tool calls ---------------------------------------------------------

    def add_tool_call(self, session_id: str, seq: int, tool_name: str,
                      args: Dict[str, Any], result: Dict[str, Any]) -> int:
        cur = self.db.execute(
            "INSERT INTO chat_tool_calls(session_id, seq, tool_name, "
            "args_json, result_json, created_utc) VALUES(?,?,?,?,?,?)",
            (
                session_id,
                seq,
                tool_name,
                json.dumps(args, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                _utcnow(),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def tool_calls(self, session_id: str, limit: int = 400) -> List[dict]:
        rows = self.db.execute(
            "SELECT seq, tool_name, args_json, result_json, created_utc "
            "FROM chat_tool_calls WHERE session_id=? ORDER BY seq LIMIT ?",
            (session_id, limit),
        ).fetchall()
        out: List[dict] = []
        for row in rows:
            d = dict(row)
            d["args"] = d.pop("args_json")
            d["result"] = d.pop("result_json")
            out.append(d)
        return out

    # -- plumbing -----------------------------------------------------------

    def _next_seq(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_messages "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self.db.close()
