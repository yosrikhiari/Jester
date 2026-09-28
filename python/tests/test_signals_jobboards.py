"""The job-board collectors: Himalayas, Remotive, Jobicy.

Offline: every test hands the source a fake opener. What is pinned down is the
part that decides lead quality -- which listings become records at all, and
the shape the hiring rules read.
"""
import io
import json
import urllib.error

import pytest

from jester.signals.filters import load_rules, apply_to
from jester.signals.sources import SourceError, available, get_source
from jester.signals.sources import jobboards as jb


class FakeOpener:
    def __init__(self, *bodies, error=None):
        self.bodies, self.error, self.urls = list(bodies), error, []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.bodies.pop(0) if self.bodies else {}).encode())


def _src(name, opener):
    return get_source(name, opener=opener, sleep=lambda s: None)


@pytest.fixture(scope="module")
def rules():
    return load_rules("config/hiring_rules.yaml")


def _himalayas_job(**kw):
    job = {"guid": "https://himalayas.app/companies/acme/jobs/python-dev",
           "title": "Senior Python Developer", "companyName": "Acme",
           "employmentType": "Contractor", "parentCategories": ["Developer"],
           "locationRestrictions": ["Germany"], "minSalary": 60, "maxSalary": 90,
           "currency": "EUR", "salaryPeriod": "hour", "pubDate": 1790195571,
           "description": "<p>Contract role building internal tools and API integrations.</p>"}
    job.update(kw)
    return job


def test_the_boards_are_registered_with_their_paperwork():
    by_name = {s["name"]: s for s in available()}
    for name in ("himalayas", "remotive", "jobicy"):
        assert by_name[name]["access"]
        assert by_name[name]["terms_url"].startswith("https://")
        assert isinstance(by_name[name]["rate"], str)
        assert by_name[name]["rate"]


def test_himalayas_asks_for_contract_work_and_keeps_only_tech(rules):
    opener = FakeOpener({"jobs": [
        _himalayas_job(),
        _himalayas_job(guid="https://himalayas.app/x/jobs/content-dev",
                       title="Content Developer", parentCategories=["Education"]),
    ]})
    sigs = list(_src("himalayas", opener).search("developer", limit=10))
    assert "employment_type=Contractor" in opener.urls[0]
    assert [s.company for s in sigs] == ["Acme"], "the education 'developer' is dropped"
    s = sigs[0]
    assert s.title.startswith("Acme | Senior Python Developer | Remote (Germany) | Contractor")
    assert s.source_url.endswith("/python-dev")
    assert s.community == "himalayas/contract"
    apply_to(s, rules)
    assert s.audience == "buyer"


def test_marketplaces_are_not_buyers():
    """lemon.io and A.Team scored 1.00 buyers in the dry run. They recruit
    freelancers for their own clients: supply side, not a lead."""
    opener = FakeOpener({"jobs": [_himalayas_job(companyName="lemon.io"),
                                  _himalayas_job(companyName="A.Team")]})
    assert list(_src("himalayas", opener).search("developer", limit=10)) == []


def test_gig_tasks_are_not_engineering_work():
    """Toloka's "Voice Recording - AI Trainer" listings were filed under
    Developer and stored as buyers on the first live run."""
    opener = FakeOpener({"jobs": [
        _himalayas_job(companyName="Some Data Co",
                       title="English-Chinese Bilingual Voice Recording - AI Trainer"),
        _himalayas_job(companyName="Other Co", title="Search Ads Rater")]})
    assert list(_src("himalayas", opener).search("developer", limit=10)) == []


def test_a_company_merely_mentioning_a_marketplace_is_kept():
    assert not jb._is_marketplace("Turingan Labs")
    assert jb._is_marketplace("Toptal")
    assert jb._is_marketplace("lemon.io")


