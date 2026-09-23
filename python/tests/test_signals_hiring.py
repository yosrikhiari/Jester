"""The hiring-advert collector and its rule set.

Why this source exists at all: twenty-one Reddit communities and two
collection modes produced no stable buyer signal in 2,257 records, because the
premise was wrong. People who will pay for engineering work do not describe
their problem on a forum - they post a job. So this collects the place where
spending money is the point of the post, and measures 2.9% where everything
else measured about zero.

The rule set is a second config, not a second engine: the same classifier
scores it, because the machinery was never specific to forum posts.
"""
import json
import urllib.error

import pytest

from jester.signals import Signal
from jester.signals.filters import classify, load_rules
from jester.signals.sources import available, get_source
from jester.signals.sources.hn_hiring import HackerNewsHiring

RULES_PATH = "config/hiring_rules.yaml"


class FakeHTTP:
    """Answers by URL fragment, so a test reads as the endpoint it stands in."""

    def __init__(self, replies=None, raise_with=None):
        self.replies = replies or {}
        self.raise_with = raise_with
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        if self.raise_with:
            raise self.raise_with
        for needle, body in self.replies.items():
            if needle in req.full_url:
                return _Resp(json.dumps(body))
        return _Resp(json.dumps({"hits": [], "children": []}))


class _Resp:
    def __init__(self, body):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _hn(http):
    return HackerNewsHiring(opener=http, sleep=lambda _s: None)


@pytest.fixture()
def rules():
    return load_rules(RULES_PATH)


# ---- the source ------------------------------------------------------------

def test_it_is_registered_with_its_own_paperwork():
    assert "hackernews-hiring" in {s["name"] for s in available()}
    src = get_source("hackernews-hiring")
    assert src.access and src.terms_url


def test_only_real_monthly_threads_count():
    """A story called "Analysis of 88,975 HN Who Is Hiring Job Posts" matches
    the query and is not a hiring thread. So does a two-comment reply about
    one. Both were in the live results."""
    http = FakeHTTP({"search_by_date": {"hits": [
        {"objectID": "1", "title": "Ask HN: Who is hiring? (September 2026)",
         "num_comments": 399, "created_at": "2026-09-01T00:00:00Z"},
        {"objectID": "2", "title": "Analysis of 88,975 HN Who Is Hiring Job Posts",
         "num_comments": 0, "created_at": "2026-09-10T00:00:00Z"},
        {"objectID": "3", "title": "Ask HN: Who is hiring? (September 2026)",
         "num_comments": 2, "created_at": "2026-09-01T00:00:00Z"},
    ]}})
    got = _hn(http).threads()
    assert [t["id"] for t in got] == ["1"]


def test_an_advert_becomes_a_record_with_its_permalink():
    http = FakeHTTP({
        "search_by_date": {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring?",
                                     "num_comments": 400, "created_at": "2026-09-01T00:00:00Z"}]},
        "items/1": {"children": [
            {"id": 55, "author": "founder", "created_at": "2026-09-01T10:00:00Z",
             "text": "<p>Noricum | Backend | REMOTE | Contract | $120-160/hr</p>"}]},
    })
    rec = list(_hn(http).search("1"))[0]
    assert rec.source_id == "55"
    assert rec.source_url == "https://news.ycombinator.com/item?id=55"
    assert rec.community == "hn/hiring" and rec.mode == "live"


def test_the_company_is_read_from_the_advert_never_inferred():
    """`company` stops being "unknown" here, and only because the advert names
    itself in its own first field. That is the "unless a permitted source
    states it" case the field map already allows."""
    http = FakeHTTP({
        "search_by_date": {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring?",
                                     "num_comments": 400, "created_at": "2026-09-01T00:00:00Z"}]},
        "items/1": {"children": [
            {"id": 1, "text": "Noricum | Backend Engineer | REMOTE | Contract | $120/hr"},
            {"id": 2, "text": "Hey HN, we are a small team in Boston and we are hiring "
                              "a few engineers this quarter to work on our platform."},
        ]},
    })
    got = {r.source_id: r for r in _hn(http).search("1")}
    assert got["1"].company == "Noricum"
    # No pipe-delimited name field: guessing one out of prose is the inference
    # this collector does not do.
    assert got["2"].company == "unknown"


def test_the_engagement_is_quoted_not_judged():
    """`buyer_intent` becomes the words the company used, never a verdict on
    whether they will buy from us."""
    http = FakeHTTP({
        "search_by_date": {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring?",
                                     "num_comments": 400, "created_at": "2026-09-01T00:00:00Z"}]},
        "items/1": {"children": [
            {"id": 1, "text": "Acme | Engineer | REMOTE | Contract / Part-time | $90/hr"}]},
    })
    rec = list(_hn(http).search("1"))[0]
    assert "contract" in rec.buyer_intent and "part-time" in rec.buyer_intent


