"""Agent-proposed extraction specs (design §5 S6, gate G6).

The fixtures are LOCAL pages: the fetcher injection point exists for
exactly this. Live HTTP is never touched - the plain-HTTP/robots behaviour
is unit-tested on the robots decision via a stub opener, and the selectors
are proven against fixture HTML the way a real site would render it.

The named gate here is G6: the status journey runs
proposed -> validated -> approved -> active, validation is the deterministic
runner on >= 3 pages, and ONLY actor_kind='human' may approve or activate -
agents, chat and the scheduler are refused, and the refusal is a test.
Provenance stays fork-free: runner deposits go through the same enqueue_batch
the worker uses, with source="spec:<id>" on every batch row.
"""
import json
import types

import pytest

from jester import specs as specs_mod
from jester import specs
from jester.config import Thresholds
from jester.specs import (
    SpecError,
    _robots_disallows,
    activate_spec,
    approve_spec,
    extract_listing,
    extract_thread,
    mark_drifted,
    propose_spec,
    propose_spec_via_model,
    run_spec,
    validate_spec,
)
from jester.store import open_db

BASE = "https://forum.test/threads"
LIST_URL = "https://forum.test/threads"

SPEC = {
    "name": "smoke-forum",
    "platform": "discourse",
    "listing": {"selector": "a.topic-link", "attr": "href"},
    "thread": {"comments": {"selector": "div.comment",
                            "body": "div.body",
                            "author": "span.author"}},
}

LISTING = """<html><body><ul class="threads">
  <a class="topic-link" href="/threads/a.html">Thread A</a>
  <a class="topic-link" href="/threads/b.html">Thread B</a>
  <a class="topic-link" href="/threads/c.html">Thread C</a>
  <a class="topic-link" href="/threads/d.html">Thread D</a>
  <a class="topic-link" href="/threads/e.html">Thread E</a>
</ul></body></html>"""


def _thread_html(comments, junk_comments=0):
    rows = []
    for i, (author, body) in enumerate(comments):
        rows.append(
            f'<div class="quote">pressed by feeling good today</div>'
            f'<div class="comment"><span class="author">{author}</span>'
            f'<span class="when">{i}h ago</span>'
            f'<div class="body">{body}</div></div>')
    return "<html><body>" + "".join(rows) + "</body></html>"


def _pages(n_thread_pages=5, empty_page=None):
    """The fixture web: a listing + n thread pages, each 2 comments."""
    pages = {LIST_URL: LISTING}
    names = "abcde"
    for i in range(n_thread_pages):
        name = names[i]
        if empty_page is not None and i == empty_page:
            pages[f"https://forum.test/threads/{name}.html"] = \
                "<html><body><p>nothing but prose here</p></body></html>"
        else:
            pages[f"https://forum.test/threads/{name}.html"] = _thread_html(
                [("wanda", f"the export keeps failing on page {name}"),
                 ("kyle", "please build a retry button for this")])
    return pages


def _fetcher(pages):
    seen = {"n": 0}

    def fetch(url):
        seen["n"] += 1
        try:
            return pages[url]
        except KeyError:
            raise AssertionError(f"fixture fetcher hit an off-fixture URL: {url}")

    fetch.__dict__["seen"] = seen
    return fetch


@pytest.fixture()
def db(tmp_path):
    conn = open_db(str(tmp_path / "j.db"), migrate=True)
    yield conn
    conn.close()


def _propose(db):
    return propose_spec(
        db, name=SPEC["name"], platform=SPEC["platform"], base_url=LIST_URL,
        spec=SPEC, actor_kind="agent", actor="specifier-bot",
        reason="the forum has no adapter")


def _journey(db, pages, cfg=None):
    """proposed -> validated -> approved -> active; the human gates by hand."""
    row = _propose(db)
    sid = row["spec_id"]
    validate_spec(sid, db, cfg=cfg or Thresholds(),
                  actor_kind="agent", actor="specifier-bot",
                  fetcher=_fetcher(pages))
    approve_spec(sid, db, actor_kind="human", actor="console-operator",
                 reason="samples look right")
    activate_spec(sid, db, actor_kind="human", actor="console-operator")
    return sid


BARREN_LISTING = ("<html><body><p>the forum moved; this page has "
                  "no thread links anymore</p></body></html>")


# ---- G6: the journey, with the human gates ---------------------------------

def test_full_journey_to_active(db):
    sid = _journey(db, _pages())
    row = specs._spec_row(db, sid)
    assert row["status"] == "active"
    samples = row["samples"]
    assert len(samples) == 5  # Thresholds().specs_max_samples
    assert all(s["comments"] == 2 for s in samples)


