"""Offline coverage for the chat clients' live-transport shapes.

The live classes must never be exercised against a real provider, but the
REQUEST/response plumbing - what gets serialized and what comes back - IS
testable with a stubbed `_obj`: attributes shadow methods in Python, so
the transport is the test's, body bytes and all. The selectors are tested
through `jester.roles.resolve_role`, patchable without network.
"""
import json
import types

import pytest

from jester.chat.client import (
    ChatClientError,
    ChatReply,
    FakeChatClient,
    GroqChatClient,
    OllamaChatClient,
    select_chat_client,
    select_specifier_client,
)


class _StubTransport:
    """Stands in for the agent bases' _obj(None, or a reply dict)."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, system, payload, temperature=0.4):
        self.calls += 1
        self.system = system
        self.payload = payload
        if callable(self.reply):
            return self.reply()
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _client_with(cls, stub, **kw):
    """A live client whose transport is the stub."""
    c = cls(**kw)
    c._obj = stub  # instance attribute shadows the transport method
    return c


def test_chat_role_round_trips_a_reply_with_tool_calls():
    stub = _StubTransport({
        "text": "I looked and propose this",
        "tool_calls": [
            {"name": "propose_threshold", "arguments": {"key": "k", "value": 1}},
            "not-a-dict",
            "extra-one".join({}),  # junk we must not crash on
            17,
        ],
    })
    c = _client_with(GroqChatClient, stub, model="openai/gpt-oss-120b")
    reply = c.chat("You are the chat.", [
        {"role": "user", "content": "hello"},
        {"role": "chat", "content": "earlier"},
    ], [{"name": "propose_threshold", "type": "function"}])
    assert stub.payload == json.dumps(
        {"history": [{"role": "user", "content": "hello"},
                     {"role": "chat", "content": "earlier"}],
         "tools": [{"name": "propose_threshold", "type": "function"}]},
        ensure_ascii=False)
    assert stub.calls == 1
    assert isinstance(reply, ChatReply) and reply.text == "I looked and propose this"
    # junk entries dropped, the well-formed call kept
    assert reply.tool_calls == [("propose_threshold", {"key": "k", "value": 1})]


def test_chat_role_keeps_at_most_eight_tool_calls():
    eight_ok = [{"name": f"t{i}", "arguments": {}} for i in range(8)]
    stub = _StubTransport({"text": "", "tool_calls": eight_ok +
                           [{"name": "too-many", "arguments": {}}]})
    c = _client_with(OllamaChatClient, stub, client=object(), timeout=5.0)
    reply = c.chat("s", [], [])
    assert len(reply.tool_calls) == 8  # the ninth is dropped, not a crash
    assert all(n != "too-many" for n, _ in reply.tool_calls)


def test_chat_role_raises_when_the_transport_says_no():
    stub = _StubTransport(None)
    c = _client_with(GroqChatClient, stub)
    c.last_error = "malformed reply (not a JSON object)"
    with pytest.raises(ChatClientError, match="malformed reply"):
        c.chat("s", [], [])


def test_selectors_resolve_every_known_provider_kind(monkeypatch):
    from jester import roles as roles_mod
    from jester.roles import RoleSettings

    def _fake_resolve(cfg, role):
        return RoleSettings(
            role=role, provider="stub", kind=_next["kind"],
            model=_next["model"])

    _next = {"kind": "openai", "model": "openai/gpt-oss-120b"}
    monkeypatch.setattr(roles_mod, "resolve_role", _fake_resolve)
    assert isinstance(select_chat_client(vars()), GroqChatClient)
    assert isinstance(select_specifier_client(vars()), GroqChatClient)

    _next = {"kind": "ollama", "model": "qwen3:8b"}
    monkeypatch.setattr(roles_mod, "resolve_role", _fake_resolve)
    assert isinstance(select_chat_client(vars()), OllamaChatClient)
    assert isinstance(select_specifier_client(vars()), OllamaChatClient)

    _next = {"kind": "fake", "model": "fake"}
    monkeypatch.setattr(roles_mod, "resolve_role", _fake_resolve)
    assert isinstance(select_chat_client(vars()), FakeChatClient)
    assert isinstance(select_specifier_client(vars()), FakeChatClient)


def test_fake_client_script_semantics_are_the_contract():
    c = FakeChatClient(script=[{"tool": "list_sources", "args": {"a": 1}},
                               {"text": "final"}])
    first = c.chat("s", [], [])
    assert first.text == "" and first.tool_calls == [("list_sources", {"a": 1})]
    second = c.chat("s", [], [])
    assert second.text == "final" and second.tool_calls == []
    # exhausted script: never random — "ok"
    third = c.chat("s", [], [])
    assert third.text == "ok"
    assert c.replies == 3
