"""Everything the scrapers capture has to survive the trip to the archive.

The adapters have always been able to see who wrote a comment, when, where to
read it and how it was received. All of it was discarded — first at the point
of capture, and then again in the queue-to-nugget hop, which copied three keys
out of the payload. A nugget could name its platform and its score and nothing
else.

These cover the second half of that: the pipeline reading a widened batch.
The vote fields carry a rule the rest of the suite depends on — NULL means the
platform does not publish that figure, and it is the honest answer far more
often than one would guess:

    downvotes   Reddit stopped publishing per-comment downvotes in 2014
    dislikes    YouTube withdrew dislike counts in December 2021
    upvotes     Hacker News has never published per-comment scores

A 0 in any of those columns would assert that nobody voted, which is a claim
nobody can make.
"""

import json
import sqlite3

import pytest

from jester.agents.extractor import extract
from jester.llm import FakeLLM
from jester.models import Nugget
from jester.store import insert_nugget, open_db


def _comment(**detail):
    base = {
        "body": "the setup docs are wrong and it cost me a weekend",
        "fingerprint": "fp1",
        "upvotes": 12,
        "detail": detail,
    }
    return base


def _extract_one(comment, post=None):
    if post is not None:
        comment = dict(comment, post=post)
    got = extract(
        [comment],
        FakeLLM(),
        platform="reddit",
        thread_id="t1",
        source_url="https://www.reddit.com/r/selfhosted/comments/t1/",
        run_id="r1",
    )
    assert len(got) == 1
    return got[0]


def test_detail_reaches_the_nugget():
    n = _extract_one(
        _comment(
            author="asimovs-auditor",
            author_url="https://www.reddit.com/user/asimovs-auditor/",
            permalink="https://www.reddit.com/r/selfhosted/comments/t1/comment/c1/",
            platform_id="t1_c1",
            created_at="2026-08-26T07:27:21Z",
            depth=2,
            parent_id="t1_c0",
            upvotes=12,
            replies=3,
            awards=1,
            distinguished=True,
        )
    )
    assert n.author == "asimovs-auditor"
    assert n.comment_id == "t1_c1"
    assert n.depth == 2
    assert n.parent_id == "t1_c0"
    assert n.upvotes == 12
    assert n.replies == 3
    assert n.awards == 1
    assert n.distinguished is True
    # created_utc is when the comment was WRITTEN; timestamp is when jester
    # ingested it. The schema could not tell those apart before.
    assert n.created_utc == "2026-08-26T07:27:21Z"
    assert n.timestamp and n.timestamp != n.created_utc


def test_source_url_points_at_the_comment_not_just_the_thread():
    """A reviewer following a citation should land on the sentence, not on a
    thread of four hundred replies."""
    n = _extract_one(
        _comment(permalink="https://www.reddit.com/r/selfhosted/comments/t1/comment/c1/")
    )
    assert n.source_url.endswith("/comment/c1/")


def test_thread_url_is_kept_when_there_is_no_comment_permalink():
    n = _extract_one(_comment(author="someone"))
    assert n.source_url == "https://www.reddit.com/r/selfhosted/comments/t1/"


def test_unpublished_counts_stay_none_not_zero():
    """The rule the whole capture rests on."""
    n = _extract_one(_comment(author="blinkbat", upvotes=None))
    assert n.downvotes is None, "reddit publishes no per-comment downvotes"
    assert n.dislikes is None, "youtube withdrew dislike counts in 2021"
    assert n.likes is None
    # A comment whose score the platform never published must not read as a
    # comment nobody upvoted.
    assert n.upvotes is None


def test_a_published_zero_is_recorded_as_zero():
    n = _extract_one(_comment(awards=0, replies=0))
    assert n.awards == 0
    assert n.replies == 0


