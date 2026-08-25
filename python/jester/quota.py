"""R30-pattern daily budget ledger (jester-setup.md §37.13 / v3.10 #6b).

The doc keeps R30's pattern alive as a self-imposed, persisted per-day budget
with a graceful stop — the seam future fetchers (M1.1 Reddit sessions,
M3.1 YouTube) consume before making requests. Day keys roll at midnight PT
per R30's reset convention; usage persists across process restarts.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jester.store import quota_add, quota_used

PT = ZoneInfo("America/Los_Angeles")


class QuotaExceeded(RuntimeError):
    """Raised by spend() when the day's budget would be blown; nothing is spent."""

    def __init__(self, remaining: int):
        super().__init__(f"daily budget exhausted ({remaining} unit(s) remain)")
        self.remaining = remaining


def day_key(now=None) -> str:
    """Local-date key in PT (R30: reset matches the platform's own schedule)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(PT).date().isoformat()


def quota_remaining(db, platform: str, daily_budget: int, now=None) -> int:
    return max(0, daily_budget - quota_used(db, platform, day_key(now)))


def spend(db, platform: str, units: int, *, daily_budget: int, now=None) -> int:
    """Charge units against today's budget; returns units remaining after.
    Raises QuotaExceeded (spending nothing) when the request doesn't fit."""
    key = day_key(now)
    used = quota_used(db, platform, key)
    if used + units > daily_budget:
        raise QuotaExceeded(max(0, daily_budget - used))
    quota_add(db, platform, key, units)
    return daily_budget - used - units
