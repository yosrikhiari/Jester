"""Recovering the community a nugget came from (jester.identify).

`community` was added to the schema after the archive already held thousands
of rows, so 1,757 of them showed as "source not recorded" in the console's
grouped view. Most were not anonymous at all — their own source_url names the
community. These tests pin the two properties that make that recovery safe:
each rule reproduces what the Go adapter for that platform writes, and a row
the rules cannot resolve is left alone rather than guessed at.
"""
import sqlite3

import pytest

from jester.identify import RULES, identify


@pytest.fixture()
def db():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE nuggets (id INTEGER PRIMARY KEY, platform TEXT, "
        "source_url TEXT, community TEXT)"
    )
    return con


def seed(db, rows):
    db.executemany(
        "INSERT INTO nuggets (platform, source_url, community) VALUES (?,?,?)", rows
    )
    db.commit()


def communities(db):
    return db.execute(
        "SELECT platform, source_url, community FROM nuggets ORDER BY id"
    ).fetchall()


# ── each rule must reproduce its adapter ─────────────────────────────────────
# These expectations are read off the Go adapters, not invented here: a
# recovered value has to be the same string a re-fetch would write, or the
# console ends up with two buckets for one community.

@pytest.mark.parametrize("platform,url,want", [
    # reddit/dom.go stores what the page says, e.g. "r/datascience".
    ("reddit", "https://www.reddit.com/r/ExperiencedDevs/comments/1abc/x/",
     "r/ExperiencedDevs"),
    # discourse.go stores the bare forum host.
    ("discourse", "https://meta.discourse.org/t/topic/410639", "meta.discourse.org"),
    ("discourse", "https://community.home-assistant.io/t/x/9", "community.home-assistant.io"),
    # stackexchange.go stores the SITE SLUG, not the hostname — and
    # serverfault has no .stackexchange.com suffix to strip.
    ("stackexchange", "https://unix.stackexchange.com/questions/702849", "unix"),
    ("stackexchange", "https://serverfault.com/questions/1", "serverfault"),
    # lemmy.go stores the bare community name: federation makes the local
    # instance the wrong identity.
    ("lemmy", "https://lemmy.world/c/selfhosted", "selfhosted"),
    # github.go stores owner/repo.
    ("github", "https://github.com/home-assistant/core/issues/151223",
     "home-assistant/core"),
    # hackernews has no sub-communities; the site name is the whole answer.
    ("hackernews", "https://news.ycombinator.com/item?id=37392676", "Hacker News"),
])
def test_rule_matches_what_the_adapter_writes(platform, url, want):
    got = RULES[platform](url)
    assert got is not None
    assert got[0] == want


def test_www_is_not_part_of_a_forum_host():
    # Otherwise "www.example.com" and "example.com" become two communities for
    # one forum, which is the failure this whole module exists to avoid.
    assert RULES["discourse"]("https://www.forum.rclone.org/t/1")[0] == "forum.rclone.org"


# ── the feed is not the site ─────────────────────────────────────────────────

def test_hackernews_recovery_refuses_to_name_a_feed():
    """Ask HN / Show HN / front page is the distinction worth having, and it
    is not in the row — the worker knows it at fetch time and nothing else
    does. Recovery must restore the site and stop."""
    community, _url = RULES["hackernews"]("https://news.ycombinator.com/item?id=1")
    assert community == "Hacker News"
    assert community not in {"Ask HN", "Show HN", "HN front page"}


def test_a_foreign_host_is_not_hacker_news():
    assert RULES["hackernews"]("https://example.com/item?id=1") is None


# ── recovery, never invention ────────────────────────────────────────────────

def test_a_row_with_no_real_url_stays_unidentified(db):
    seed(db, [("reddit", "", None), ("reddit", None, None)])
    report = identify(db, apply=True)
    assert report.n_identified == 0
    assert report.unidentified["reddit"]["source_url is not a URL"] == 2
    assert all(c is None for _p, _u, c in communities(db))


def test_fixture_rows_are_named_as_fixtures_not_as_parse_failures(db):
    """source_url = "mock" is the fixture runs' marker. That text was written
    for a test and was never scraped from anywhere, which is a different fact
    from "a URL we could not read" and matters to whoever reads the report."""
    seed(db, [("reddit", "mock", None), ("reddit", "MOCK", None)])
    report = identify(db, apply=True)
    reasons = report.unidentified["reddit"]
    assert list(reasons) == ["fixture row (source_url = 'mock') — never scraped"]
    assert sum(reasons.values()) == 2


def test_a_platform_with_no_rule_is_reported_not_guessed(db):
    # YouTube stores the channel NAME; a watch URL carries only a video id, so
    # there is nothing to recover and saying so is the correct outcome.
    seed(db, [("youtube", "https://www.youtube.com/watch?v=abc123", None)])
    report = identify(db, apply=True)
    assert report.n_identified == 0
    assert report.unidentified["youtube"]["no rule for this platform"] == 1


def test_a_url_that_names_no_community_is_left_alone(db):
    seed(db, [("reddit", "https://www.reddit.com/", None),
              ("github", "https://github.com/", None)])
    report = identify(db, apply=True)
    assert report.n_identified == 0
    assert report.n_unidentified == 2


def test_rows_that_already_know_are_never_touched(db):
    seed(db, [("reddit", "https://www.reddit.com/r/devops/x", "r/DevOps")])
    report = identify(db, apply=True)
    # It was not missing, so it was never a candidate — the recovered spelling
    # does not get to overwrite what the adapter actually read off the page.
    assert report.n_identified == 0
    assert communities(db)[0][2] == "r/DevOps"


# ── the pass itself ──────────────────────────────────────────────────────────

def test_dry_run_changes_nothing(db):
    seed(db, [("discourse", "https://meta.discourse.org/t/x/1", None)])
    report = identify(db, apply=False)
    assert report.n_identified == 1
    assert report.applied is False
    assert communities(db)[0][2] is None


def test_apply_writes_and_is_idempotent(db):
    seed(db, [("discourse", "https://meta.discourse.org/t/x/1", None),
              ("reddit", "https://www.reddit.com/r/homelab/c/1", None)])
    first = identify(db, apply=True)
    assert first.n_identified == 2
    assert [c for _p, _u, c in communities(db)] == ["meta.discourse.org", "r/homelab"]

    # Nothing is missing any more, so a second pass has no work to do.
    second = identify(db, apply=True)
    assert second.n_identified == 0
    assert second.n_unidentified == 0


def test_blank_and_whitespace_communities_count_as_missing(db):
    seed(db, [("reddit", "https://www.reddit.com/r/homelab/c/1", "   ")])
    assert identify(db, apply=True).n_identified == 1
    assert communities(db)[0][2] == "r/homelab"


def test_report_lines_state_both_halves(db):
    seed(db, [("discourse", "https://meta.discourse.org/t/x/1", None),
              ("reddit", "mock", None)])
    lines = "\n".join(identify(db, apply=False).lines())
    assert "would identify 1" in lines
    # R55: the half it could not do has to be on screen too.
    assert "still unidentified: 1" in lines
    assert "fixture row" in lines
