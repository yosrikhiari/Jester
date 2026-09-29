"""Closed job adverts are marked removed (2026-09-29).

A role is filled and the board drops the listing; nothing noticed, because
`mark_removed` was only ever called by the fixtures. The brief and leads.csv
already leave removed rows out -- they just never had any.

Only a COMPLETE read may close anything: a listing missing from a partial read
may simply be further down.
"""
import io
import json

import pytest

from jester import signals as sig
from jester.signals import run as sigrun
from jester.signals.filters import load_rules
from jester.signals.sources import get_source


def _job(slug, category="Developer", company="Acme"):
    return {"guid": f"https://himalayas.app/companies/acme/jobs/{slug}",
            "title": "Senior Python Developer", "companyName": company,
            "employmentType": "Contractor", "parentCategories": [category],
            "pubDate": 1790195571, "description": "Contract role building API integrations."}


class Opener:
    """Serves the bodies in order, one per request."""

    def __init__(self, *bodies):
        self.bodies, self.urls = list(bodies), []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        return io.BytesIO(json.dumps(self.bodies.pop(0) if self.bodies else {}).encode())


@pytest.fixture
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def rules():
    return load_rules("config/hiring_rules.yaml")


def _collect(db, rules, name, opener, queries, limit=1000):
    source = get_source(name, opener=opener, sleep=lambda s: None)
    return sigrun.collect(db, source=source, rules=rules, queries=queries, limit_per_query=limit)


def _removed(db):
    return {r[0] for r in db.execute(
        f"SELECT source_id FROM {sig.TABLE} WHERE COALESCE(removed_utc, '') != ''")}


def test_a_listing_gone_from_a_complete_read_is_marked_closed(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    row = _collect(db, rules, "himalayas", Opener({"jobs": [_job("a")], "totalCount": 1}),
                   ["developer"])
    assert _removed(db) == {"himalayas:b"}
    assert "1 listing(s) gone from the board; marked closed" in row["note"]


def test_a_check_that_closed_nothing_says_it_ran(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    row = _collect(db, rules, "himalayas",
                   Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}), ["developer"])
    assert "all 2 listing(s) checked are still on the board" in row["note"]


def test_a_partial_read_closes_nothing(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    # 40 results exist; the run was allowed 20 listing slots and read one page.
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a")] * 20, "totalCount": 40}),
             ["developer"], limit=20)
    assert _removed(db) == set()


def test_only_records_found_by_the_terms_read_are_judged(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a")], "totalCount": 1}), ["developer"])
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("z")], "totalCount": 1}), ["data"])
    # A complete read of "developer" that no longer holds "a" -- and says
    # nothing about "z", which "data" found.
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("q")], "totalCount": 1}), ["developer"])
    assert _removed(db) == {"himalayas:a"}


def test_a_listing_the_rules_now_filter_out_is_not_closed(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    # "b" is still on the board, now under a category the collector drops:
    # filtered is not closed.
    _collect(db, rules, "himalayas",
             Opener({"jobs": [_job("a"), _job("b", category="Education")], "totalCount": 2}),
             ["developer"])
    assert _removed(db) == set()


def test_a_listing_that_comes_back_is_reopened(db, rules):
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a")], "totalCount": 1}), ["developer"])
    assert _removed(db) == {"himalayas:b"}
    _collect(db, rules, "himalayas", Opener({"jobs": [_job("a"), _job("b")], "totalCount": 2}),
             ["developer"])
    assert _removed(db) == set()


def test_a_board_that_suddenly_lost_most_listings_closes_none(db, rules):
    many = [_job(f"j{i}") for i in range(30)]
    _collect(db, rules, "himalayas", Opener({"jobs": many, "totalCount": 30}), ["developer"], )
    row = _collect(db, rules, "himalayas", Opener({"jobs": [_job("j0")], "totalCount": 1}),
                   ["developer"])
    assert _removed(db) == set()
    assert "too many to be real; none marked closed" in row["note"]


def _remotive_job(i):
    return {"id": i, "url": f"https://remotive.com/j/{i}", "company_name": f"Co{i}",
            "title": "Senior Backend Engineer", "category": "Software Development",
            "job_type": "contract", "candidate_required_location": "Worldwide",
            "salary": "$90/hr", "description": "Contract backend work.",
            "publication_date": "2026-09-20T10:00:00"}


def test_remotive_returns_its_whole_board_so_one_read_decides(db, rules):
    _collect(db, rules, "remotive", Opener({"jobs": [_remotive_job(1), _remotive_job(2)]}), ["all"])
    _collect(db, rules, "remotive", Opener({"jobs": [_remotive_job(1)]}), ["all"])
    assert _removed(db) == {"remotive:2"}


def test_an_empty_answer_closes_nothing(db, rules):
    _collect(db, rules, "remotive", Opener({"jobs": [_remotive_job(1)]}), ["all"])
    _collect(db, rules, "remotive", Opener({"jobs": []}), ["all"])
    assert _removed(db) == set()


def _jobicy_job(i):
    return {"id": i, "url": f"https://jobicy.com/jobs/{i}", "companyName": f"Co{i}",
            "jobTitle": "Platform Engineer", "jobType": ["Contract"], "jobGeo": "Anywhere",
            "jobDescription": "Contract platform engineering.", "pubDate": "2026-09-20 10:00:00"}


def test_jobicy_is_complete_only_when_it_returned_fewer_than_asked(db, rules):
    _collect(db, rules, "jobicy", Opener({"jobs": [_jobicy_job(1), _jobicy_job(2)]}),
             ["engineering"], limit=5)
    # Exactly as many as asked: there may be more beyond the cap -- judge nothing.
    _collect(db, rules, "jobicy", Opener({"jobs": [_jobicy_job(1), _jobicy_job(3)]}),
             ["engineering"], limit=2)
    assert _removed(db) == set()
    # Fewer than asked: the whole industry.
    _collect(db, rules, "jobicy", Opener({"jobs": [_jobicy_job(1)]}), ["engineering"], limit=5)
    assert _removed(db) == {"jobicy:2", "jobicy:3"}


def test_the_sweep_is_schedulable_and_its_two_word_term_survives_the_launcher():
    """cmd splits an unquoted "machine learning" into two arguments, and
    argparse would reject the second on every scheduled run."""
    from jester import schedule
    script = schedule.launcher_script("data/signals-live.db", "config", "python",
                                      command="signals-sweep")
    line = next(l for l in script.splitlines() if "jester.cli" in l)
    assert '--query "machine learning"' in line
    assert "--limit 3000" in line
    assert schedule.TASK_FOR_COMMAND["signals-sweep"].endswith("SignalsSweep")
