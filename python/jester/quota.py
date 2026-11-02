"""R30-pattern daily budget ledger (jester-setup.md §37.13 / v3.10 #6b).

The doc keeps R30's pattern alive as a self-imposed, persisted per-day budget
with a graceful stop — the seam future fetchers (M1.1 Reddit sessions,
M3.1 YouTube) consume before making requests. Day keys roll at midnight PT
per R30's reset convention; usage persists across process restarts.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jester.store import quota_used

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
    Raises QuotaExceeded (spending nothing) when the request doesn't fit.

    ONE STATEMENT DECIDES. This used to read the total, compare it, and then
    write, which is a race whenever two processes hold the same budget: both
    read "0 used", both pass the check, both charge. The statement below only
    inserts or accumulates when the result still fits, and its rowcount is the
    verdict — 0 means "it did not fit" and nothing was written. SQLite
    serialises the writes, so the decision is serialised with them.

    The `INSERT ... SELECT ... WHERE` form is what keeps the first charge
    honest: on a conflict the DO UPDATE's WHERE guards the accumulation, but a
    plain VALUES insert would ignore the budget for a day with no row yet.
    """
    key = day_key(now)
    own_txn = not db.in_transaction
    cur = db.execute(
        "INSERT INTO quota(platform, day_key, used) SELECT ?,?,? WHERE ? <= ? "
        "ON CONFLICT(platform, day_key) DO UPDATE SET used = used + excluded.used "
        "WHERE quota.used + excluded.used <= ?",
        (platform, key, int(units), int(units), int(daily_budget), int(daily_budget)),
    )
    if cur.rowcount != 1:
        # Nothing was charged (the statement matched no row). Ending the empty
        # transaction is ours to do only when the caller did not have one.
        if own_txn:
            db.rollback()
        used = quota_used(db, platform, key)
        raise QuotaExceeded(max(0, int(daily_budget) - used))
    # Read the new total inside the same transaction, so the number returned
    # is the one this call produced rather than a later writer's.
    used = quota_used(db, platform, key)
    db.commit()
    return max(0, int(daily_budget) - used)
