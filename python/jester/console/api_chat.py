"""The chat's console side: what a model may start, what a human decides.

Split from api.py on purpose — that file is the god-file this feature must
not grow. This module is a MIXIN the console composes in one line, so the
chat's HTTP behaviour stays inspectable in one place.

Shape, one job at a time:
  POST /api/chat            {text, session_id?} → start_job(...) → poll
  GET  /api/chat/sessions   transcript index
  GET  /api/chat/session    full transcript + tool calls + pending proposals

The human confirmation flows (actor_kind='human', the console's actor):
  POST /api/chat/apply      {proposal_id, reason?}   the yes
  POST /api/chat/reject     {proposal_id, reason?}   the no
  POST /api/chat/revert     {edit_id, reason?}       the undo

Discipline, enforced here rather than trusted to callers:
- A chat turn only STARTS when `chat_enabled` is true — the kill switch.
  The human endpoints stay usable either way: a proposal on file needs its
  disposition even after the flag goes back off.
- The model side runs inside jobs.start_job, so a chat turn refuses when a
  pipeline is running (and vice versa). Two models cannot scrape and edit
  the same archive at once.
- Everything human goes through jester.edits with actor_kind='human', so
  the audit rows say console-operator, with who/what/why exactly as an
  edit_log row carries them.
- Nothing here reads or writes chat history for decisions. Decisions read
  the PROPOSAL (its diff is on the row); the transcript is for people.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from jester.chat.store import ChatStore, chat_db_path
from jester.chat.tools import ToolContext
from jester import edits

#: An S- file stamp + 3 hex pairs. Refusing anything else here is the same
#: measure /api/post/<thread> takes: malformed ids are not for real rows.
_SESSION_ID_RE = re.compile(r"^S-[0-9]{14}-[0-9a-f]{6}$")
_PROPOSAL_ID_RE = re.compile(r"^P-[0-9T\-:]{15,19}-[0-9]{8}-[0-9a-f]{8}$")
_EDIT_ID_RE = re.compile(r"^E-[0-9T\-:]{15,19}-[0-9]{8}-[0-9a-f]{8}$")
_MAX_TURN_CHARS = 4000


class ChatAPI:
    """Mixed into ConsoleAPI; relies on self.db, self.db_path,
    self.config_dir, self._cfg() and self._action_guard()."""

    JOB_KIND = "chat"

    # ---- the gate ---------------------------------------------------------

    def _chat_enabled(self) -> bool:
        try:
            return bool(self._cfg().thresholds.chat_enabled)
        except Exception:  # noqa: BLE001 - unreadable config = chat off
            return False

    # ---- model side -------------------------------------------------------

    def chat_start(self, text, session_id=None):
        """One turn of the chat as a background job (the console's own way)."""
        if not self._chat_enabled():
            return {"ok": False, "error": (
                "chat is off: set chat_enabled: true in thresholds.yaml to "
                "open it")}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "an empty turn has nothing to answer"}
        if len(text) > _MAX_TURN_CHARS:
            return {"ok": False, "error": (
                f"a turn is at most {_MAX_TURN_CHARS} characters; "
                "continue in the next message")}
        session_id = self._check_session_id(session_id)
        if not session_id.get("ok"):
            return session_id

        from jester.chat.client import select_chat_client
        from jester.chat.session import SessionRunner

        settings = self._cfg().thresholds
        config_dir = self.config_dir
        db_path = self.db_path
        max_rounds = int(settings.chat_max_rounds)
        chat_db = chat_db_path(db_path)
        sid = session_id["session_id"] if "session_id" in session_id else None
        client = select_chat_client(settings)

        def work(progress: Callable[[str], None]):
            from jester.store import open_db

            progress("grounding the tools")
            db = open_db(db_path, migrate=False)
            try:
                ctx = ToolContext(db, config_dir, actor_kind="chat",
                                  actor=f"chat model {client.name}")
                runner = SessionRunner(ctx, client, chat_db=chat_db,
                                       session_id=sid, max_rounds=max_rounds)
                try:
                    turn = runner.run_turn(text)
                finally:
                    runner.close()
            finally:
                db.close()
            return {
                "ok": True,
                "session_id": turn.session_id,
                "reply": turn.reply,
                "rounds": turn.rounds,
                "proposals": turn.proposals,
                "warnings": turn.warnings,
            }

        from jester.jobs import start_job

        started = start_job(db_path, self.JOB_KIND, work)
        if isinstance(started, dict):
            # Refused to start (another job running, most likely): the dict
            # already carries ok/error in the console's own shape.
            return started
        return {"ok": True, "job_id": started}

    def chat_sessions(self):
        """Transcript index: newest sessions with their message counts."""
        store = ChatStore(chat_db_path(self.db_path))
        try:
            return {"ok": True, "sessions": store.list_sessions()}
        finally:
            store.close()

    def chat_session(self, session_id):
        """One transcript: messages, tool calls, and what awaits a human."""
        sid = self._check_session_id(session_id)
        if not sid.get("ok"):
            return sid
        store = ChatStore(chat_db_path(self.db_path))
        try:
            messages = [m for m in store.messages(sid["session_id"])]
            calls = [c for c in store.tool_calls(sid["session_id"])]
            sessions = [s for s in store.list_sessions(limit=200)
                        if s["session_id"] == sid["session_id"]]
            if not sessions:
                return {"ok": False, "error": f"unknown session {sid['session_id']}"}
        finally:
            store.close()
        return {
            "ok": True,
            "session": sessions[0],
            "messages": messages,
            "tool_calls": calls,
            "pending": edits.pending_proposals(self.db),
            "edits": edits.edit_rows(self.db, limit=20),
        }

    # ---- the human side ---------------------------------------------------

    def _human_action(self, fn):
        """Run an edits call: pass its payload through, normalize failures.

        `ConsoleAPI._action_guard` collapses every success to {"ok": True};
        the chat UI needs the `edit_id` an apply mints so it can offer the
        revert without a re-fetch. Failures keep the same shape as the
        console's guard: {"ok": False, "error": "..."}.
        """
        try:
            return fn()
        except SystemExit as e:  # pragma: no cover - parity with _action_guard
            return {"ok": False, "code": e.code}
        except Exception as exc:  # noqa: BLE001 - surfaced to the console UI
            return {"ok": False, "error": str(exc)}

    def chat_apply(self, proposal_id):
        """The yes: apply a proposed change as the console operator.

        No `reason` here on purpose: the WHY of an apply is the proposal
        row's own reason (the proposer's), and apply_edit keeps the audit
        honest by naming proposer and applier separately. Reject and revert
        take the human's reasons instead.
        """
        def go():
            settings = self._cfg().thresholds
            return edits.apply_edit(
                self.db, str(proposal_id or ""),
                actor_kind="human", actor="console-operator",
                ttl_hours=settings.chat_proposal_ttl_hours,
                config_dir=self.config_dir,
            )
        return self._human_action(go)

    def chat_reject(self, proposal_id, reason=""):
        """The no: decline a proposal. Nothing is written to the archive."""
        def go():
            return edits.reject_proposal(
                self.db, str(proposal_id or ""),
                actor_kind="human", actor="console-operator",
                reason=reason,
            )
        return self._human_action(go)

    def chat_revert(self, edit_id, reason=""):
        """The undo: write the inverse as a new edit_log row."""
        def go():
            return edits.revert_edit(
                self.db, str(edit_id or ""),
                actor_kind="human", actor="console-operator",
                reason=reason,
                config_dir=self.config_dir,
            )
        return self._human_action(go)

    # ---- plumbing ---------------------------------------------------------

    @staticmethod
    def _check_session_id(session_id):
        """Empty → approved continuation handed back as {"ok": True}."""
        if not session_id:
            return {"ok": True}
        session_id = str(session_id)
        if not _SESSION_ID_RE.fullmatch(session_id):
            # No echo: a malformed id is untrusted input, the same discipline
            # /api/post keeps — the refusal names the problem, not the string.
            return {"ok": False, "error": "bad session id"}
        return {"ok": True, "session_id": session_id}
