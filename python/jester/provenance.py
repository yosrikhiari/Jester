"""What was asked and what answered (S-08).

`runs.models_used` records which role was configured with which model, and
`runs.env` records the machine. Neither says what instruction produced an idea.
This module adds the missing half: one row per LLM call, keyed by the run, with
a hash of the exact prompt and the exact reply.

Why hashes rather than text by default: a prompt carries a scraped comment, and
the archive should not store every body twice just to answer "was it the same
question?". The hash answers that, and `JESTER_LLM_CALL_TEXT=1` keeps the text
too for the cases where a reader needs the words.

The recorder is attached to agents by `attach`, and lives outside the run's own
code so a call site does not have to remember to file anything. `_Agent` calls
it from the one place every send passes through.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

TEXT_ENV = "JESTER_LLM_CALL_TEXT"
KEEP_DAYS_ENV = "JESTER_LLM_CALLS_KEEP_DAYS"
DEFAULT_KEEP_DAYS = 30
_KEEP_FALSY = ("", "0", "false", "no", "off")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def prompt_of(system, user) -> str:
    """The exact string whose hash is recorded. One separator, always."""
    return f"{system or ''}\n\n{user or ''}"


def attach(db, run_id, *agents):
    """File every call these agents make under `run_id`.

    Returns the recorder, which is also what a test can call directly. Agents
    without a recorder (the deterministic stand-ins) never reach it: they make
    no call, so there is nothing to record.
    """
    def record(*, role, model, digest, prompt, response, latency_ms, error=None):
        record_call(
            db, run_id, role=role, model=model, digest=digest, prompt=prompt,
            response=response, latency_ms=latency_ms, error=error)

    for agent in agents:
        setattr(agent, "_recorder", record)
    return record


def record_call(db, run_id, *, role, model="", digest=None, prompt="",
                response=None, latency_ms=None, error=None) -> None:
    keep_text = os.environ.get(TEXT_ENV, "").strip().lower() not in _KEEP_FALSY
    db.execute(
        "INSERT INTO llm_calls (run_id, created_utc, role, model, digest,"
        " prompt_sha256, response_sha256, prompt_chars, response_chars,"
        " latency_ms, ok, error, prompt_text, response_text)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            _now(),
            role,
            model,
            digest,
            sha256_text(prompt),
            sha256_text(response) if response is not None else None,
            len(prompt or ""),
            len(response or "") if response is not None else None,
            None if latency_ms is None else int(round(latency_ms)),
            1 if error is None else 0,
            error,
            prompt if keep_text else None,
            response if keep_text else None,
        ),
    )
    db.commit()


def calls_for(db, run_id, limit=None) -> list:
    """Every recorded call for one run, oldest first."""
    sql = (
        "SELECT id, run_id, created_utc, role, model, digest, prompt_sha256,"
        " response_sha256, prompt_chars, response_chars, latency_ms, ok, error,"
        " prompt_text, response_text FROM llm_calls WHERE run_id=?"
        " ORDER BY id")
    params = (run_id,)
    if limit:
        sql += " LIMIT ?"
        params += (int(limit),)
    cur = db.execute(sql, params)
    # dicts, not sqlite3.Row: open_db leaves the default tuple factory on the
    # migration path, and a reader of the pack should not depend on which
    # factory the connection happens to carry.
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def prune_calls(db, keep_days=None, now=None) -> int:
    """Drop calls older than the retention window. Returns how many went.

    A bound rather than an expiry policy: the hashes are small, but the table
    grows once per model call forever otherwise. `0` (or a negative value)
    keeps everything, which is a choice an operator can make explicitly.
    """
    if keep_days is None:
        raw = os.environ.get(KEEP_DAYS_ENV, "").strip()
        try:
            keep_days = int(raw) if raw else DEFAULT_KEEP_DAYS
        except ValueError:
            # One typo must not abort every run before its first call.
            keep_days = DEFAULT_KEEP_DAYS
    if int(keep_days) <= 0:
        return 0
    anchor = now or datetime.now(timezone.utc)
    cutoff = (anchor - timedelta(days=int(keep_days))).isoformat(timespec="seconds")
    cur = db.execute("DELETE FROM llm_calls WHERE created_utc < ?", (cutoff,))
    db.commit()
    return cur.rowcount or 0
