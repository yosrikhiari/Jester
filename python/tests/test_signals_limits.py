"""The orchestrator's limits: a rate-limited or spent source sits out, and the
chain moves on to the next board instead of failing or hammering it.

Offline throughout: a fake opener answers 429 on demand, and a fixed clock
decides when a cool-down ends.
"""
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from jester import signals as sig
from jester.signals import limits
from jester.signals import run as sigrun
from jester.signals.filters import load_rules
from jester.signals.sources import SourceError, available, get_source

NOON = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
JOB = {"guid": "https://himalayas.app/companies/acme/jobs/python-dev",
       "title": "Senior Python Developer", "companyName": "Acme",
       "employmentType": "Contractor", "parentCategories": ["Developer"],
       "pubDate": 1790195571, "description": "Contract role building API integrations."}


class Opener:
    """Answers one listing per request; a query named in `limited` gets 429."""

    def __init__(self, limited=(), retry_after="120", total=1):
        self.limited, self.retry_after, self.urls = set(limited), retry_after, []
        self.total = total

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        if any(f"q={q}&" in req.full_url for q in self.limited):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                         {"Retry-After": self.retry_after}, None)
        return io.BytesIO(json.dumps({"jobs": [JOB], "totalCount": self.total}).encode())


def _board(opener, name="himalayas"):
    return get_source(name, opener=opener, sleep=lambda s: None)


@pytest.fixture
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def rules():
    return load_rules("config/hiring_rules.yaml")


# ---- the state -------------------------------------------------------------

def test_a_budget_is_counted_per_day(db):
    board = _board(Opener())
    assert limits.budget_left(db, board, NOON) == 600
    limits.record_requests(db, "himalayas", 590, NOON)
    assert limits.budget_left(db, board, NOON) == 10
    limits.record_requests(db, "himalayas", 10, NOON)
    assert "daily budget of 600 requests spent" in limits.blocked(db, board, NOON)
    # A new UTC day is a new budget.
    assert limits.blocked(db, board, NOON + timedelta(days=1)) == ""
    limits.record_requests(db, "himalayas", 3, NOON + timedelta(days=1))
    assert limits.requests_today(db, "himalayas", NOON + timedelta(days=1)) == 3


def test_a_source_without_a_budget_is_never_spent(db):
    hn = get_source("hackernews-hiring")
    limits.record_requests(db, hn.name, 10_000, NOON)
    assert limits.budget_left(db, hn, NOON) is None
    assert limits.blocked(db, hn, NOON) == ""


def test_a_cool_down_follows_retry_after_and_then_ends(db):
    board = _board(Opener())
    until = limits.mark_limited(db, "himalayas",
                                SourceError("HTTP 429", status="429", retry_after=90), NOON)
    assert until == "2026-09-28T12:01:30+00:00"
    assert limits.blocked(db, board, NOON).startswith("cooling down until 2026-09-28T12:01:30")
    assert limits.blocked(db, board, NOON + timedelta(minutes=2)) == ""


def test_without_retry_after_the_cool_down_is_an_hour(db):
    until = limits.mark_limited(db, "jobicy", SourceError("HTTP 429", status="429"), NOON)
    assert until == "2026-09-28T13:00:00+00:00"


def test_a_spent_budget_sits_out_until_midnight_utc(db):
    until = limits.mark_limited(db, "remotive", SourceError("spent", status="budget"), NOON)
    assert until == "2026-09-29T00:00:00+00:00"


def test_only_rate_limits_and_budgets_are_limits():
    assert limits.is_limit(SourceError("x", status="429"))
    assert limits.is_limit(SourceError("x", status="budget"))
    # A block page or a 403 may mean we are locked out: a failure, not a limit.
    assert not limits.is_limit(SourceError("x", status="403"))
    assert not limits.is_limit(SourceError("non-JSON"))


@pytest.mark.parametrize("value, seconds", [("120", 120.0), ("", 0.0), (None, 0.0),
                                            ("soon", 0.0), ("-5", 0.0)])
def test_retry_after_is_read_as_seconds(value, seconds):
    assert limits.retry_after_seconds(value) == seconds


