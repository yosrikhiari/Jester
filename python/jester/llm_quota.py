"""Remember that the LLM said no, and until when.

WHY THIS EXISTS. Scraping and treatment have completely different costs.
Fetching is cheap, polite and bounded by how often we are willing to knock on
someone's door. Extraction is one LLM call per comment, bounded by a quota that
resets on someone else's clock. Running them on the same schedule means the
expensive half sets the pace for the cheap half — and worse, that a
quota-exhausted tick still walks the whole source list and then throws the
result away.

So the two are split, and this is the piece that lets the treatment half know
when it is worth waking up.

THE FAILURE THIS PREVENTS. Every Groq agent falls back to a deterministic
stand-in when the API is unreachable — a sensible default for one bad call
during a healthy run. Under an exhausted quota it is a disaster: the pipeline
happily processes the entire backlog into mechanical output, marks it done, and
the material is gone. There is no second chance, because the queue rows are
marked processed. Treatment must STOP on exhaustion, not degrade.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_quota (
    provider     TEXT PRIMARY KEY,
    blocked_until TEXT,        -- RFC3339 UTC; NULL means "not blocked"
    reason        TEXT,
    noticed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

#: A 429 whose reset is further away than this is treated as a real quota
#: exhaustion rather than a momentary throttle. Groq's per-minute buckets clear
#: in seconds; the daily one does not.
LONG_BLOCK_SECONDS = 120

#: How long a row with an unreadable `blocked_until` blocks for.
#:
#: The value cannot be trusted, so neither "it expires now" nor "it expires
#: next year" can be derived from it. Before this, corruption read as 0.0
#: seconds — the same answer as "no row" — so `blocked_for` said "not blocked"
#: and treatment ran straight back into the quota it had already been refused
#: by. A brake that reads a damaged row as "go" is not a brake. An hour is
#: long enough that a broken row cannot burn a day's quota, short enough that
#: an operator who fixes the row is not waiting forever; `clear_block` (a
#: successful call) and `record_block` both replace it immediately.
#:
#: The hour is measured from `noticed_at`, not from every read: it is a
#: deadline, so the reported wait counts down and the block expires on its own.
CORRUPT_BLOCK_SECONDS = 3600

#: Stamped into the reason `status()` reports when `blocked_until` could not
#: be parsed. Kept as a constant so the sentence is one thing in one place.
CORRUPT_REASON_PREFIX = "blocked_until is corrupt"

#: The key blocks used to be filed under, for every model at once.
#:
#: Groq's free tier counts each MODEL separately, so one model running out
#: said nothing about the others, yet a block filed here stopped every job,
#: including the extractor on local Ollama. Blocks are now filed per provider
#: and model (`quota_key`). A row under this old key is still honoured, as a
#: block on every model of that provider, until it expires or is cleared.
TREATMENT_PROVIDER = "groq"


def quota_key(settings):
    """Where a job's quota block is filed: `provider/model` for a metered
    (OpenAI-compatible) provider, None for one with no quota (Ollama, fake)."""
    if settings is None or getattr(settings, "kind", None) != "openai":
        return None
    return f"{settings.provider}/{settings.model}"


def ensure_schema(db) -> None:
    db.executescript(SCHEMA)
    db.commit()


def _now():
    return datetime.now(timezone.utc)


def record_block(db, provider: str, retry_after_seconds: float, reason: str = "") -> str:
    """Remember that `provider` is unavailable for `retry_after_seconds`.

    Returns the RFC3339 instant it becomes available again.
    """
    ensure_schema(db)
    seconds = max(0.0, float(retry_after_seconds or 0))
    until = _now() + timedelta(seconds=seconds)
    stamp = until.isoformat()
    db.execute(
        "INSERT INTO llm_quota(provider, blocked_until, reason, noticed_at) "
        "VALUES(?,?,?,?) "
        "ON CONFLICT(provider) DO UPDATE SET "
        "blocked_until=excluded.blocked_until, reason=excluded.reason, "
        "noticed_at=excluded.noticed_at",
        (provider, stamp, (reason or "")[:400], _now().isoformat()),
    )
    db.commit()
    return stamp


def clear_block(db, provider: str) -> None:
    """The provider answered; forget any recorded block.

    Clearing a `provider/model` key also clears an old provider-wide row:
    a model of that provider just answered, so the old block is stale.
    """
    ensure_schema(db)
    keys = [provider] + ([provider.split("/", 1)[0]] if "/" in provider else [])
    db.executemany(
        "UPDATE llm_quota SET blocked_until=NULL, reason='' WHERE provider=?",
        [(k,) for k in keys],
    )
    db.commit()


def active_blocks(db) -> list:
    """Every block still in force, longest first, as `status` dicts.

    A read (the console polls it): no schema creation, no DML. "No table" is
    "no blocks", which is the truth on an archive that never recorded one.
    """
    if not _schema_present(db):
        return []
    keys = [r[0] for r in db.execute(
        "SELECT provider FROM llm_quota WHERE blocked_until IS NOT NULL")]
    out = [status(db, k) for k in keys]
    out = [st for st in out if _remaining(db, st["provider"]) > 0]
    return sorted(out, key=lambda st: -st["seconds_remaining"])


def blocked_roles(db, settings_by_role: dict) -> dict:
    """{role: (key, seconds)} for each job whose model is known to be out of
    quota. A job on a provider with no quota is never in it."""
    out = {}
    for role, settings in settings_by_role.items():
        key = quota_key(settings)
        if key:
            wait = blocked_for(db, key)
            if wait > 0:
                out[role] = (key, wait)
    return out


def settle(db, agents_by_role: dict, run_id: str = "") -> dict:
    """After a run: record a block for each model that ran out, and clear the
    block of each model that answered. {role: key} of the ones that ran out.

    Two jobs can share a model (synthesizer and critic on gpt-oss-120b); a
    model that ran out for one is not cleared because the other answered
    earlier in the same run.
    """
    limited, answered = {}, set()
    for role, agent in agents_by_role.items():
        key = quota_key(getattr(agent, "settings", None))
        if not key:
            continue
        if int(getattr(agent, "rate_limited", 0) or 0):
            limited[role] = key
            record_block(
                db, key,
                agent.retry_after if getattr(agent, "retry_after", None) is not None else 3600,
                reason=f"{role}: {agent.rate_limited} call(s) lost to the rate limit"
                       + (f" during {run_id}" if run_id else ""),
            )
        elif int(getattr(agent, "calls", 0) or 0) > int(getattr(agent, "fallbacks", 0) or 0):
            answered.add(key)
    for key in answered - set(limited.values()):
        clear_block(db, key)
    return limited


def blocked_for(db, provider: str) -> float:
    """Seconds until `provider` (a quota key) is usable again; 0.0 when now.

    Reading this BEFORE making a call is the point: a treatment run that knows
    it is blocked exits without spending a request, which is what makes it safe
    to schedule frequently. A `provider/model` key is also blocked by an
    old provider-wide row (see TREATMENT_PROVIDER).
    """
    waits = [_remaining_from_row(row) for row in _rows_for(db, provider)]
    return max(waits) if waits else 0.0


def _remaining(db, provider: str) -> float:
    """Seconds left on this exact key's own row (no provider-wide fallback)."""
    row = _row(db, provider)
    return _remaining_from_row(row) if row is not None else 0.0


