"""the live collector, the run ledger, recovery and the digest.

What a run has to prove: that it happened on schedule, that a failure can be
recovered, that its real unique/relevant/exported/error counts are saved, and
that finding nothing still counts as a successful run. What the digest has to
carry: repeated needs, source links, real counts, what is unknown — and no
automatic outreach.

Everything here runs against a fake HTTP transport. Not because the real API is
hard to reach — it is public and keyless — but because the behaviours that
matter are the ones that are hard to *produce* on demand: a 429, a body that is
not JSON, a query that fails and then recovers. A test that needs the network
to misbehave is a test that never runs.
"""
import json
import urllib.error

import pytest

from jester import signals as sig
from jester.signals import digest as dg
from jester.signals import run as sigrun
from jester.signals.filters import load_rules
from jester.signals.sources import SourceError, available, get_source
from jester.signals.sources.hackernews import HackerNews, _bucket, _epoch, _plain


class FakeHTTP:
    """Answers with canned Algolia pages, and can be told to misbehave."""

    def __init__(self, pages=None, raise_with=None):
        self.pages = pages or []
        self.raise_with = raise_with
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        if self.raise_with:
            exc = self.raise_with
            if isinstance(exc, list):
                exc = exc.pop(0) if exc else None
            if exc is not None:
                raise exc
        page = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)] if self.pages else {
            "hits": [], "nbPages": 0}
        return _Resp(json.dumps(page))


class _Resp:
    def __init__(self, body):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _hit(oid, **kw):
    base = {"objectID": oid, "author": "someone", "created_at": "2026-09-20T10:00:00Z",
            "title": "We re-key every order by hand", "_tags": ["story", "ask_hn"],
            "story_text": "I run a small distributor and we do it manually every week."}
    base.update(kw)
    return base


@pytest.fixture()
def rules():
    return load_rules()


@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


def _hn(http, **kw):
    # sleep is stubbed out: the pacing is asserted separately, and no test
    # should cost a second per request to prove it.
    return HackerNews(opener=http, sleep=lambda _s: None, **kw)


# ---- the source's paperwork ------------------------------------------------

def test_every_source_declares_the_authority_it_runs_on():
    """Bypassing a service's limits is not an option. A collector that cannot
    say why it is allowed to run is one nobody can check."""
    for s in available():
        assert s["access"] and s["terms_url"] and s["rate"], s


def test_reddit_is_absent_on_purpose():
    """The free Data API is non-commercial and this work is commercial. A path
    that cannot legally run must not be callable by accident."""
    assert "reddit" not in {s["name"] for s in available()}
    with pytest.raises(SourceError, match="no source named"):
        get_source("reddit")


# ---- turning a hit into a record -------------------------------------------

def test_the_authors_html_is_stored_as_words():
    """Algolia returns markup. `<p>` between paragraphs and `&#x2F;` inside a
    URL both defeat phrase matching, and the excerpt is meant to be read."""
    assert _plain("<p>we&#x27;re doing it<br>by hand</p>") == "we're doing it by hand"


def test_the_part_of_the_site_is_kept_apart():
    """Ask HN and Show HN are different populations; lumping them together
    hides that one of them is entirely vendors."""
    assert _bucket(["story", "ask_hn"]) == "hn/ask"
    assert _bucket(["story", "show_hn"]) == "hn/show"
    assert _bucket(["comment"]) == "hn/comment"
    assert _bucket(["story"]) == "hn/story"


def test_a_hit_becomes_a_record_with_its_permalink():
    hn = _hn(FakeHTTP([{"hits": [_hit("42")], "nbPages": 1}]))
    got = list(hn.search("we do it manually", limit=10))
    assert len(got) == 1
    s = got[0]
    assert s.source_id == "42"
    assert s.source_url == "https://news.ycombinator.com/item?id=42"
    assert s.mode == "live" and s.platform == "hackernews"
    assert s.query == "we do it manually"


def test_a_hit_with_no_text_at_all_is_skipped():
    """A link-only submission with no headline is a URL. Storing it would
    inflate the collected count with a record that can never be classified."""
    hn = _hn(FakeHTTP([{"hits": [_hit("1", title=None, story_text=None)], "nbPages": 1}]))
    assert list(hn.search("q", limit=10)) == []


