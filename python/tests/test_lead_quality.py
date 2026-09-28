"""Lead-quality fixes measured on the live archive on 2026-09-28.

* Reddit records were stored again on every run: the worker keeps a post's
  id, author, link and date under `detail`, the scorer read the top level,
  found nothing, and fell back to an id built from a running counter. 415
  Reddit rows, 286 of them copies; all 63 Reddit "buyers" undated.
* Half of the unique Reddit buyers were data-task gigs or roles with no
  engineering in them.
* Hiring adverts that open with a role put the role in the company column.
* 122 of the 140 rows handed over in leads.csv were adverts months old.
"""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from jester.signals import open_signals
from jester.signals import leads as sigleads
from jester.signals.from_archive import gig_or_not_engineering, score_archive
from jester.signals.sources.hn_hiring import company_from_headline
from jester.store import open_db

CLIENT = ("[Hiring] Senior Full-Stack Engineer ($100-$180/hr) - contract, "
          "remote, React and Node. Apply: jobs@example.com")


def _post(body, pid="t3_abc123", created="2026-09-20T10:00:00Z", **extra):
    """An item in the shape the worker stores today: fields under `detail`."""
    return {"body": body, "fingerprint": extra.pop("fingerprint", "fp1"), "upvotes": "3",
            "detail": {"id": pid, "author": "acme_hiring", "created_at": created,
                       "permalink": f"https://www.reddit.com/r/devsforhire/comments/{pid[3:]}/x/",
                       "depth": 0, **extra}}


def _archive(path, batches, threads=None):
    db = open_db(str(path))
    for i, items in enumerate(batches):
        db.execute(
            "INSERT INTO ingest_batch (platform, source, thread_id, comments, status, run_id, kind) "
            "VALUES ('reddit', 'https://www.reddit.com/r/DevsForHire/', ?, ?, 'pending', 'r', 'comment')",
            ((threads or {}).get(i, f"t{i}"), json.dumps(items)))
    db.commit()
    return db


@pytest.fixture
def score(tmp_path):
    def go(batches, runs=1, threads=None):
        nug = _archive(tmp_path / "nuggets.db", batches, threads)
        sig = open_signals(str(tmp_path / "signals.db"))
        try:
            results = [score_archive(nug, sig, slugs={"r/devsforhire"},
                                     rules_path="config/hiring_rules.yaml", run_id=f"r{n}")
                       for n in range(runs)]
            rows = [dict(r) for r in sig.execute(
                "SELECT source_id, author, source_url, created_utc, audience FROM problem_signal")]
            return results, rows
        finally:
            nug.close()
            sig.close()
    return go


def test_the_posts_own_fields_are_read_from_detail(score):
    _, rows = score([[_post(CLIENT)]])
    assert len(rows) == 1
    row = rows[0]
    assert row["source_id"] == "t3_abc123"
    assert row["author"] == "acme_hiring"
    assert row["created_utc"].startswith("2026-09-20")
    assert row["source_url"].endswith("/comments/abc123/x/")


def test_one_post_in_many_batches_is_one_record(score):
    """The worker re-reads a room every hour; each read is a new batch."""
    results, rows = score([[_post(CLIENT)], [_post(CLIENT)], [_post(CLIENT)]], runs=2)
    assert len(rows) == 1
    assert results[0]["seen"] == 1
    assert results[0]["repeats"] == 2
    assert results[1]["new"] == 0, "a second run must not store the post again"


def test_an_item_without_an_id_still_gets_a_stable_one(score):
    """Older batches carry no id at all. The fallback must not be a counter.
    A re-read of the same post is a new batch in the same thread."""
    legacy = {"body": CLIENT, "fingerprint": "b7e27bc0f28293f0"}
    _, rows = score([[legacy], [legacy]], runs=2, threads={0: "1wpmyqt", 1: "1wpmyqt"})
    assert len(rows) == 1
    assert "fp-b7e27bc0f28293f0" in rows[0]["source_id"]