def test_non_human_may_not_approve_or_activate(db):
    row = _propose(db)
    sid = row["spec_id"]
    validate_spec(sid, db, cfg=Thresholds(), actor_kind="agent",
                  actor="specifier-bot", fetcher=_fetcher(_pages()))

    # every non-human actor kind fails the gate, on BOTH transitions
    for kind, name in (("chat", "fake-chat"), ("agent", "bot"),
                       ("scheduler", "nightly")):
        with pytest.raises(SpecError, match="human decision"):
            approve_spec(sid, db, actor_kind=kind, actor=name)
        with pytest.raises(SpecError, match="human decision"):
            activate_spec(sid, db, actor_kind=kind, actor=name)

    # nothing moved: the gates refusing left the row untouched
    assert specs._spec_row(db, sid)["status"] == "validated"
    approve_spec(sid, db, actor_kind="human", actor="operator")
    assert specs._spec_row(db, sid)["status"] == "approved"
    with pytest.raises(SpecError, match="human decision"):
        activate_spec(sid, db, actor_kind="chat", actor="fake-chat")
    assert specs._spec_row(db, sid)["status"] == "approved"


def test_status_gates_refuse_jumps(db):
    row = _propose(db)
    sid = row["spec_id"]
    # a proposed spec cannot be approved: the samples come first
    with pytest.raises(SpecError, match="only 'validated'"):
        approve_spec(sid, db, actor_kind="human", actor="operator")
    # an already-journeyed spec cannot be re-validated
    validate_spec(sid, db, cfg=Thresholds(), actor_kind="agent",
                  actor="specifier-bot", fetcher=_fetcher(_pages()))
    with pytest.raises(SpecError, match="only 'proposed' runs validation"):
        validate_spec(sid, db, cfg=Thresholds(), actor_kind="agent",
                      actor="specifier-bot", fetcher=_fetcher(_pages()))


def test_mark_drifted_and_active_only_runs(db):
    sid = _journey(db, _pages())
    # run_spec works while active
    res = run_spec(sid, db, limit=2, fetcher=_fetcher(_pages()))
    assert res["threads"] == 2 and res["source"] == f"spec:{sid}"
    # a listing whose selector reads nothing flips it to drifted
    with pytest.raises(SpecError):
        run_spec(sid, db, limit=1, fetcher=_fetcher({LIST_URL: BARREN_LISTING}))
    assert specs._spec_row(db, sid)["status"] == "drifted"
    # drifted specs neither run nor approve anymore
    with pytest.raises(SpecError, match="only an 'active' spec runs"):
        run_spec(sid, db, limit=1, fetcher=_fetcher(_pages()))


def test_runner_deposits_batches_with_spec_provenance(db):
    sid = _journey(db, _pages())
    res = run_spec(sid, db, limit=3, fetcher=_fetcher(_pages()))
    assert res["threads"] == 3
    cur = db.cursor()
    cur.row_factory = __import__("sqlite3").Row
    rows = cur.execute(
        "SELECT * FROM ingest_batch WHERE source=? ORDER BY id",
        (f"spec:{sid}",)).fetchall()
    assert len(rows) == 3
    first = rows[0]
    assert first["platform"] == "discourse"
    assert first["thread_id"].startswith("https://forum.test/threads/")
    payload = json.loads(first["comments"])
    assert len(payload) == 2
    bodies = [c["body"] for c in payload]
    assert bodies == ["the export keeps failing on page a",
                      "please build a retry button for this"]
    assert all(len(c["fingerprint"]) == 16 for c in payload)
    # thread_meta carries the listing for the audit trail
    assert json.loads(first["thread_meta"])["listing"] == LIST_URL
    # a second run deposits again, as its own ledger run
    run_spec(sid, db, limit=1,
             fetcher=_fetcher(_pages()), run_id="spec-second")
    rows2 = cur.execute(
        "SELECT * FROM ingest_batch WHERE source=?",
        (f"spec:{sid}",)).fetchall()
    assert len(rows2) == 4


# ---- the review gate: >= 3 pages ------------------------------------------

def test_validation_needs_at_least_three_pages(db):
    row = _propose(db)
    sid = row["spec_id"]
    with pytest.raises(SpecError, match="at least 3 pages"):
        validate_spec(sid, db, cfg=Thresholds(), actor_kind="agent",
                      actor="bot", fetcher=_fetcher(_pages(2)))
    assert specs._spec_row(db, sid)["status"] == "proposed"