def test_flags_are_false_when_captured_and_none_when_not():
    """An absent flag inside a detail block that EXISTS means the adapter
    looked and found nothing set. No detail block at all means nobody looked —
    two different states that must not collapse."""
    captured = _extract_one(_comment(author="x"))
    assert captured.edited is False
    assert captured.pinned is False

    legacy = _extract_one(
        {"body": "an older batch, queued before the widened capture",
         "fingerprint": "fp2", "upvotes": 3}
    )
    assert legacy.edited is None
    assert legacy.pinned is None
    assert legacy.author == ""


def test_post_metadata_reaches_the_nugget():
    n = _extract_one(
        _comment(author="x"),
        post={
            "title": "Centralized secret management options",
            "url": "https://www.reddit.com/r/selfhosted/comments/t1/centralized/",
            "author": "SugarvetFounder",
            "created_at": "2026-08-26T07:27:11Z",
            "score": 41,
            # The only downvote signal any of these platforms publishes.
            "upvote_ratio": 0.6666666666666666,
            "comment_count": 10,
            "community": "r/selfhosted",
        },
    )
    assert n.post_title == "Centralized secret management options"
    assert n.post_author == "SugarvetFounder"
    assert n.post_score == 41
    assert n.post_upvote_ratio == pytest.approx(0.667, abs=0.001)
    assert n.post_comment_count == 10
    assert n.community == "r/selfhosted"


def test_extra_carries_the_platform_specific_tail():
    n = _extract_one(_comment(extra={"trust_level": 3, "post_type": 1}))
    assert n.extra == {"trust_level": 3, "post_type": 1}


# ---- persistence ---------------------------------------------------------


