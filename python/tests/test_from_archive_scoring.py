"""score_archive: the bridge between the two pipelines, tested end to end.

The rules it applies are covered in test_hiring_seller_noise. This covers the
function that walks the archive and decides what to write -- which had no
direct test at all, sitting at 38% coverage while being the thing that
actually moves records from one pipeline into the other.

The property that matters most is negative: this must never collect. It
reads rows already on disk. The scope document gates Reddit COLLECTION on
written commercial access, and if this function ever grew a fetch the whole
justification for scheduling it would be gone.
"""
import json

import pytest

from jester.signals import open_signals
from jester.signals.from_archive import hiring_slugs, score_archive
from jester.store import open_db


def _archive(tmp_path, records):
    """A nuggets database holding `records` as one reddit batch each."""
    db = open_db(str(tmp_path / "nuggets.db"))
    for i, (room, bodies) in enumerate(records):
        db.execute(
            "INSERT INTO ingest_batch (platform, source, thread_id, comments, "
            "status, run_id, kind) VALUES ('reddit', ?, ?, ?, 'pending', 'r', 'comment')",
            (f"https://www.reddit.com/r/{room}/", f"t{i}",
             json.dumps([{"id": f"{i}-{j}", "body": b, "author": "someone",
                          "permalink": f"https://reddit.com/r/{room}/c/{i}-{j}"}
                         for j, b in enumerate(bodies)])))
    db.commit()
    return db


CLIENT = ("[Hiring] Senior Full-Stack Engineer ($100-$180/hr) - contract, "
          "remote, React and Node. Apply: jobs@example.com")
SELLER = "[For Hire] I'm a web developer, WordPress and Shopify, $20/hr"
# Title on its own line, then the body -- the shape these records are
# actually stored in. The rule keys on the FIRST LINE ending in "?",
# not on a question mark anywhere in the text, because a client advert
# may perfectly well ask a question halfway down.
QUESTION = "\n".join([
    "Micro1, Mercor and Ethos?",
    "I've been through 10 interviews now, rates $30-100/hr, and I am "
    "starting to doubt these job offers are legit.",
])
DISCUSSION = "\n".join([
    "[Discussion] - devops engineer",
    "What does everyone think?",
])


@pytest.fixture
def run(tmp_path):
    def _go(records, slugs=None):
        nug = _archive(tmp_path, records)
        sig = open_signals(str(tmp_path / "signals.db"))
        try:
            res = score_archive(nug, sig, slugs=slugs or {"r/devsforhire"},
                                rules_path="config/hiring_rules.yaml",
                                run_id="test-run")
            stored = sig.execute(
                "SELECT audience, title FROM problem_signal").fetchall()
            return res, [tuple(r) for r in stored]
        finally:
            nug.close(); sig.close()
    return _go


def test_a_client_advert_is_stored_as_a_buyer(run):
    res, stored = run([("devsforhire", [CLIENT])])
    assert res["seen"] == 1
    assert [a for a, _ in stored] == ["buyer"]


def test_a_seller_advert_never_reaches_the_classifier(run):
    """Discarded on structure, before the prose gets a vote."""
    res, stored = run([("devsforhire", [SELLER])])
    assert res["rejected"].get("seller") == 1
    assert stored == []


def test_a_question_is_discarded_with_its_own_reason(run):
    """The reason is reported separately so a run can say "13 sellers, 14
    questions" rather than one opaque discard count."""
    res, _ = run([("devsforhire", [QUESTION])])
    assert res["rejected"].get("question") == 1


def test_a_discussion_post_is_discarded(run):
    res, _ = run([("devsforhire", [DISCUSSION])])
    assert res["rejected"].get("not_an_advert") == 1


def test_rooms_outside_the_slug_list_are_skipped(run):
    """The caller decides which rooms are in scope. A room that is not named
    must not be read at all, however promising its contents."""
    res, stored = run([("someotherroom", [CLIENT])])
    assert res["seen"] == 0
    assert stored == []


def test_an_empty_read_and_an_empty_verdict_are_different(run):
    """seen=0 means the worker has not fetched those rooms; seen>0 with
    kept=0 means the rules rejected everything. Only the second is a reason
    to look at the rules."""
    res, _ = run([("devsforhire", [SELLER, QUESTION])])
    assert res["seen"] == 2
    assert res["kept"] == 0


def test_a_rerun_does_not_duplicate(run, tmp_path):
    """Scoring is idempotent: it upserts on the record's own id, so running
    it twice over an unchanged archive leaves one row per record."""
    nug = _archive(tmp_path, [("devsforhire", [CLIENT])])
    sig = open_signals(str(tmp_path / "signals.db"))
    try:
        for _ in range(2):
            score_archive(nug, sig, slugs={"r/devsforhire"},
                          rules_path="config/hiring_rules.yaml", run_id="r")
        n = sig.execute("SELECT COUNT(*) FROM problem_signal").fetchone()[0]
    finally:
        nug.close(); sig.close()
    assert n == 1


def test_an_unreadable_payload_does_not_stop_the_walk(tmp_path):
    """One malformed batch must not cost the healthy ones."""
    db = open_db(str(tmp_path / "nuggets.db"))
    db.execute("INSERT INTO ingest_batch (platform, source, thread_id, comments, "
               "status, run_id, kind) VALUES ('reddit', "
               "'https://www.reddit.com/r/devsforhire/', 't0', 'not json', "
               "'pending', 'r', 'comment')")
    db.execute("INSERT INTO ingest_batch (platform, source, thread_id, comments, "
               "status, run_id, kind) VALUES ('reddit', "
               "'https://www.reddit.com/r/devsforhire/', 't1', ?, 'pending', 'r', "
               "'comment')", (json.dumps([{"id": "x", "body": CLIENT}]),))
    db.commit()
    sig = open_signals(str(tmp_path / "signals.db"))
    try:
        res = score_archive(db, sig, slugs={"r/devsforhire"},
                            rules_path="config/hiring_rules.yaml", run_id="r")
    finally:
        db.close(); sig.close()
    assert res["seen"] == 1


# ---- the room list -------------------------------------------------------

def test_hiring_slugs_reads_the_rooms_from_sources_yaml():
    """One home for the room list: posts_only is already the worker's marker
    for "the advert is the post", so adding a subreddit to sources.yaml is
    enough and there is no second list to forget."""
    slugs = hiring_slugs("config/sources.yaml")
    assert "r/forhire" in slugs
    assert all(s.startswith("r/") and s == s.lower() for s in slugs)


def test_a_question_mark_further_down_is_not_a_question_post():
    """The rule is positional. A client advert may ask a question in its body
    -- "Sound like you?" is standard advert copy -- and must survive."""
    from jester.signals.from_archive import not_an_advert

    assert not_an_advert("Senior Go Engineer, contract, $120/hr\n"
                         "Sound like you? Apply below.") == ""
