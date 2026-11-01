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
CORRUPT_BLOCK_SECONDS = 3600

#: Stamped into `reason` when `blocked_until` could not be parsed. Kept as a
#: constant so the rewrite below is idempotent: `status()` may be called many
#: times, and the explanation must not accumulate copies of itself.
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
    """Every block still in force, longest first, as `status` dicts."""
    ensure_schema(db)
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
    own = _remaining(db, provider)
    if "/" in provider:
        return max(own, _remaining(db, provider.split("/", 1)[0]))
    return own


def _remaining(db, provider: str) -> float:
    ensure_schema(db)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT blocked_until, reason FROM llm_quota WHERE provider=?", (provider,)
    ).fetchone()
    if row is None or not row["blocked_until"]:
        # No row, or a cleared one: nothing was ever recorded against this
        # provider, which is not the same fact as a row we cannot read.
        return 0.0
    try:
        until = datetime.fromisoformat(row["blocked_until"])
    except (TypeError, ValueError):
        # V3-02. The row exists and says "blocked"; only the instant is
        # unreadable. Fail CLOSED for a fixed window and leave the reason on
        # file saying why, so status() explains it instead of the operator
        # finding "not blocked" next to an expired-looking stamp.
        _note_corrupt(db, provider, row["blocked_until"], row["reason"])
        return float(CORRUPT_BLOCK_SECONDS)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = (until - _now()).total_seconds()
    return max(0.0, remaining)


def _note_corrupt(db, provider: str, value, reason) -> None:
    """Record, once, that `blocked_until` could not be read.

    Idempotent by construction: a reason that already names the corruption is
    left alone, so reading `status()` in a loop does not grow it.
    """
    note = (f"{CORRUPT_REASON_PREFIX} ({value!r}); treating as blocked for "
            f"{CORRUPT_BLOCK_SECONDS}s")
    existing = (reason or "").strip()
    if CORRUPT_REASON_PREFIX in existing:
        return
    db.execute(
        "UPDATE llm_quota SET reason=? WHERE provider=?",
        ((note + (f"; was: {existing}" if existing else ""))[:400], provider),
    )
    db.commit()


def status(db, provider: str) -> dict:
    """Everything the console needs to explain the current state."""
    ensure_schema(db)
    db.row_factory = sqlite3.Row
    # Read AFTER `blocked_for`: an unreadable `blocked_until` is explained by
    # rewriting the reason (see _note_corrupt), and reporting the pre-rewrite
    # row would hide the very sentence this exists to surface.
    remaining = blocked_for(db, provider)
    row = db.execute(
        "SELECT * FROM llm_quota WHERE provider=?", (provider,)
    ).fetchone()
    return {
        "provider": provider,
        "blocked": remaining > 0,
        "seconds_remaining": round(remaining, 1),
        "blocked_until": (row["blocked_until"] if row else None) or None,
        "reason": (row["reason"] if row else "") or "",
        "noticed_at": (row["noticed_at"] if row else None),
    }


def human(seconds: float) -> str:
    """`3725.0` -> `1h 2m`. Used in operator-facing messages."""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
