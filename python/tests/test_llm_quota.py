"""Scrape and treat are separate jobs on separate clocks.

Fetching is cheap, polite, and bounded by how often we are willing to knock on
someone's door. Treatment is one LLM call per comment, bounded by a quota that
resets on someone else's clock. Running them together meant the expensive half
set the pace for the cheap half.

The rule these pin down hardest: treatment must STOP on an exhausted quota, not
degrade. Every Groq agent falls back to a deterministic stand-in on failure —
right for one bad call, catastrophic under an exhausted quota, because the
whole backlog would be processed into mechanical output and MARKED DONE with no
second chance.
"""

import io
import json
import sqlite3
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from jester import llm_quota
from jester.llm import GroqClient, GroqError, GroqRateLimited
from jester.store import open_db


@pytest.fixture
def db(tmp_path):
    return open_db(str(tmp_path / "q.db"))


def test_an_unseen_provider_is_not_blocked(db):
    assert llm_quota.blocked_for(db, "groq") == 0.0
    assert llm_quota.status(db, "groq")["blocked"] is False


def test_a_block_is_remembered_and_expires(db):
    llm_quota.record_block(db, "groq", 300, reason="tokens per day")
    remaining = llm_quota.blocked_for(db, "groq")
    assert 290 < remaining <= 300
    st = llm_quota.status(db, "groq")
    assert st["blocked"] is True
    assert "tokens per day" in st["reason"]

    # A block in the past is not a block.
    llm_quota.record_block(db, "groq", 0)
    assert llm_quota.blocked_for(db, "groq") == 0.0


def test_clearing_a_block(db):
    llm_quota.record_block(db, "groq", 600)
    assert llm_quota.blocked_for(db, "groq") > 0
    llm_quota.clear_block(db, "groq")
    assert llm_quota.blocked_for(db, "groq") == 0.0
    assert llm_quota.status(db, "groq")["blocked"] is False


def test_recording_twice_replaces_rather_than_duplicates(db):
    llm_quota.record_block(db, "groq", 60, reason="first")
    llm_quota.record_block(db, "groq", 600, reason="second")
    rows = db.execute("SELECT COUNT(*) FROM llm_quota WHERE provider='groq'").fetchone()[0]
    assert rows == 1
    assert llm_quota.status(db, "groq")["reason"] == "second"


def test_a_corrupt_timestamp_blocks_and_says_why(db):
    """V3-02: this used to fail OPEN.

    An unreadable `blocked_until` returned 0.0 seconds — the same answer as
    "no row" — so `blocked_for` said "not blocked" and treatment ran straight
    into the quota it had already been refused by. A brake that reads a
    damaged row as "go" is not a brake. It now blocks for a named window and
    `status()` explains it from the raw row instead of hiding it.
    """
    llm_quota.ensure_schema(db)
    db.execute(
        "INSERT INTO llm_quota(provider, blocked_until) VALUES('groq','not-a-date')"
    )
    db.commit()

    wait = llm_quota.blocked_for(db, "groq")
    assert 0 < wait <= llm_quota.CORRUPT_BLOCK_SECONDS
    assert wait > llm_quota.CORRUPT_BLOCK_SECONDS - 60, "a fresh row blocks for the window"
    st = llm_quota.status(db, "groq")
    assert st["blocked"] is True
    assert "corrupt" in st["reason"].lower()
    assert "not-a-date" in st["reason"], "the offending value stays legible"

    # Explaining it twice must not keep appending to the reason.
    again = llm_quota.status(db, "groq")["reason"]
    assert again == st["reason"]


def test_a_corrupt_row_is_reported_on_a_read_only_connection(tmp_path):
    """Reading a damaged row must not write to it.

    Recording the corruption used to mean an UPDATE ... SET reason from
    `_remaining`, which runs on read paths — `status()` is served by the
    console on a timer, and a console viewer is opened read-only. The read
    then either 500s or turns a report into a mutation. Nothing here may
    issue INSERT/UPDATE/DELETE (or CREATE) at all.
    """
    path = tmp_path / "q.db"
    writable = open_db(str(path))
    llm_quota.ensure_schema(writable)
    writable.execute(
        "INSERT INTO llm_quota(provider, blocked_until, noticed_at) VALUES(?,?,?)",
        ("groq", "not-a-date", datetime.now(timezone.utc).isoformat()),
    )
    writable.commit()
    writable.close()

    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    seen = []
    ro.set_trace_callback(seen.append)
    try:
        wait = llm_quota.blocked_for(ro, "groq")
        st = llm_quota.status(ro, "groq")
    finally:
        ro.close()

    assert wait > 0, "an unreadable stamp still blocks"
    assert st["blocked"] is True
    assert "corrupt" in st["reason"].lower(), "the report explains the damage"
    assert "not-a-date" in st["reason"]

    writes = [
        sql for sql in seen
        if sql.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE"))
    ]
    assert writes == [], writes