def _remaining_from_row(row) -> float:
    value = row["blocked_until"]
    if not value:
        # No row, or a cleared one: nothing was ever recorded against this
        # provider, which is not the same fact as a row we cannot read.
        return 0.0
    until = _parse_stamp(value)
    if until is None:
        # V3-02. The row exists and says "blocked"; only the instant is
        # unreadable. Fail CLOSED for a fixed window, measured from when the
        # row was noticed so the wait counts down and the block expires.
        return _corrupt_remaining(row["noticed_at"])
    return max(0.0, (until - _now()).total_seconds())


def _corrupt_remaining(noticed_at) -> float:
    """The rest of the corrupt-block window, counting down from `noticed_at`.

    With no readable `noticed_at` there is nothing to count from, so the full
    window is reported: a row we cannot date must not read as "go".
    """
    noticed = _parse_stamp(noticed_at) if noticed_at else None
    if noticed is None:
        return float(CORRUPT_BLOCK_SECONDS)
    return max(0.0, CORRUPT_BLOCK_SECONDS - (_now() - noticed).total_seconds())


def _parse_stamp(value):
    """An ISO-8601/SQLite stamp in UTC, or None when it cannot be read.

    SQLite's `datetime('now')` default writes a naive UTC stamp, so a value
    with no offset is read as UTC — the same clock every writer here uses.
    """
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _is_corrupt(row) -> bool:
    value = row["blocked_until"]
    return bool(value) and _parse_stamp(value) is None


def _schema_present(db) -> bool:
    """Whether `llm_quota` exists, WITHOUT creating it.

    The read path must not write — `status()` is served by the console on a
    timer and the viewer opens the archive read-only — so the reads below ask
    the catalogue instead of calling `ensure_schema`.
    """
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_quota'"
    ).fetchone()
    return row is not None


def _row(db, provider: str):
    """This key's row, or None. Read-only (see `_schema_present`)."""
    db.row_factory = sqlite3.Row
    if not _schema_present(db):
        return None
    return db.execute(
        "SELECT * FROM llm_quota WHERE provider=?", (provider,)
    ).fetchone()


def _rows_for(db, provider: str) -> list:
    """The rows that can block `provider`: its own, then a provider-wide one."""
    keys = [provider]
    if "/" in provider:
        keys.append(provider.split("/", 1)[0])
    rows = []
    for key in keys:
        row = _row(db, key)
        if row is not None:
            rows.append(row)
    return rows


def _corrupt_reason(row) -> str:
    """The explanation `status()` reports for an unreadable stamp.

    Computed from the raw row every time and never written back: a read that
    mutates the row it is describing is not a read, and the console alone
    would have been rewriting this on every poll.
    """
    note = (f"{CORRUPT_REASON_PREFIX} ({row['blocked_until']!r}); treated as "
            f"blocked for up to {CORRUPT_BLOCK_SECONDS}s from noticed_at")
    existing = (row["reason"] or "").strip()
    if existing and CORRUPT_REASON_PREFIX not in existing:
        note += f"; was: {existing}"
    return note


def status(db, provider: str) -> dict:
    """Everything the console needs to explain the current state.

    Pure read (V3-02): a corrupt `blocked_until` is explained in the returned
    `reason`, from the raw row, and nothing is written. The winner is the row
    that actually blocks — own first, then the old provider-wide one.
    """
    row, remaining = None, 0.0
    for candidate in _rows_for(db, provider):
        wait = _remaining_from_row(candidate)
        if row is None or wait > remaining:
            row, remaining = candidate, wait
    reason = (row["reason"] if row is not None else "") or ""
    if row is not None and _is_corrupt(row):
        reason = _corrupt_reason(row)
    return {
        "provider": provider,
        "blocked": remaining > 0,
        "seconds_remaining": round(remaining, 1),
        "blocked_until": (row["blocked_until"] if row is not None else None) or None,
        "reason": reason,
        "noticed_at": (row["noticed_at"] if row is not None else None),
    }


def human(seconds: float) -> str:
    """`3725.0` -> `1h 2m`. Used in operator-facing messages."""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
