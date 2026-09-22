"""The console endpoints behind the 2026-09-22 review picks.

Paged ideas and nuggets replaced "hand the browser every row" (1 321 ideas
each with a <select>, 39 745 nuggets in one table); the queue splits comment
and listing batches; /api/trends feeds the Overview sparklines; /api/infra
caches its 3 s of probes; /api/running feeds the strip every page shows.
"""
import json
from pathlib import Path

import pytest

from jester.console.api import ConsoleAPI
from jester.models import Nugget
from jester.store import insert_nugget, open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def api(tmp_path):
    return ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(REPO_CONFIG))


def _nuggets(db, rows):
    """rows: (platform, community, category, insight, trivial)"""
    for i, (platform, community, category, insight, trivial) in enumerate(rows):
        n = Nugget(unique_key=f"{platform}:t:{i}", platform=platform,
                   raw_text=f"raw {i}", extracted_insight=insight, category=category)
        insert_nugget(db, n)
        db.execute("UPDATE nuggets SET community=?, trivial=? WHERE unique_key=?",
                   (community, int(trivial), n.unique_key))
    db.commit()


# ---- nuggets: one page + facet counts --------------------------------------

def test_nuggets_page_returns_a_page_and_the_true_total(api):
    _nuggets(api.db, [("reddit", "r/a", "pain_point", f"insight {i}", False) for i in range(130)])
    res = api.nuggets_page(offset=0, limit=100)
    assert res["ok"] and len(res["nuggets"]) == 100
    assert res["total"] == 130 and res["archive_total"] == 130
    tail = api.nuggets_page(offset=100, limit=100)
    assert len(tail["nuggets"]) == 30


def test_nuggets_facets_count_under_the_other_filters(api):
    _nuggets(api.db, [
        ("reddit", "r/a", "pain_point", "backup died", False),
        ("reddit", "r/b", "feature_request", "backup ui", True),
        ("hackernews", "Ask HN", "pain_point", "backup fails", False),
        ("hackernews", "Ask HN", "pain_point", "onboarding slow", False),
    ])
    res = api.nuggets_page(platform="reddit")
    assert res["total"] == 2
    # The platform facet ignores the platform filter (so you can switch), the
    # community facet honours it (so counts are "what a click would show").
    assert {f["value"]: f["n"] for f in res["facets"]["platform"]} == {"reddit": 2, "hackernews": 2}
    assert {f["value"]: f["n"] for f in res["facets"]["community"]} == {"r/a": 1, "r/b": 1}
    assert res["facets"]["flags"]["trivial"] == 1

    q = api.nuggets_page(q="backup", flag="trivial")
    assert q["total"] == 1 and q["nuggets"][0]["extracted_insight"] == "backup ui"


# ---- ideas: paged + sorted server-side -----------------------------------

def _ideas(db, n):
    for i in range(n):
        db.execute(
            "INSERT INTO ideas (title, problem_statement, proposed_solution, supporting_nuggets, "
            " overall, demand_signal, feasibility, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"Idea {i:03d}", f"problem {i}", "solution", json.dumps([f"k{i}"]),
             float(i % 10), 5.0, 5.0, "new" if i % 3 else "reviewed", f"2026-09-{1 + i % 20:02d} 00:00:00"),
        )
    db.commit()


def test_ideas_are_paged_sorted_and_counted(api):
    _ideas(api.db, 120)
    res = api.ideas(sort="overall", dir="desc", offset=0, limit=50)
    assert res["ok"] and len(res["ideas"]) == 50 and res["total"] == 120
    assert res["ideas"][0]["overall"] == 9.0
    assert res["status_counts"] == {"new": 80, "reviewed": 40}

    page3 = api.ideas(sort="overall", dir="desc", offset=100, limit=50)
    assert len(page3["ideas"]) == 20

    only = api.ideas(status="reviewed", q="idea 00", limit=50)
    assert only["total"] == 4  # 000, 003, 006, 009 are 'reviewed' and match "idea 00"
    assert all(i["status"] == "reviewed" for i in only["ideas"])

    asc = api.ideas(sort="title", dir="asc", limit=1)
    assert asc["ideas"][0]["title"] == "Idea 000"

    everything = api.ideas()
    assert len(everything["ideas"]) == 120, "limit=0 keeps the old all-rows answer"


# ---- queue: comments and listings are two queues --------------------------

def test_queue_separates_kinds(api):
    api.db.execute(
        "INSERT INTO ingest_batch (run_id, platform, source, thread_id, comments, status, kind, listings)"
        " VALUES ('r', 'reddit', 's', 't', ?, 'pending', 'comment', NULL)", (json.dumps([{"body": "a"}, {"body": "b"}]),))
    api.db.execute(
        "INSERT INTO ingest_batch (run_id, platform, source, thread_id, comments, status, kind, listings)"
        " VALUES ('r', 'realestate', 'p', 'l', '[]', 'pending', 'listing', ?)", (json.dumps([{"id": 1}, {"id": 2}, {"id": 3}]),))
    api.db.commit()

    comments = api.queue(kind="comment")
    assert comments["total_batches"] == 1 and comments["total_comments"] == 2
    assert comments["by_kind"] == {"comment": {"batches": 1, "items": 2}, "listing": {"batches": 1, "items": 3}}
    assert [b["platform"] for b in comments["batches"]] == ["reddit"]

    listings = api.queue(kind="listing")
    assert [b["platform"] for b in listings["batches"]] == ["realestate"]

    both = api.queue()
    assert both["total_batches"] == 2, "no kind keeps the old whole-queue answer"


# ---- trends, running, infra cache ------------------------------------------

def test_trends_counts_per_day_and_pads_missing_days(api):
    _nuggets(api.db, [("reddit", "r/a", "pain_point", "x", False)])
    res = api.trends(days=7)
    assert res["ok"] and len(res["days"]) == 7
    assert sum(res["nuggets"]) == 1 and res["nuggets"][-1] == 1, "today's nugget lands in the last slot"
    assert res["ideas"] == [0] * 7 and res["bad_runs"] == [0] * 7


def test_running_lists_only_runs_in_flight(api):
    from jester.store import start_run
    start_run(api.db, "live-1")
    api.db.execute("UPDATE runs SET phase_checkpoints=? WHERE run_id='live-1'",
                   (json.dumps([{"phase": "archive", "at": "now"}]),))
    api.db.commit()
    res = api.running()
    assert [r["run_id"] for r in res["running"]] == ["live-1"]
    assert res["running"][0]["phase"] == "archive"


def test_infra_is_cached_for_thirty_seconds(api, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_infra_probe", lambda: (calls.append(1), {"ok": True, "probe": len(calls)})[1])
    ConsoleAPI._infra_cache.clear()
    first = api.infra()
    second = api.infra()
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1
    assert api.infra(fresh=True)["cached"] is False and len(calls) == 2