def test_a_comment_carries_its_storys_title_as_context_not_as_its_own():
    """A comment has no title. What the collector stores there belongs to the
    thread, and scoring it as the record's own words would let one popular
    thread lend its voice to every reply under it."""
    hn = _hn(FakeHTTP([{"hits": [_hit("7", _tags=["comment"], title=None,
                                      story_title="What would you automate in your business?",
                                      story_text=None, comment_text="chasing late invoices")],
                        "nbPages": 1}]))
    s = list(hn.search("q", limit=5))[0]
    assert s.kind == "comment"
    assert s.title == "What would you automate in your business?"
    assert s.text == "chasing late invoices"


def test_paging_stops_at_the_last_page_rather_than_looping():
    http = FakeHTTP([{"hits": [_hit("1")], "nbPages": 2},
                     {"hits": [_hit("2")], "nbPages": 2}])
    hn = _hn(http)
    assert len(list(hn.search("q", limit=100))) == 2
    assert len(http.calls) == 2


def test_the_limit_is_respected_even_mid_page():
    http = FakeHTTP([{"hits": [_hit(str(i)) for i in range(50)], "nbPages": 5}])
    assert len(list(_hn(http).search("q", limit=3))) == 3


# ---- failure, and being polite about it ------------------------------------

def test_a_rate_limit_is_waited_out_not_hammered():
    """Retrying a 429 immediately is the behaviour the card calls bypassing a
    limit."""
    slept = []
    err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    http = FakeHTTP([{"hits": [_hit("1")], "nbPages": 1}], raise_with=[err, None, None])
    hn = HackerNews(opener=http, sleep=slept.append)
    assert len(list(hn.search("q", limit=5))) == 1
    assert any(s >= 4.0 for s in slept), f"no backoff happened: {slept}"


def test_a_4xx_that_is_not_a_rate_limit_fails_immediately():
    err = urllib.error.HTTPError("u", 400, "Bad", {}, None)
    hn = _hn(FakeHTTP(raise_with=err))
    with pytest.raises(SourceError, match="HTTP 400"):
        list(hn.search("q"))


def test_a_body_that_is_not_json_is_a_failure_not_an_empty_day():
    """An interstitial read as "no results" is how a block becomes a quiet
    week nobody investigates."""
    class Junk(FakeHTTP):
        def __call__(self, req, timeout=None):
            self.calls.append(req.full_url)
            return _Resp("<html>Just a moment</html>")

    with pytest.raises(SourceError, match="non-JSON"):
        list(_hn(Junk()).search("q"))


def test_a_relative_window_is_understood():
    assert _epoch("24h") < _epoch("1h")
    assert _epoch("2026-01-01T00:00:00+00:00") == 1767225600
    with pytest.raises(SourceError, match="cannot read a date"):
        _epoch("last tuesday")


# ---- the run ---------------------------------------------------------------

def test_a_run_records_what_it_actually_collected(db, rules):
    http = FakeHTTP([{"hits": [_hit("1"), _hit("2")], "nbPages": 1}])
    row = sigrun.collect(db, rules=rules, queries=["we do it manually"],
                         source=_hn(http), limit_per_query=10)
    assert row["collected"] == 2 and row["new"] == 2 and row["errors"] == 0
    assert row["status"] == "ok"
    ledger = sigrun.runs(db)
    assert len(ledger) == 1 and ledger[0]["run_id"] == row["run_id"]
    assert json.loads(ledger[0]["queries"]) == ["we do it manually"]


def test_a_second_run_of_the_same_records_adds_none(db, rules):
    http = FakeHTTP([{"hits": [_hit("1"), _hit("2")], "nbPages": 1}])
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(http))
    second = sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1"), _hit("2")], "nbPages": 1}])))
    assert second["new"] == 0 and second["seen_again"] == 2
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 2


def test_a_run_that_finds_nothing_is_a_successful_run(db, rules):
    """Explicitly on the card: "a zero-result run is valid". Collecting
    nothing and failing to collect are different facts."""
    row = sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP()))
    assert row["collected"] == 0 and row["errors"] == 0
    assert row["status"] == "ok"


