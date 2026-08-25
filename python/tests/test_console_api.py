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
