"""Chat-driven editing for Jester.

This package holds the part of the autonomous-curation design that a model
can drive inside the operator console:

- ``schemas.py`` — a hand-rolled strict-subset JSON Schema validator used at
  every tool boundary (stdlib only; see its docstring for why not
  `jsonschema`).
- ``tools.py`` — the tool registry: code-owned descriptions and schemas, a
  read half grounding the model in the real archive, and a write half that
  can ONLY mint proposals through ``jester.edits``. Applying, rejecting,
  reverting and every config promotion are human actions taken in the
  console, never model tool calls.
- ``session.py``  (next step) — the bounded ReAct-style loop that runs the
  model against these tools, and the deterministic FakeChatClient stand-in.

Hard rules, tested: this package never imports ``jester.journal`` or
``jester.signals`` (journal gates and the deterministic acceptance path stay
unreachable from anything a model can call), and it never opens a database
other than the archive the console already opened.
"""
