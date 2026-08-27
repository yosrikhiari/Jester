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

#: The provider key treatment blocks are filed under.
#:
#: A CONSTANT because two sides have to agree on it and they are in different
#: files: `cmd_treat` writes the block, the console's Queue page reads it. The
#: console originally looked up whatever `thresholds.llm_provider` said, which
#: is "fake" in the default profile — so it read nothing, and showed a queue
#: as free to drain while treatment was in fact shut for another 22 minutes.
#:
#: Deliberately not the configured provider: the quota being tracked is Groq's
#: daily token allowance, which is a fact about an account rather than about
#: whichever model role happens to be pointed where.
TREATMENT_PROVIDER = "groq"


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
    """The provider answered; forget any recorded block."""
    ensure_schema(db)
    db.execute(
        "UPDATE llm_quota SET blocked_until=NULL, reason='' WHERE provider=?",
        (provider,),
    )
    db.commit()


def blocked_for(db, provider: str) -> float:
    """Seconds until `provider` is usable again. 0.0 when it is usable now.

    Reading this BEFORE making a call is the point: a treatment run that knows
    it is blocked exits without spending a request, which is what makes it safe
    to schedule frequently.
    """
    ensure_schema(db)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT blocked_until FROM llm_quota WHERE provider=?", (provider,)
    ).fetchone()
    if row is None or not row["blocked_until"]:
        return 0.0
    try:
        until = datetime.fromisoformat(row["blocked_until"])
    except (TypeError, ValueError):
        return 0.0
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = (until - _now()).total_seconds()
    return max(0.0, remaining)


def status(db, provider: str) -> dict:
    """Everything the console needs to explain the current state."""
    ensure_schema(db)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM llm_quota WHERE provider=?", (provider,)
    ).fetchone()
    remaining = blocked_for(db, provider)
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
