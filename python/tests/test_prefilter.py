"""M1.3 pre-filter tests (§37.12): pure heuristics + R35 funnel aggregation."""
from pathlib import Path

import pytest

from jester.config import Thresholds
from jester.store import get_runs, open_db, update_run_summary

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

HEALTHY = {
    "body": "Splitting a 40GB CSV in Excel freezes my machine for minutes every time I export the monthly report.",
    "fingerprint": "ok1",
}


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: prefilter_comment rules -------------------------------------------

def test_healthy_comment_passes():
    from jester.prefilter import prefilter_comment

    v = prefilter_comment(HEALTHY, Thresholds())
    assert v.keep and v.reason == ""


def test_reaction_only_dropped():
    from jester.prefilter import prefilter_comment

    v = prefilter_comment({"body": "lol", "fingerprint": "r"}, Thresholds())
    assert not v.keep and v.reason == "reaction_only"


def test_too_short_dropped():
    from jester.prefilter import prefilter_comment

    v = prefilter_comment({"body": "short but not really", "fingerprint": "s"}, Thresholds())
    assert not v.keep and v.reason == "too_short"


def test_too_long_dropped():
    from jester.prefilter import prefilter_comment

    v = prefilter_comment({"body": "x" * 601, "fingerprint": "l"}, Thresholds())
    assert not v.keep and v.reason == "too_long"


def test_emoji_spam_dropped():
    from jester.prefilter import prefilter_comment

    body = ("🔥" * 6) + " this is a long enough comment body to pass the char floor easily"
    v = prefilter_comment({"body": body, "fingerprint": "e"}, Thresholds())
    assert not v.keep and v.reason == "emoji_spam"


def test_mention_spam_dropped():
    from jester.prefilter import prefilter_comment

    body = "@a @b @c @d look at this mess with plenty of words to clear the minimum word count"
    v = prefilter_comment({"body": body, "fingerprint": "m"}, Thresholds())
    assert not v.keep and v.reason == "mention_spam"


def test_too_few_words_dropped():
    from jester.prefilter import prefilter_comment

    v = prefilter_comment(
        {"body": "configuration synchronization fails occasionally", "fingerprint": "w"},
        Thresholds(),
    )  # >=40 chars but only 4 words
    assert not v.keep and v.reason == "too_few_words"


def test_over_budget_dropped():
    from jester.prefilter import prefilter_comment

    cfg = Thresholds(max_comments_per_thread=2)
    v = prefilter_comment(HEALTHY, cfg, already_used=2)
    assert not v.keep and v.reason == "over_budget"


# --- Task 2: run_prefilter funnel -------------------------------------------------

def test_run_prefilter_aggregates_and_suppresses_zeroes():
    from jester.prefilter import run_prefilter

    comments = [
        HEALTHY,
        {"body": "same", "fingerprint": "r"},
        {"body": "x", "fingerprint": "s"},
    ]
    kept, funnel = run_prefilter(comments, Thresholds())
    assert [c["fingerprint"] for c in kept] == ["ok1"]
    assert funnel["kept"] == 1
    assert funnel["reaction_only"] == 1
    assert funnel["too_short"] == 1
    assert "emoji_spam" not in funnel  # zero-suppressed


# --- Task 3: store plumbing --------------------------------------------------------

def test_prefilter_funnel_round_trips(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    from jester.store import start_run, get_run

    rid = start_run(db, "f1")
    update_run_summary(db, "f1", prefilter_funnel={"kept": 2, "reaction_only": 1})
    row = get_run(db, rid)
    import json

    assert json.loads(row["prefilter_funnel"]) == {"kept": 2, "reaction_only": 1}


# --- Task 4: cmd_run wiring ----------------------------------------------------------

def test_cmd_run_records_funnel_and_drops_short_body(tmp_path):
    from jester.cli import cmd_run

    p = tmp_path / "j.db"
    db = open_db(str(p))
    from jester.store import enqueue_batch

    enqueue_batch(db, "reddit", "src", "tid", [
        {"body": HEALTHY["body"], "fingerprint": "keep-me", "upvotes": 10},
        {"body": "this.", "fingerprint": "drop-me", "upvotes": 99},
    ])

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="pf-run"))

    r = get_runs(open_db(str(p)))[0]
    import json

    funnel = json.loads(r["prefilter_funnel"] or "{}")
    assert funnel.get("kept") == 1
    assert funnel.get("reaction_only") == 1
    # The dropped comment never reached the archive.
    keys = [row[0] for row in db.execute("SELECT unique_key FROM nuggets").fetchall()]
    assert all("drop-me" not in k for k in keys)


def test_cmd_runs_renders_filter_token(tmp_path, capsys):
    from jester.cli import cmd_run, cmd_runs

    p = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(p), run="disp"))
    cmd_runs(_ns(db=str(p)))
    assert "filter=" in capsys.readouterr().out