def test_nugget_round_trips_through_sqlite(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    n = Nugget(
        unique_key="reddit:t1:c1",
        platform="reddit",
        raw_text="body",
        author="asimovs-auditor",
        comment_url="https://example.test/c1",
        created_utc="2026-08-26T07:27:21Z",
        upvotes=12,
        likes=None,
        awards=0,
        edited=False,
        pinned=True,
        post_title="Centralized secrets",
        post_upvote_ratio=0.6666666666666666,
        community="r/selfhosted",
        extra={"trust_level": 3},
    )
    insert_nugget(db, n)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM nuggets WHERE unique_key=?", (n.unique_key,)).fetchone()

    assert row["author"] == "asimovs-auditor"
    assert row["created_utc"] == "2026-08-26T07:27:21Z"
    assert row["upvotes"] == 12
    assert row["awards"] == 0
    # NULL, not 0 — the column must be able to say "not published".
    assert row["likes"] is None
    assert row["downvotes"] is None
    # Flags land as 0/1, and False must not become NULL on the way.
    assert row["edited"] == 0
    assert row["pinned"] == 1
    assert row["post_title"] == "Centralized secrets"
    assert row["post_upvote_ratio"] == pytest.approx(0.667, abs=0.001)
    assert json.loads(row["extra"]) == {"trust_level": 3}


def test_export_carries_the_captured_detail(tmp_path):
    """A CSV that names only the score cannot answer "who said this, and
    when" — the first question anyone asks of a scraped corpus."""
    from jester.export import NUGGET_COLUMNS, read_nuggets

    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(
        db,
        Nugget(
            unique_key="reddit:t1:c1",
            platform="reddit",
            raw_text="body",
            author="asimovs-auditor",
            upvotes=12,
            post_title="Centralized secrets",
        ),
    )
    rows = read_nuggets(db)
    assert len(rows) == 1
    for col in ("author", "upvotes", "downvotes", "created_utc", "post_title",
                "post_upvote_ratio", "community"):
        assert col in NUGGET_COLUMNS, f"{col} missing from the CSV contract"
        assert col in rows[0], f"{col} not selected out of the database"
    assert rows[0]["author"] == "asimovs-auditor"
    assert rows[0]["post_title"] == "Centralized secrets"
    # Blank, not 0: a spreadsheet full of zeroes reads as "measured, and
    # nobody voted".
    assert rows[0]["downvotes"] is None


# ---- the queue-to-nugget hop ---------------------------------------------


def test_cmd_run_carries_detail_and_thread_meta_out_of_the_queue(tmp_path):
    """The hop that used to lose everything.

    `cmd_run` read three keys off each queued comment — body, fingerprint,
    upvotes — so a batch could hold the full record and the archive would
    still come out with only a score. Both halves are checked here: the
    per-comment `detail` and the batch-level `thread_meta`.
    """
    from pathlib import Path

    from jester.cli import cmd_run
    from jester.store import enqueue_batch

    db_path = tmp_path / "queue.db"
    db = open_db(str(db_path))
    enqueue_batch(
        db,
        "reddit",
        "https://www.reddit.com/r/selfhosted/comments/t1/",
        "t1",
        [
            {
                "body": "centralized secret management is a mess and I have tried five tools",
                "fingerprint": "fp-detail-1",
                "upvotes": 12,
                "detail": {
                    "author": "asimovs-auditor",
                    "permalink": "https://www.reddit.com/r/selfhosted/comments/t1/comment/c1/",
                    "created_at": "2026-08-26T07:27:21Z",
                    "upvotes": 12,
                    "replies": 2,
                    "depth": 0,
                },
            }
        ],
        run_id="queued",
        thread_meta={
            "title": "Centralized secret management options",
            "author": "SugarvetFounder",
            "score": 41,
            "upvote_ratio": 0.6666666666666666,
            "comment_count": 10,
            "community": "r/selfhosted",
        },
    )

    class _NS:
        pass

    args = _NS()
    args.db = str(db_path)
    args.config = str(Path(__file__).resolve().parents[2] / "config")
    args.run = "detail-run"
    args.exports = str(tmp_path / "exports")
    args.max_ideas = None
    cmd_run(args)

    db2 = open_db(str(db_path))
    db2.row_factory = sqlite3.Row
    row = db2.execute(
        "SELECT * FROM nuggets WHERE raw_text LIKE 'centralized secret%'"
    ).fetchone()
    assert row is not None, "the queued comment was not archived"
    assert row["author"] == "asimovs-auditor"
    assert row["created_utc"] == "2026-08-26T07:27:21Z"
    assert row["upvotes"] == 12
    assert row["replies"] == 2
    assert row["depth"] == 0
    assert row["source_url"].endswith("/comment/c1/")
    assert row["post_title"] == "Centralized secret management options"
    assert row["post_score"] == 41
    assert row["community"] == "r/selfhosted"


def test_a_batch_without_thread_meta_still_processes(tmp_path):
    """Batches queued before the widened capture must keep flowing: a pipeline
    that crashed on them would refuse to drain a queue it had already filled."""
    from pathlib import Path

    from jester.cli import cmd_run
    from jester.store import enqueue_batch

    db_path = tmp_path / "legacy.db"
    db = open_db(str(db_path))
    enqueue_batch(
        db,
        "reddit",
        "https://www.reddit.com/r/selfhosted/comments/t0/",
        "t0",
        [{"body": "an older comment queued by an older worker build", "fingerprint": "fp-legacy", "upvotes": 4}],
        run_id="legacy",
    )

    class _NS:
        pass

    args = _NS()
    args.db = str(db_path)
    args.config = str(Path(__file__).resolve().parents[2] / "config")
    args.run = "legacy-run"
    args.exports = str(tmp_path / "exports")
    args.max_ideas = None
    cmd_run(args)

    db2 = open_db(str(db_path))
    db2.row_factory = sqlite3.Row
    row = db2.execute("SELECT * FROM nuggets WHERE raw_text LIKE 'an older comment%'").fetchone()
    assert row is not None
    assert row["engagement_score"] == 4
    # Nothing was captured, so nothing is claimed.
    assert row["author"] is None or row["author"] == ""
    assert row["post_title"] is None or row["post_title"] == ""
    assert row["edited"] is None