def test_a_corrupt_block_counts_down_from_when_it_was_noticed(db):
    """The window is an hour from when the row was noticed, not an hour from
    every read.

    The old code returned the constant on every read, so the reported wait
    never moved and the block never expired on its own: an operator watching
    `status()` saw "1h 0m" forever. Now the hour is a deadline stamped by
    `noticed_at`, and a row with no readable `noticed_at` still fails closed
    for the full window.
    """
    llm_quota.ensure_schema(db)
    now = datetime.now(timezone.utc)

    def corrupt(noticed_at):
        db.execute("DELETE FROM llm_quota")
        db.execute(
            "INSERT INTO llm_quota(provider, blocked_until, reason, noticed_at) "
            "VALUES('groq','not-a-date','tokens per day',?)",
            (noticed_at,),
        )
        db.commit()

    corrupt((now - timedelta(seconds=60)).isoformat())
    fresh = llm_quota.blocked_for(db, "groq")
    assert fresh == pytest.approx(llm_quota.CORRUPT_BLOCK_SECONDS - 60, abs=5)

    corrupt((now - timedelta(seconds=1800)).isoformat())
    older = llm_quota.blocked_for(db, "groq")
    assert older == pytest.approx(llm_quota.CORRUPT_BLOCK_SECONDS - 1800, abs=5)
    assert older < fresh, "the wait counts down as the row ages"
    assert "tokens per day" in llm_quota.status(db, "groq")["reason"], (
        "the stored reason is still explained alongside the damage")

    # Past its window the block expires instead of blocking forever.
    corrupt((now - timedelta(seconds=llm_quota.CORRUPT_BLOCK_SECONDS + 60)).isoformat())
    assert llm_quota.blocked_for(db, "groq") == 0.0
    expired = llm_quota.status(db, "groq")
    assert expired["blocked"] is False
    assert "corrupt" in expired["reason"].lower(), "the damage stays on record"

    # No readable noticed_at: the window cannot count down, so it is the full
    # window (fail closed), not zero.
    corrupt("")
    assert llm_quota.blocked_for(db, "groq") == pytest.approx(
        llm_quota.CORRUPT_BLOCK_SECONDS)


def test_an_empty_timestamp_is_not_a_block(db):
    """Absent and unreadable are different facts: a cleared row (NULL) or an
    empty cell means no block at all."""
    llm_quota.ensure_schema(db)
    db.execute("INSERT INTO llm_quota(provider, blocked_until) VALUES('groq','')")
    db.commit()
    assert llm_quota.blocked_for(db, "groq") == 0.0


def test_human_readable_waits():
    assert llm_quota.human(45) == "45s"
    assert llm_quota.human(125) == "2m 5s"
    assert llm_quota.human(3725) == "1h 2m"


# ---- the client must distinguish the two kinds of failure ----------------


def _raiser(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


def test_an_exhausted_rate_limit_is_its_own_exception():
    """A malformed reply is one bad call. An exhausted quota means everything
    after it would be a substitution — the two call for opposite responses."""
    err = urllib.error.HTTPError(
        "u", 429, "Too Many Requests",
        {"retry-after": "210"},
        io.BytesIO(json.dumps({"error": {"message": "tokens per day"}}).encode()),
    )
    client = GroqClient(api_key="k", opener=_raiser(err), max_retries=0, sleep=lambda s: None)
    with pytest.raises(GroqRateLimited) as e:
        client.chat("m", [])
    assert e.value.retry_after == 210
    # Still a GroqError, so existing handlers keep working.
    assert isinstance(e.value, GroqError)
    # And the status stays visible: it is what an operator greps for.
    assert "429" in str(e.value)


def test_a_non_429_failure_is_not_a_rate_limit():
    err = urllib.error.HTTPError(
        "u", 500, "Server Error", {}, io.BytesIO(b'{"error":{"message":"boom"}}'))
    client = GroqClient(api_key="k", opener=_raiser(err), max_retries=0, sleep=lambda s: None)
    with pytest.raises(GroqError) as e:
        client.chat("m", [])
    assert not isinstance(e.value, GroqRateLimited)


def test_the_reset_window_is_not_clamped_like_a_retry_delay():
    """`_retry_delay` caps at 30s so one comment cannot stall a run. The RESET
    feeds a decision about the NEXT run, and a daily quota really does reset
    hours away — clamping it would send treatment straight back to be refused
    again, forever."""
    err = urllib.error.HTTPError(
        "u", 429, "Too Many Requests", {"retry-after": "43200"},
        io.BytesIO(b'{"error":{"message":"tokens per day"}}'))
    client = GroqClient(api_key="k", opener=_raiser(err), max_retries=0, sleep=lambda s: None)
    with pytest.raises(GroqRateLimited) as e:
        client.chat("m", [])
    assert e.value.retry_after == 43200


def test_an_agent_records_the_rate_limit_separately_from_other_fallbacks():
    """The caller must be able to tell 'we substituted once' from 'the quota
    is gone'."""
    from jester.llm import GroqLLM

    err = urllib.error.HTTPError(
        "u", 429, "Too Many Requests", {"retry-after": "120"},
        io.BytesIO(b'{"error":{"message":"tokens per day"}}'))
    agent = GroqLLM(
        client=GroqClient(api_key="k", opener=_raiser(err), max_retries=0,
                          sleep=lambda s: None))
    agent._obj("system", "user")
    assert agent.rate_limited == 1
    assert agent.retry_after == 120
    # It is also counted as a fallback, because output WAS substituted.
    assert agent.fallbacks == 1
