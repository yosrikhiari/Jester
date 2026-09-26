"""The archive stores comments; people think in posts.

One row per COMMENT, with the post copied onto every one of them:
post_title, post_url, post_author, post_score, post_upvote_ratio,
post_comment_count, post_views. Measured on the live archive: 109,981 rows
standing for 7,123 posts -- 15.4 comments each -- and post_title alone
occupying 5.5 MB where 383 KB would do.

The storage cost is the smaller half. The Nuggets page showed those rows
flat: 1,100 pages in which two replies to the same thread appear as unrelated
entries, with no way to read a discussion as a discussion.

These endpoints do not change how anything is stored. They group by thread so
the post comes first, and they keep the post's own metrics separate from the
comments, because the metrics say whether a thread is worth reading and the
comments are the reading.
"""
import pytest

from jester.console.api import ConsoleAPI
from jester.models import Nugget
from jester.store import insert_nugget, open_db


@pytest.fixture
def api(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    # Two posts: one busy thread we have most of, one we barely sampled.
    for i in range(5):
        insert_nugget(db, Nugget(
            unique_key=f"busy-{i}", platform="reddit", thread_id="t-busy",
            category="pain_point", extracted_insight=f"insight {i}",
            raw_text=f"body {i}", run_id="r", community="r/devops",
            author=f"user{i}", post_title="Our deploys take all afternoon",
            post_author="op", post_score=140, post_comment_count=6,
            post_created_utc="2026-09-20T10:00:00Z", trivial=(i == 0),
        ))
    insert_nugget(db, Nugget(
        unique_key="thin-0", platform="reddit", thread_id="t-thin",
        category="complaint", extracted_insight="only one we caught",
        raw_text="body", run_id="r", community="r/sre", author="someone",
        post_title="Massive thread we barely sampled", post_author="op2",
        post_score=9000, post_comment_count=400,
        post_created_utc="2026-09-21T10:00:00Z",
    ))
    db.close()
    return ConsoleAPI(db_path=str(tmp_path / "j.db"))


# ---- the list --------------------------------------------------------------

def test_the_list_is_posts_not_comments(api):
    """The whole point. Six comment rows must read as two posts."""
    res = api.posts_page()
    assert res["total"] == 2
    assert len(res["posts"]) == 2


def test_each_post_carries_the_comments_we_hold(api):
    by_id = {p["thread_id"]: p for p in api.posts_page()["posts"]}
    assert by_id["t-busy"]["held"] == 5
    assert by_id["t-thin"]["held"] == 1


def test_coverage_is_reported_against_what_the_source_advertised(api):
    """"1 of 400" and "1" are very different claims. Showing only our own
    number implies we have the thread when we have a fraction of it."""
    by_id = {p["thread_id"]: p for p in api.posts_page()["posts"]}
    assert by_id["t-thin"]["advertised"] == 400
    assert by_id["t-busy"]["advertised"] == 6


def test_newest_post_first(api):
    """Ordered by the post's own date, not by when we happened to collect
    it, so the list reads the way the source reads."""
    assert api.posts_page()["posts"][0]["thread_id"] == "t-thin"


def test_the_list_can_be_filtered(api):
    res = api.posts_page(q="deploys")
    assert res["total"] == 1
    assert res["posts"][0]["thread_id"] == "t-busy"


def test_trivial_comments_are_counted_per_post(api):
    """A post whose comments are mostly junk is worth spotting from the list
    rather than after opening it."""
    by_id = {p["thread_id"]: p for p in api.posts_page()["posts"]}
    assert by_id["t-busy"]["trivial"] == 1


# ---- the detail ------------------------------------------------------------

def test_the_detail_separates_metrics_from_comments(api):
    res = api.post("t-busy")
    assert res["ok"] is True
    assert set(res) >= {"metrics", "comments"}
    assert res["metrics"]["score"] == 140
    assert len(res["comments"]) == 5


def test_coverage_says_when_a_thread_is_only_a_sample(api):
    """1 of 400 is 0.25%. A conclusion drawn from that is a conclusion drawn
    from one comment, and the number has to say so."""
    m = api.post("t-thin")["metrics"]
    assert m["comments_advertised"] == 400
    assert m["comments_held"] == 1
    assert m["coverage"] == pytest.approx(0.0025, abs=1e-4)


def test_coverage_is_none_when_the_source_published_no_count(tmp_path):
    """None, never 1.0. An absent count is not a claim of full coverage."""
    db = open_db(str(tmp_path / "k.db"))
    insert_nugget(db, Nugget(unique_key="a", platform="reddit", thread_id="t",
                             category="pain_point", extracted_insight="x",
                             run_id="r", post_title="No count published"))
    db.close()
    assert ConsoleAPI(db_path=str(tmp_path / "k.db")).post("t")["metrics"]["coverage"] is None


def test_an_unknown_post_is_a_clean_miss(api):
    res = api.post("nope")
    assert res["ok"] is False


def test_the_miss_does_not_echo_what_was_asked_for(api):
    """The id arrives from a URL, and the 404 used to quote it back -- a
    taint flow from the request line straight into the response body, which
    Sonar flagged as reflected XSS. Content-Type: application/json makes that
    hard to exploit rather than impossible, and hard is not the bar.
    """
    assert "<script>" not in api.post("<script>alert(1)</script>")["error"]


def test_the_route_refuses_an_id_that_cannot_exist():
    """Validated at the boundary rather than sanitised downstream.

    The pattern is measured, not guessed: all 4,000 distinct thread_ids
    sampled from the live archive match it and the longest is 11 characters,
    so anything outside it cannot name a real post and is refused without
    being repeated back.
    """
    from jester.console.server import _THREAD_ID_RE

    for good in ("1wpoevs", "t-busy", "7507445", "a.b_c:d"):
        assert _THREAD_ID_RE.fullmatch(good), good


def test_the_route_refuses_the_shapes_that_matter():
    from jester.console.server import _THREAD_ID_RE

    for bad in ("<script>", "../../etc/passwd", "a b", "", "x" * 121):
        assert not _THREAD_ID_RE.fullmatch(bad), bad


def test_comments_carry_their_extractor(api):
    """So "stub extraction" can be shown per comment. 95,992 of the live
    archive's nuggets were produced by the stand-in, and a reader needs to
    know which rows those are before trusting the insight."""
    assert "extractor_model" in api.post("t-busy")["comments"][0]