def test_a_failed_query_becomes_a_counted_record_not_a_log_line(db, rules):
    """A silent failure is the worst kind. A log line is not counted, not
    queryable and not in the export."""
    err = urllib.error.HTTPError("u", 400, "Bad", {}, None)
    row = sigrun.collect(db, rules=rules, queries=["doomed query"],
                         source=_hn(FakeHTTP(raise_with=err)))
    assert row["errors"] == 1 and row["status"] == "failed"
    stored = db.execute(
        f"SELECT * FROM {sig.TABLE} WHERE run_status = 'error'").fetchone()
    assert stored is not None
    assert stored["query"] == "doomed query"
    assert sigrun.FAILURE_PREFIX in stored["source_id"]


def test_the_same_query_failing_twice_does_not_grow_the_archive(db, rules):
    err = urllib.error.HTTPError("u", 500, "Boom", {}, None)
    for _ in range(3):
        sigrun.collect(db, rules=rules, queries=["doomed"],
                       source=_hn(FakeHTTP(raise_with=err)))
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 1


def test_a_partial_run_is_neither_ok_nor_failed(db, rules):
    """One query down out of two is a different fact from all of them down,
    and a status that cannot say so sends someone to the wrong problem."""
    class OneBad(FakeHTTP):
        def __call__(self, req, timeout=None):
            self.calls.append(req.full_url)
            if "doomed" in req.full_url:
                raise urllib.error.HTTPError("u", 500, "Boom", {}, None)
            return _Resp(json.dumps({"hits": [_hit("1")], "nbPages": 1}))

    row = sigrun.collect(db, rules=rules, queries=["fine", "doomed"], source=_hn(OneBad()))
    assert row["errors"] == 1 and row["status"] == "partial"


# ---- recovery --------------------------------------------------------------

def test_recovery_re_runs_exactly_the_queries_that_failed(db, rules):
    """The gate asks for three runs *and a recovery pass*, which only means
    something if a failure can be found again."""
    err = urllib.error.HTTPError("u", 503, "Down", {}, None)
    sigrun.collect(db, rules=rules, queries=["good", "broken"], source=_hn(
        FakeHTTPSelective(fail_on="broken")))

    row = sigrun.recover(db, rules=rules, source=_hn(
        FakeHTTP([{"hits": [_hit("99")], "nbPages": 1}])))
    assert json.loads(row["queries"]) == ["broken"]
    assert row["kind"] == "recovery"
    assert "recovered 1 of 1" in row["note"]


def test_a_recovered_failure_is_marked_not_deleted(db, rules):
    """The outage happened. An archive that erases its own failures cannot
    show that the recovery worked."""
    sigrun.collect(db, rules=rules, queries=["broken"],
                   source=_hn(FakeHTTPSelective(fail_on="broken")))
    sigrun.recover(db, rules=rules, source=_hn(FakeHTTP([{"hits": [], "nbPages": 0}])))
    row = db.execute(f"SELECT * FROM {sig.TABLE} WHERE source_id LIKE ?",
                     (f"{sigrun.FAILURE_PREFIX}:%",)).fetchone()
    assert row is not None, "the failure record was deleted"
    assert row["run_status"] == "ok"
    assert "recovered" in row["error"]


def test_recovery_with_nothing_to_recover_still_records_a_run(db, rules):
    row = sigrun.recover(db, rules=rules, source=_hn(FakeHTTP()))
    assert row["note"] == "nothing to recover"
    assert sigrun.runs(db)[0]["kind"] == "recovery"


class FakeHTTPSelective(FakeHTTP):
    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        if self.fail_on in req.full_url:
            raise urllib.error.HTTPError("u", 503, "Down", {}, None)
        return _Resp(json.dumps({"hits": [_hit("1")], "nbPages": 1}))


# ---- re-scoring an archive -------------------------------------------------

def test_a_reclassify_without_a_baseline_says_it_is_not_an_ab(db, rules):
    """Comparing new verdicts against stored ones mixes the rule change with
    the fact that only a 600-char excerpt survives. Reading that as "my rules
    did this" credits the rules with work truncation did."""
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1")], "nbPages": 1}])))
    res = sigrun.reclassify(db, rules)
    assert res["comparable"] is False


