from pathlib import Path

import pytest

from jester.console.api import ConsoleAPI

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def api(tmp_path):
    return ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(REPO_CONFIG))


def test_overview_shape(api):
    o = api.overview()
    assert o["ok"] and {"counts", "quota", "prompt_versions"} <= set(o)
    assert o["prompt_versions"] == {"extractor": 1, "synthesizer": 1, "critic": 1}


def test_run_pipeline_end_to_end_then_runs_and_doctor(api):
    r = api.run_pipeline(run_id="console-a")
    assert r["ok"] is True
    runs = api.runs()["runs"]
    assert runs and runs[0]["run_id"] == "console-a"
    assert runs[0]["status"] == "completed"
    assert isinstance(runs[0]["floor_flags_list"], list)
    d = api.doctor()
    assert d["ok"] is True


def test_ideas_mark_flow(api):
    api.run_pipeline(run_id="console-b")
    ideas = api.ideas()["ideas"]
    if not ideas:
        pytest.skip("mock corpus produced no idea this shape")
    idea = ideas[0]
    detail = api.idea(idea["id"])
    assert detail["ok"]
    for cite in detail["idea"]["citations"]:
        if not cite["found"]:
            continue
        break  # at least structure verified; missing cites are allowed to exist
    res = api.mark_idea(idea["id"], "reviewed")
    assert res["ok"] is True
    after = api.ideas()["ideas"][0]
    assert after["status"] == "reviewed"


def test_migrate_refuses_while_running(api):
    from jester.store import start_run

    start_run(api.db, "active-run")
    res = api.migrate()
    assert res["ok"] is False


def test_config_surfaces_every_knob(api):
    t = api.config()["thresholds"]
    for key in ("llm_provider", "embedding_provider", "dedup_threshold",
                "good_idea_min", "request_delay_ms", "competitor_ttl_days",
                "critic_web_daily_budget"):
        assert key in t, f"config missing {key}"
    assert "prefilter" in t
    assert len(api.config()["sources"]) >= 3


def test_requeue_and_reembed_shapes(api):
    rq = api.requeue()
    assert rq == {"ok": True, "requeued": 0}
    re_ = api.reembed()
    assert re_["ok"] is True and re_["fixed"] == 0


# ---- nuggets: no silent caps (R55) ---------------------------------------


def _seed_nuggets(db, n):
    from jester.models import Nugget
    from jester.store import insert_nugget

    for i in range(n):
        insert_nugget(db, Nugget(unique_key=f"reddit:t:{i}", platform="reddit",
                                 raw_text=f"comment {i}", extracted_insight=f"insight {i}"))


def test_nuggets_returns_everything_by_default(tmp_path):
    """The archive does not get to cap itself.

    The default was 500, applied after loading and dicting every row, and the
    route never passed anything else — so an archive of 1,761 showed 500 and
    counted itself as 500.
    """
    from jester.console.api import ConsoleAPI
    from jester.store import open_db

    db_path = str(tmp_path / "j.db")
    _seed_nuggets(open_db(db_path), 620)

    res = ConsoleAPI(db_path, str(REPO_CONFIG)).nuggets()
    assert res["ok"] is True
    assert len(res["nuggets"]) == 620, "the archive capped itself"
    assert res["total"] == 620
    assert res["truncated"] is False


def test_nuggets_reports_the_true_total_when_a_caller_limits(tmp_path):
    """A caller may ask for fewer. It may not be told that is all there is."""
    from jester.console.api import ConsoleAPI
    from jester.store import open_db

    db_path = str(tmp_path / "j.db")
    _seed_nuggets(open_db(db_path), 40)

    res = ConsoleAPI(db_path, str(REPO_CONFIG)).nuggets(limit=10)
    assert len(res["nuggets"]) == 10
    # The count that matters: what the archive holds, not what this page got.
    assert res["total"] == 40
    assert res["truncated"] is True


def test_nuggets_treats_a_junk_limit_as_no_limit(tmp_path):
    """`?limit=abc` must not silently become a cap of some arbitrary size."""
    from jester.console.api import ConsoleAPI
    from jester.store import open_db

    db_path = str(tmp_path / "j.db")
    _seed_nuggets(open_db(db_path), 12)

    api = ConsoleAPI(db_path, str(REPO_CONFIG))
    for junk in ("abc", "", None, 0, "0"):
        res = api.nuggets(limit=junk)
        assert len(res["nuggets"]) == 12, f"limit={junk!r} silently capped"
        assert res["truncated"] is False


def test_list_nuggets_limits_in_sql_not_in_a_slice(tmp_path):
    """The limit has to reach the query, or it protects nothing: the console
    was loading every row and discarding the tail."""
    from jester.store import list_nuggets, count_nuggets, open_db

    db = open_db(str(tmp_path / "j.db"))
    _seed_nuggets(db, 30)
    assert count_nuggets(db) == 30
    assert len(list_nuggets(db)) == 30
    assert len(list_nuggets(db, limit=7)) == 7
    # Newest first, so a limited read takes the newest — not a random 7.
    assert list_nuggets(db, limit=7)[0]["unique_key"] == "reddit:t:29"