def test_a_deleted_advert_is_not_a_record():
    http = FakeHTTP({
        "search_by_date": {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring?",
                                     "num_comments": 400, "created_at": "2026-09-01T00:00:00Z"}]},
        "items/1": {"children": [{"id": 1, "text": None}, {"id": 2, "text": "  "}]},
    })
    assert list(_hn(http).search("1")) == []


def test_a_dead_service_is_an_error_not_an_empty_month():
    http = FakeHTTP(raise_with=urllib.error.HTTPError("u", 400, "Bad", {}, None))
    from jester.signals.sources import SourceError

    with pytest.raises(SourceError, match="HTTP 400"):
        list(_hn(http).search("1"))


# ---- the rule set ----------------------------------------------------------

def test_the_rule_set_asks_a_different_question(rules):
    """The forum rules ask "is this person describing work software could
    remove?". An advert is not describing a problem; it is announcing a
    decision with a budget attached."""
    names = {f.name for f in rules.families}
    assert "engagement" in names and "our_work" in names
    assert "owner_voice" not in names, "that is the other question"
    required = {f.name for f in rules.by_role("buyer_required")}
    assert required == {"engagement"}, \
        "a buyer must want a contract-shaped engagement; the work alone is not enough"


def test_a_contract_advert_in_our_field_is_a_buyer(rules):
    v = classify("Noricum | Senior Backend Engineer | REMOTE | Contract to permanent | "
                 "$120-160/hr | Python, Postgres, API integration work.", rules)
    assert v.audience == "buyer" and v.ambiguous is False


def test_full_time_in_our_field_is_evidence_not_a_lead(rules):
    """The practitioner tier means "spending on our kind of work, but only as
    an employee". Real market information; never handed to outreach."""
    v = classify("Quobyte | Senior Backend Engineer | Full-time | Berlin | "
                 "Python and distributed systems, salary EUR 90k.", rules)
    assert v.audience != "buyer"


def test_equity_only_is_rejected_outright(rules):
    """Not scored low - rejected. The entire reason for preferring adverts to
    forum posts is that the budget is stated, and this one states there is
    none. At a weight it landed at 0.50 and read as a thin lead."""
    v = classify("Olli | Founding Engineer | REMOTE | Fractional / Contract | "
                 "Equity-only (2-5%). AI-native operations platform, Python, LLM agents.",
                 rules)
    assert v.audience == "none"
    assert "hard reject" in v.reason


def test_a_cofounder_hunt_is_the_same_thing_with_a_better_title(rules):
    v = classify("Regnitive | Technical Co-Founder | Toronto | Equity | "
                 "AI-powered data governance. Consulting arrangement possible.", rules)
    assert v.audience == "none"


def test_founding_engineer_survives(rules):
    """The hard reject on co-founder must not take "Founding Engineer" with
    it: that is a real paid role and one of the better leads in the sample."""
    v = classify("Duets Network | Founding Engineer | REMOTE | Contract, ~10-15 hrs/wk | "
                 "$100/hr. AI-powered platform, Python and agents.", rules)
    assert v.audience == "buyer"


def test_the_company_name_does_not_score_itself(rules):
    """Black Canyon Consulting scored buyer 1.00 because `consulting` was in
    its name. Bare consulting/consultant were dropped for the forms that
    describe an arrangement."""
    v = classify("BCC | Platform Systems Engineers | Bethesda MD | Competitive "
                 "compensation. Black Canyon Consulting is hiring. Python and AWS.", rules)
    assert v.audience != "buyer"


def test_a_number_is_not_a_tax_form(rules):
    """`1099` matched a throughput figure in a full-time advert."""
    v = classify("SerpApi | Fullstack Engineer | Based in our Austin office | "
                 "Full-time only | We serve 1099 requests per second. Python, AI.", rules)
    assert v.audience != "buyer"


def test_the_supply_side_is_rejected_wherever_it_lands(rules):
    v = classify("SEEKING WORK | Remote | Python automation, API integrations. $80/hr.",
                 rules)
    assert v.audience == "none"


def test_the_shipped_hiring_cases_pass(rules):
    """A second rule set needs its own gate, or it has none. This set is
    deliberately small and the coverage grader says so on every run - the
    categories for edits, failures and ambiguity are not written yet."""
    from jester.signals import fixtures as fx

    res = fx.check(rules, "python/tests/fixtures/hiring")
    assert res["failed"] == [], f"cases missed their label: {res['failed']}"
    assert res["passed"] == res["total"] >= 10


def test_the_hiring_signal_still_carries_the_rules_that_do_not_change(rules):
    """`mode` is still a column and nothing is enriched. The only field rule
    that moves here is `company`, and only because the advert states it."""
    s = Signal(source_id="x", platform="hackernews", community="hn/hiring",
               text="Acme | Engineer | REMOTE | Contract | $90/hr", mode="live",
               company="Acme", buyer_intent="contract")
    row = s.row()
    assert row["mode"] == "live"
    assert row["company"] == "Acme"
    assert row["buyer_intent"] == "contract"