def test_remotive_keeps_contract_shaped_tech_listings_in_one_request():
    opener = FakeOpener({"jobs": [
        {"id": 1, "title": "Shopify Developer", "company_name": "Sanctuary",
         "category": "Software Development", "job_type": "contract",
         "url": "https://remotive.com/1", "description": "x"},
        {"id": 2, "title": "Backend Engineer", "company_name": "Big Co",
         "category": "Software Development", "job_type": "full_time",
         "url": "https://remotive.com/2", "description": "x"},
        {"id": 3, "title": "Support Agent", "company_name": "Helpdesk",
         "category": "Customer Service", "job_type": "freelance",
         "url": "https://remotive.com/3", "description": "x"},
    ]})
    sigs = list(_src("remotive", opener).search("all", limit=50))
    assert len(opener.urls) == 1, "one call a run: the API asks for four a day"
    assert [s.company for s in sigs] == ["Sanctuary"]
    assert sigs[0].buyer_intent == "contract"


def test_jobicy_drops_full_time_whatever_the_description_says():
    """Board boilerplate ("employees and contractors") made the rules score a
    full-time architect role at Autodesk as a 1.00 buyer. The board states the
    type; that is what decides."""
    opener = FakeOpener({"jobs": [
        {"id": 7, "jobTitle": "Software Architect", "companyName": "Autodesk",
         "jobType": ["Full-Time"], "url": "https://jobicy.com/7",
         "jobDescription": "We hire employees and contractors worldwide."},
        {"id": 8, "jobTitle": "Automation Engineer", "companyName": "Small Co",
         "jobType": ["Contract"], "url": "https://jobicy.com/8",
         "jobDescription": "Short-term project."},
    ]})
    sigs = list(_src("jobicy", opener).search("engineering", limit=50))
    assert [s.company for s in sigs] == ["Small Co"]


def test_a_refusal_is_a_source_error_not_an_empty_board():
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    listings = _src("remotive", FakeOpener(error=err)).search("all")
    with pytest.raises(SourceError, match="403"):
        next(listings)


class FlakyOpener(FakeOpener):
    """Fails with the given errors first, then answers."""

    def __init__(self, errors, body):
        super().__init__(body)
        self.errors = list(errors)

    def __call__(self, req, timeout=None):
        if self.errors:
            self.urls.append(req.full_url)
            raise self.errors.pop(0)
        return super().__call__(req, timeout)


def test_a_503_is_waited_out_and_retried():
    busy = urllib.error.HTTPError("u", 503, "Busy", {}, None)
    opener = FlakyOpener([busy], {"jobs": [_himalayas_job()]})
    sigs = list(_src("himalayas", opener).search("developer", limit=5))
    assert len(opener.urls) == 2, "one failure, one retry"
    assert [s.company for s in sigs] == ["Acme"]


def test_an_unreachable_board_fails_after_its_retries():
    down = urllib.error.URLError("connection refused")
    opener = FlakyOpener([down, down, down], {})
    listings = _src("jobicy", opener).search("engineering")
    with pytest.raises(SourceError, match="after 3 attempts"):
        next(listings)


def test_a_block_page_is_an_error_not_an_empty_board():
    class HTMLOpener(FakeOpener):
        def __call__(self, req, timeout=None):
            return io.BytesIO(b"<html>Access denied</html>")

    listings = _src("remotive", HTMLOpener()).search("all")
    with pytest.raises(SourceError, match="non-JSON"):
        next(listings)


@pytest.mark.parametrize("lo, hi, currency, period, want", [
    (60, 90, "EUR", "hour", "EUR 60-90 /hour"),
    (5, 5, "USD", "hourly", "USD 5 /hourly"),       # the same figure twice, once
    ("80000", None, "", "", "80,000"),
    (None, None, "USD", "year", ""),
    ("n/a", 0, "", "", ""),
])
def test_salary_ranges_read_the_way_a_person_writes_them(lo, hi, currency, period, want):
    assert jb._money(lo, hi, currency, period) == want


def test_a_bad_timestamp_is_left_empty():
    assert jb._iso_from_epoch("not a time") == ""
    assert jb._iso_from_epoch(0).startswith("1970-01-01")