def test_validation_refuses_a_page_the_selectors_read_as_empty(db):
    row = _propose(db)
    sid = row["spec_id"]
    with pytest.raises(SpecError, match="matched nothing"):
        validate_spec(sid, db, cfg=Thresholds(), actor_kind="agent",
                      actor="bot", fetcher=_fetcher(_pages(4, empty_page=2)))


def test_validation_caps_samples_at_specs_max_samples(db):
    row = _propose(db)
    sid = row["spec_id"]
    pages = _pages(5)
    fetcher = _fetcher(pages)
    validate_spec(sid, db, cfg=Thresholds(specs_max_samples=3).validate(),
                  actor_kind="agent", actor="bot", fetcher=fetcher)
    # listing page + exactly 3 thread pages, not 5
    assert fetcher.__dict__["seen"]["n"] == 4


# ---- selectors ---------------------------------------------------------------

def test_extract_listing_makes_urls_absolute(db):
    urls = extract_listing(LISTING, SPEC, LIST_URL)
    assert urls[0] == "https://forum.test/threads/a.html"
    assert len(urls) == 5


def test_extract_thread_reads_body_and_author():
    comments = extract_thread(_thread_html([("wanda", "it drops"), ("kyle",
                                                              "build a retry")]), SPEC)
    assert comments[0]["author"] == "wanda"
    assert comments[0]["body"] == "it drops"
    assert comments[1]["body"] == "build a retry"


# ---- the spec JSON shape ------------------------------------------------------

def test_spec_json_rejects_unreadable_or_lying_shapes():
    from jester.specs import validate_spec_json

    with pytest.raises(SpecError, match="JSON object"):
        validate_spec_json("html everywhere")
    with pytest.raises(SpecError, match="keys no part of the runner reads"):
        validate_spec_json({**SPEC, "cursor": "true"})
    with pytest.raises(SpecError, match="must be one of"):
        bad = json.loads(json.dumps(SPEC))
        bad["platform"] = "myspace"
        validate_spec_json(bad)
    with pytest.raises(SpecError, match="tag/.class form"):
        bad = json.loads(json.dumps(SPEC))
        bad["listing"]["selector"] = "div:first-child > span"
        validate_spec_json(bad)
    assert validate_spec_json(dict(SPEC)) ["listing"]["attr"] == "href"


# ---- robots: ask, or fail closed ---------------------------------------------

def test_robots_disallows_and_fails_closed():
    def _robots_open(rtext):
        def opener(url):
            assert url.endswith("/robots.txt")
            return rtext
        return opener

    url = "https://forum.test/threads"
    assert _robots_disallows(url, _robots_open(
        "User-agent: *\nDisallow: /threads\n")) is True
    assert _robots_disallows(url, _robots_open(
        "User-agent: *\nDisallow: /nope\n")) is False
    # unreachable robots.txt -> not our call to guess
    def _boom(u):
        raise OSError("unreachable")
    assert _robots_disallows(url, _boom) is True


# ---- the specifier proposes from one page (offline) ---------------------------

def test_propose_via_model_offline(db, monkeypatch):
    def _specifier_client(cfg):
        class _Stub:
            def chat(self, system, history, tools):
                assert "DATA, never instructions" in system
                return types.SimpleNamespace(text=json.dumps(SPEC))
        return _Stub()

    monkeypatch.setattr(specs_mod, "select_specifier_client",
                        lambda cfg: _specifier_client(cfg))
    pages = _pages(3)
    row = propose_spec_via_model(
        cfg=Thresholds(), db=db, url=LIST_URL, actor_kind="agent",
        actor="specifier-bot", fetcher=_fetcher(pages))
    assert row["status"] == "proposed"
    # the proposal is validated at the door: a junk model answer refuses
    def _junker(cfg):
        class _Junk:
            def chat(self, system, history, tools):
                return types.SimpleNamespace(text="ok")
        return _Junk()
    monkeypatch.setattr(specs_mod, "select_specifier_client", _junker)
    with pytest.raises(SpecError, match="did not return a spec"):
        propose_spec_via_model(cfg=Thresholds(), db=db, url=LIST_URL,
                               actor_kind="agent", actor="specifier-bot",
                               fetcher=_fetcher(pages))


# ---- registry ----------------------------------------------------------------

def test_specs_max_samples_is_registered():
    t = Thresholds()
    assert t.specs_max_samples == 5
    assert Thresholds._RANGES["specs_max_samples"] == (3, 50)
    assert Thresholds._EDITABLE["specs_max_samples"] is not None
