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
import urllib.error

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


def test_a_corrupt_timestamp_does_not_block_forever(db):
    """Failing open is right here: a bad row must not make treatment
    permanently refuse to run."""
    llm_quota.ensure_schema(db)
    db.execute(
        "INSERT INTO llm_quota(provider, blocked_until) VALUES('groq','not-a-date')"
    )
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