def test_retry_after_may_be_an_http_date():
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    wait = limits.retry_after_seconds(later.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert 500 < wait <= 600


def test_the_budgets_are_listed_with_the_sources():
    budgets = {s["name"]: s["daily_budget"] for s in available()}
    assert budgets["remotive"] == 4, "Remotive's own published allowance"
    assert budgets["himalayas"] and budgets["jobicy"]
    assert budgets["hackernews"] == 0


# ---- the board -------------------------------------------------------------

def test_a_board_stops_asking_when_its_budget_is_spent():
    opener = Opener(total=5000)
    board = _board(opener)
    board.budget = 2
    with pytest.raises(SourceError) as exc:
        list(board.search("engineer", limit=1000))
    assert exc.value.status == "budget"
    assert board.requests == 2 and len(opener.urls) == 2


def test_a_429_carries_the_wait_it_asked_for():
    board = _board(Opener(limited=["engineer"], retry_after="300"))
    with pytest.raises(SourceError) as exc:
        list(board.search("engineer", limit=20))
    assert exc.value.status == "429" and exc.value.retry_after == 300.0
    assert board.requests == 3, "the retries are requests too"


# ---- one run ---------------------------------------------------------------

def test_a_rate_limited_run_keeps_what_it_read_and_stops_asking(db, rules):
    opener = Opener(limited=["engineer"])
    row = sigrun.collect(db, source=_board(opener), rules=rules,
                         queries=["developer", "engineer", "software"], limit_per_query=20)
    assert row["status"] == "limited"
    kept = db.execute("SELECT COUNT(*) FROM problem_signal "
                      "WHERE source_id NOT LIKE 'query-failure%'").fetchone()[0]
    assert kept == 1, "the listing read before the limit is kept"
    assert not any("q=software" in u for u in opener.urls), "no request after the 429"
    # Both unread queries are filed failed, so the recovery pass reads them later.
    assert row["failed_queries"] == ["engineer", "software"]
    assert "limit reached" in row["note"] and "sits out until" in row["note"]
    assert limits.blocked(db, _board(Opener())).startswith("cooling down")
    assert limits.requests_today(db, "himalayas") == 4


def test_a_run_uses_only_what_is_left_of_the_budget(db, rules):
    limits.record_requests(db, "himalayas", 599)
    opener = Opener()
    row = sigrun.collect(db, source=_board(opener), rules=rules,
                         queries=["developer", "engineer"], limit_per_query=20)
    assert len(opener.urls) == 1
    assert row["status"] == "limited"
    assert "budget spent" in row["note"]


# ---- the chain -------------------------------------------------------------

@pytest.fixture
def fake_collect(db, monkeypatch):
    sigrun.ensure_run_table(db)
    calls, results = [], {}

    def collect(db, *, source_name="hackernews", source=None, **kw):
        name = source.name if source is not None else source_name
        calls.append(name)
        row = {"run_id": f"r-{name}", "source": name, "mode": "live", "kind": "manual",
               "started_utc": "t", "finished_utc": "t", "since": "7d", "queries": "[]",
               "collected": 5, "new": 0, "seen_again": 5, "edited": 0, "relevant": 0,
               "buyer": 0, "practitioner": 0, "errors": 0, "status": "ok", "note": "",
               **results.get(name, {})}
        sigrun._save_run(db, row)
        return row

    monkeypatch.setattr(sigrun, "collect", collect)
    collect.calls, collect.results = calls, results
    return collect


def test_a_limit_switches_to_the_next_board_even_after_new_records(db, fake_collect):
    fake_collect.results["himalayas"] = {"status": "limited", "new": 7,
                                         "note": "limit reached: HTTP 429"}
    fake_collect.results["remotive"] = {"new": 2}
    rows = sigrun.collect_or_fall_back(db, source_name="himalayas",
                                       fallback="remotive,jobicy")
    assert fake_collect.calls == ["himalayas", "remotive"]
    assert rows[0]["note"] == "limit reached: HTTP 429; limit reached; fell back to remotive"
    assert rows[1]["note"] == "fallback: himalayas hit its limit"


def test_a_source_sitting_out_is_skipped_without_a_request(db, fake_collect):
    limits.mark_limited(db, "himalayas", SourceError("HTTP 429", status="429"))
    fake_collect.results["remotive"] = {"new": 3}
    rows = sigrun.collect_or_fall_back(db, source_name="hackernews-hiring",
                                       fallback="himalayas,remotive")
    assert fake_collect.calls == ["hackernews-hiring", "remotive"]
    skipped = rows[1]
    assert skipped["source"] == "himalayas" and skipped["status"] == "skipped"
    assert skipped["note"].startswith("not asked: cooling down until")
    assert "sitting out; fell back to remotive" in skipped["note"]
    ledger = {r["source"]: r["status"] for r in sigrun.runs(db)}
    assert ledger["himalayas"] == "skipped"


def test_a_first_source_sitting_out_hands_over_to_the_chain(db, fake_collect):
    limits.record_requests(db, "remotive", 4)
    fake_collect.results["jobicy"] = {"new": 1}
    rows = sigrun.collect_or_fall_back(db, source_name="remotive", fallback="jobicy")
    assert fake_collect.calls == ["jobicy"]
    assert rows[0]["status"] == "skipped"
    assert rows[1]["note"] == "fallback: remotive was sitting out"


def test_a_blocked_first_source_still_stops_the_chain(db, fake_collect):
    """A 403 or a block page is not a limit; moving on would hide it."""
    fake_collect.results["himalayas"] = {"status": "failed", "errors": 1}
    rows = sigrun.collect_or_fall_back(db, source_name="himalayas", fallback="remotive")
    assert fake_collect.calls == ["himalayas"] and len(rows) == 1


def test_every_board_limited_ends_with_the_chain(db, fake_collect):
    for name in ("himalayas", "remotive", "jobicy"):
        fake_collect.results[name] = {"status": "limited"}
    rows = sigrun.collect_or_fall_back(db, source_name="himalayas",
                                       fallback="remotive,jobicy")
    assert [r["status"] for r in rows] == ["limited"] * 3