@pytest.mark.parametrize("headline, reason", [
    ("[Hiring] Get paid $30/hr to role-play talking to an AI assistant", "gig"),
    ("[Hiring] AI Safety Specialists - Remote (US only) | $37 perTask", "gig"),
    ("[Priority Hiring] Generalist Experts - US/Canada(Remote) | $50-$70/hour", "gig"),
    ("[Hiring] Experienced Egocentric Video Contributor - $18/hour", "gig"),
    ("[Hiring] Medical Writers (US/Canada) - Remote | $90-$150/hr", "not_engineering"),
    ("[Hiring] Medical & Health Services Managers - Remote - $70-$110/hr", "not_engineering"),
    ("[Hiring] Senior Full-Stack Engineer ($100-$180/hr)", ""),
    ("QA needed [hiring]", ""),
    ("[REMOTE] Python Developer: Automation, Web Scraping, and API Integration", ""),
    ("[HIRING] Looking for the Native AI Tech Partner (US only)", ""),
    ("[HIRE] - looking for a website designer to create a functional website", ""),
    ("[HIRING] Freelance Midweight Graphic Designer - Luxury Brand - London", "not_engineering"),
    ("[Hiring] Remote Part-Time Cold Caller - Real Estate | $11/hr", "not_engineering"),
])
def test_gigs_and_non_engineering_posts_are_ruled_out(headline, reason):
    assert gig_or_not_engineering(headline + "\nbody text") == reason


def test_a_gig_is_counted_as_rejected_not_stored(score):
    gig = "[Hiring] Get paid $30/hr to role-play talking to an AI assistant\nRemote, flexible."
    results, rows = score([[_post(gig, pid="t3_gig1")]])
    assert results[0]["rejected"] == {"gig": 1}
    assert rows == []


@pytest.mark.parametrize("line, company", [
    ("Noricum | Senior Backend Engineer | REMOTE | Contract", "Noricum"),
    ("LeadIQ | Engineer | Remote", "LeadIQ"),
    ("Senior Python Backend Engineer | REMOTE (EMEA/APAC)", ""),
    ("HIRING: Frontend Developer (Contract) | Remote (UK/EU)", ""),
    ("GTM Engineer | TestGorilla TYPE: Full-time", ""),
    ("No pipe here, just prose", ""),
    ("Hiring: MouseMux | Senior Engineer | Remote", "MouseMux"),
    ("forus - founding security engineer | Remote", "forus"),
    ("Architect Financial Technologies | Backend Engineer", "Architect Financial Technologies"),
])
def test_a_role_in_the_first_field_is_not_a_company(line, company):
    assert company_from_headline(line) == company


def _leads_db(tmp_path, rows):
    db = open_signals(str(tmp_path / "leads.db"))
    for rid, created in rows:
        db.execute(
            "INSERT INTO problem_signal (record_id, mode, platform, source_id, source_url, community, "
            "kind, title, excerpt, text_hash, audience, relevant, match_confidence, created_utc, "
            "first_seen_utc, last_seen_utc) VALUES (?, 'live', 'hackernews', ?, ?, 'hn/hiring', 'post', "
            "'Acme | Engineer | Contract', 'Apply: jobs@acme.example', 'h', 'buyer', 1, 1.0, ?, "
            "'2026-09-24', '2026-09-24')",
            (rid, rid, f"https://news.ycombinator.com/item?id={rid}", created))
    db.commit()
    return db


def test_old_adverts_are_left_out_of_the_handover_and_counted(tmp_path):
    now = datetime.now(timezone.utc)
    fresh = now.strftime("%Y-%m-%dT00:00:00Z")
    db = _leads_db(tmp_path, [("fresh", fresh), ("stale", "2025-12-01T00:00:00Z"), ("undated", "")])
    res = sigleads.write_csv(db, tmp_path / "out")
    assert res["written"] == 2, "the fresh advert and the undated one"
    assert res["too_old"] == 1
    everything = sigleads.write_csv(db, tmp_path / "all", max_age_days=0)
    assert everything["written"] == 3
    assert everything["too_old"] == 0


def test_an_undated_post_is_not_old():
    assert sigleads.too_old({"created_utc": ""}, 60) is False
    assert sigleads.too_old({"created_utc": "2020-01-01"}, 0) is False
