"""What each source has left: a rate-limit cool-down and a daily request budget.

The scraper walks a chain of sources (`run.collect_or_fall_back`). Before this
module it moved on only when a source had nothing new, and a source answering
"429, slow down" after every retry was filed as a FAILED run -- which stops the
chain, since "could not look" must not be buried under a quiet fallback. But a
rate limit is not a broken collector: the board is up and asking for less.
The honest reply is to stop asking it, say so, and read the next board.

Two limits, both kept per source in `signal_source_state`:

* **a cool-down** -- set when a source rate-limits us: its `Retry-After`, or
  an hour. Until it ends the chain skips the source without a single request,
  because retrying a 429 early is how a rate limit becomes a ban.
* **a daily budget** -- our own ceiling on requests, set per board as
  `daily_budget` (none of the boards publishes a number; Himalayas says its
  data refreshes daily, so polling it harder buys nothing). Spent, the source
  sits out until the next UTC day.

What is NOT a limit: a block page, a non-JSON body, a 401/403. Those mean we
may be locked out, and switching past them would hide it -- they stay failures.

Only whole sources rotate. Nothing here rotates addresses, keys or accounts to
get around one source's limit; the limit is the source's answer, and the
answer is respected.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

STATE_TABLE = "signal_source_state"

STATE_DDL = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
  source TEXT PRIMARY KEY,
  limited_until TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL DEFAULT '',
  requests INTEGER NOT NULL DEFAULT 0
);
"""

#: How long a rate-limited source sits out when it does not say.
DEFAULT_COOLDOWN = timedelta(hours=1)

#: `SourceError.status` values that mean "you have asked enough", not "broken".
RATE_LIMITED = "429"
BUDGET_SPENT = "budget"


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def ensure_state_table(db: sqlite3.Connection) -> None:
    db.executescript(STATE_DDL)
    db.commit()


def is_limit(exc) -> bool:
    return getattr(exc, "status", "") in (RATE_LIMITED, BUDGET_SPENT)


def _state(db: sqlite3.Connection, source: str) -> dict:
    ensure_state_table(db)
    row = db.execute(f"SELECT limited_until, reason, day, requests FROM {STATE_TABLE} "
                     "WHERE source = ?", (source,)).fetchone()
    if row is None:
        return {"limited_until": "", "reason": "", "day": "", "requests": 0}
    return dict(zip(("limited_until", "reason", "day", "requests"), tuple(row)))


def _save(db: sqlite3.Connection, source: str, state: dict) -> None:
    db.execute(f"INSERT OR REPLACE INTO {STATE_TABLE} "
               "(source, limited_until, reason, day, requests) VALUES (?,?,?,?,?)",
               (source, state["limited_until"], state["reason"], state["day"],
                state["requests"]))
    db.commit()


def requests_today(db: sqlite3.Connection, source: str, now: Optional[datetime] = None) -> int:
    state = _state(db, source)
    return state["requests"] if state["day"] == _now(now).date().isoformat() else 0


def budget_left(db: sqlite3.Connection, source, now: Optional[datetime] = None) -> Optional[int]:
    """Requests `source` may still make today, or None when it has no budget."""
    budget = getattr(source, "daily_budget", 0) or 0
    if not budget:
        return None
    return max(0, budget - requests_today(db, source.name, now))


def blocked(db: sqlite3.Connection, source, now: Optional[datetime] = None) -> str:
    """Why `source` must not be asked right now, or "" when it may be."""
    now = _now(now)
    state = _state(db, source.name)
    if state["limited_until"] and state["limited_until"] > _iso(now):
        return f"cooling down until {state['limited_until']} ({state['reason']})"
    if budget_left(db, source, now) == 0:
        return (f"daily budget of {source.daily_budget} requests spent; "
                f"resets at {_iso(_next_day(now))}")
    return ""


def record_requests(db: sqlite3.Connection, name: str, count: int,
                    now: Optional[datetime] = None) -> None:
    if not count:
        return
    today = _now(now).date().isoformat()
    state = _state(db, name)
    state["requests"] = (state["requests"] if state["day"] == today else 0) + count
    state["day"] = today
    _save(db, name, state)


def mark_limited(db: sqlite3.Connection, name: str, exc, now: Optional[datetime] = None) -> str:
    """Start the source's cool-down after `exc`; returns when it ends."""
    now = _now(now)
    if getattr(exc, "status", "") == BUDGET_SPENT:
        until = _next_day(now)
    else:
        wait = getattr(exc, "retry_after", 0) or 0
        until = now + (timedelta(seconds=wait) if wait > 0 else DEFAULT_COOLDOWN)
    state = _state(db, name)
    state["limited_until"], state["reason"] = _iso(until), str(exc)[:200]
    _save(db, name, state)
    return state["limited_until"]


def _next_day(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def retry_after_seconds(value) -> float:
    """A `Retry-After` header as seconds. It may also be an HTTP date; anything
    unreadable is 0, which means "use the default cool-down"."""
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, (parsedate_to_datetime(value) - _now()).total_seconds())
    except (TypeError, ValueError, IndexError):
        return 0.0