def test_a_reclassify_with_a_baseline_is_a_clean_comparison(db, rules):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1")], "nbPages": 1}])))
    res = sigrun.reclassify(db, rules, baseline=rules)
    assert res["comparable"] is True
    assert res["changed"] == 0, "the same rules against the same text must not move"


def test_a_dry_run_does_not_write(db, rules):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1")], "nbPages": 1}])))
    before = db.execute(f"SELECT audience FROM {sig.TABLE}").fetchone()["audience"]
    sigrun.reclassify(db, rules, dry_run=True)
    assert db.execute(f"SELECT audience FROM {sig.TABLE}").fetchone()["audience"] == before


# ---- the digest ------------------------------------------------------------

def test_the_digest_groups_by_the_work_that_was_named(db, rules):
    """Grouping on owner_voice would report "thirty people own a business".
    The useful grouping is the work itself."""
    hits = [_hit("1", story_text="I run a small shop and invoicing eats a day a month, we do it manually"),
            _hit("2", story_text="We own a studio; chasing payments and invoicing by hand every week")]
    sigrun.collect(db, rules=rules, queries=["invoicing"], source=_hn(
        FakeHTTP([{"hits": hits, "nbPages": 1}])))
    data = dg.gather(db, rules=rules)
    needs = {g["need"] for g in data["repeated_needs"]}
    assert "invoicing" in needs, data["repeated_needs"]


def test_a_need_mentioned_once_is_not_called_a_trend(db, rules):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1", story_text="I run a shop, payroll is manual")],
                   "nbPages": 1}])))
    data = dg.gather(db, rules=rules)
    assert all(len(g["records"]) >= dg.REPEAT_AT for g in data["repeated_needs"])
    assert data["single_mentions"], "a lone mention must still be listed somewhere"


def test_an_empty_week_produces_a_real_document_saying_so(db, rules, tmp_path):
    """"We found nothing" is a finding about the source. Pretending otherwise
    is how a dead collector goes unnoticed for a month."""
    data = dg.write_digest(db, tmp_path, rules=rules)
    text = (tmp_path / f"digest-{data['window']['to'][:10]}.md").read_text(encoding="utf-8")
    assert "Nothing was collected this week" in text
    assert "This is a finding, not an omission" in text


def test_the_digest_links_every_claim(db, rules, tmp_path):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1"), _hit("2")], "nbPages": 1}])))
    data = dg.write_digest(db, tmp_path, rules=rules)
    text = open(data["path"], encoding="utf-8").read()
    for group in data["repeated_needs"] + data["single_mentions"]:
        for r in group["records"][:8]:
            assert r["source_url"] in text, "a record is claimed without its link"


def test_the_digest_states_what_it_does_not_know(db, rules, tmp_path):
    """The easiest way to make a thin week read as a strong one is to omit
    what the data does not say."""
    data = dg.write_digest(db, tmp_path, rules=rules)
    text = open(data["path"], encoding="utf-8").read()
    assert "## What this does not tell you" in text
    assert "buyer_intent" in text and "unknown" in text
    assert "no outreach" in text.lower() or "produces no outreach" in text


def test_the_digest_never_produces_a_contact(db, rules, tmp_path):
    """"No automatic outreach" on the card, and no Apollo enrichment of
    handles anywhere in this task. A digest that listed authors to message
    would be the first step of exactly that."""
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1", author="someone_identifiable")], "nbPages": 1}])))
    data = dg.write_digest(db, tmp_path, rules=rules)
    text = open(data["path"], encoding="utf-8").read()
    assert "someone_identifiable" not in text


def test_the_digest_reports_the_runs_that_produced_it(db, rules, tmp_path):
    """Records with no run behind them were collected outside the schedule,
    which is worth knowing."""
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(
        FakeHTTP([{"hits": [_hit("1")], "nbPages": 1}])))
    data = dg.write_digest(db, tmp_path, rules=rules)
    assert len(data["runs"]) == 1
    assert "## The runs behind these numbers" in open(data["path"], encoding="utf-8").read()


def test_phrases_that_found_nothing_are_named(db, rules, tmp_path):
    """Cheap to replace — that is the whole argument for phrases over
    communities, and it only works if the dead ones are visible."""
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP()))
    data = dg.gather(db, rules=rules)
    assert data["dead_queries"], "every configured phrase found nothing; say so"
