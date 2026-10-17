"""The chat's model clients, on the same agent bases every job uses.

One interface only: ``chat(system, history, manifest) -> ChatReply``, where
ChatReply = {text, tool_calls}. Whether the model wanted a tool or an answer
is the model's claim, parsed from ONE JSON object — the same discipline the
extractor/synthesizer/critic already use: ask for JSON, tolerate nothing
outside it, and record WHY a reply was unusable on the agent's own counters.

No quota logic here: `llm.py`'s agent base counts calls, fallbacks and
rate-limits per instance, exactly as it does for the other jobs; the session
reads those counters. Roles come from `jester.roles` (`roles.chat` or the
llm_provider fallback recorded in the rung table).

The stand-in (FakeChatClient) is list-scriptable so tests drive whole
conversations deterministically, offline, with JESTER_MOCK unset.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from jester.llm import _GroqAgent, _OllamaAgent

#: The most tool demands one model reply may carry; the rest are dropped as
#: an observation-level abuse, not a crash.
MAX_TOOL_CALLS_PER_REPLY = 8


class ChatClientError(RuntimeError):
    """The model could not be reached, or replied unusably, for one turn."""


@dataclass(frozen=True)
class ChatReply:
    text: str = ""
    tool_calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    model: str = ""


class FakeChatClient:
    """Deterministic stand-in.

    `script` is a list of steps consumed in order; a step is either
    {"tool": name, "args": {...}} (one tool call) or {"text": "..."} (a
    final answer). An exhausted or missing script answers "ok" — the same
    promise the other fizz's make: never random, never external.
    """

    name = "fake-chat"

    def __init__(self, script: Optional[List[Dict[str, Any]]] = None) -> None:
        self.script = list(script or [])
        self.replies = 0

    def chat(self, system: str, history: Sequence[dict],
             manifest_items: Sequence[dict]) -> ChatReply:
        self.replies += 1
        step = self.script.pop(0) if self.script else {"text": "ok"}
        if "tool" in step:
            return ChatReply(text="", model=self.name,
                             tool_calls=[(str(step["tool"]), step.get("args") or {})])
        return ChatReply(text=str(step.get("text", "ok")), model=self.name)


class _ChatRole:
    """The chat request shape on top of an agent base's `_obj` transport."""

    def chat(self, system: str, history: Sequence[dict],
             manifest_items: Sequence[dict]) -> ChatReply:
        payload = json.dumps(
            {"history": list(history), "tools": list(manifest_items)},
            ensure_ascii=False,
        )
        obj = self._obj(system, payload)
        if obj is None:
            why = getattr(self, "last_error", None) or "no usable reply"
            raise ChatClientError(f"{self._model}: {why}")
        calls: List[Tuple[str, Dict[str, Any]]] = []
        for one in (obj.get("tool_calls") or [])[:MAX_TOOL_CALLS_PER_REPLY]:
            if (isinstance(one, dict) and isinstance(one.get("name"), str)
                    and isinstance(one.get("arguments") or {}, dict)):
                calls.append((one["name"], one.get("arguments") or {}))
        return ChatReply(text=str(obj.get("text", "") or ""),
                         tool_calls=calls, model=self._model)


class GroqChatClient(_ChatRole, _GroqAgent):
    def __init__(self, model: str = "openai/gpt-oss-120b", **kw):
        super().__init__(model, **kw)


class OllamaChatClient(_ChatRole, _OllamaAgent):
    def __init__(self, model: str = "qwen3:8b", client=None, timeout=None,
                 settings=None):
        super().__init__(model, client=client, timeout=timeout, settings=settings)


def select_chat_client(cfg):
    """Resolve roles.chat like every other job; fake → the stand-in.

    With no `roles.chat` block the provider falls to `llm_provider` — which
    means the shipped profile resolves chat to FAKE until it is configured,
    on purpose (no surprise quota burn from a chat nobody asked for).
    """
    from jester.roles import resolve_role

    r = resolve_role(cfg, "chat")
    if r.kind == "openai":
        return GroqChatClient(model=r.model, settings=r)
    if r.kind == "ollama":
        return OllamaChatClient(model=r.model, settings=r)
    return FakeChatClient()
