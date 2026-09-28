"""The Clusters workspace: what the theme list and the idea box read.

The page tags each theme's row with its idea state ("draft 7.0" / "idea 7.0")
straight from the list response, shows the comments a draft was built from,
and drafts in the background so it can show each stage. These pin down the
data behind those three, without a model call.
"""
import json
import time
from pathlib import Path

import pytest

from jester.console.api import ConsoleAPI
from jester.jobs import get_job
from jester.models import Idea, IdeaScores, Nugget
from jester.store import (
    insert_cluster_idea,
    insert_nugget,
    list_clusters,
    open_db,
    promote_cluster_idea,
    replace_clusters,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _seed(db):
    for k in ("n1", "n2", "n3"):
        insert_nugget(db, Nugget(unique_key=k, platform="reddit", thread_id="t1",
                                 raw_text=f"body {k}", extracted_insight=f"i {k}"))
    replace_clusters(db, "r", [{
        "label": "backups · upgrade · backups.", "problem_statement": "ps",
        "size": 3, "n_nuggets": 3, "n_authors": 3, "authors_unknown": 0,
        "n_threads": 2, "platforms": '["reddit"]',
        "nugget_keys": json.dumps(["n1", "n2", "n3"]),
        "coherence": 0.85, "embedding_model": "nomic-embed-text",
        "single_author": False, "single_thread": False,
        "members": [{"nugget_key": "n1", "chunk_index": 0, "text": "x", "similarity": 0.9}],
    }])
    return list_clusters(db)[0]["id"]


def _draft(overall=7.0):
    return Idea(title="Backup Sentinel", problem_statement="p", proposed_solution="s",
                supporting_nuggets=["n1", "n2"],
                scores=IdeaScores(demand_signal=8, feasibility=6, competition=None,
                                  overall=overall),
                synthesis_model="test-model")


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "c.db")
    db = open_db(path)
    cid = _seed(db)
    return path, db, cid


def test_the_list_says_whether_a_theme_has_a_draft_or_a_saved_idea(env):
    path, db, cid = env
    api = ConsoleAPI(path, str(REPO_CONFIG))

    row = api.clusters()["clusters"][0]
    assert row["n_ideas"] == 0
    assert row["idea_best"] is None
    assert row["n_ideas_saved"] == 0

    first = insert_cluster_idea(db, cid, _draft(6.5))
    insert_cluster_idea(db, cid, _draft(7.25))
    row = api.clusters()["clusters"][0]
    assert row["n_ideas"] == 2
    assert row["idea_best"] == 7.25
    assert row["n_ideas_saved"] == 0

    promote_cluster_idea(db, first)
    assert api.clusters()["clusters"][0]["n_ideas_saved"] == 1


def test_a_draft_lists_the_comments_it_was_built_from(env):
    """The full brief matches these against the theme's excerpts, so it has
    to be a list, not the JSON string the table stores."""
    path, db, cid = env
    insert_cluster_idea(db, cid, _draft())
    idea = ConsoleAPI(path, str(REPO_CONFIG)).cluster(cid)["cluster"]["ideas"][0]
    assert idea["supporting_nuggets"] == ["n1", "n2"]


def _wait(path, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = open_db(path)
        try:
            job = get_job(db, job_id)
        finally:
            db.close()
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("the draft job never finished")


def test_drafting_runs_as_a_job_named_after_its_theme(env, monkeypatch):
    """The kind names the theme so a reload mid-draft can pick it back up;
    the stages reach the job row so the page can show them."""
    path, db, cid = env
    stages = []

    def fake_generate(self, cluster_id, progress=None):
        for s in ("reading the theme's 3 comments", "drafting the problem and the solution",
                  "scoring demand, feasibility and competition", "saving the draft"):
            progress(s)
            stages.append(s)
        return {"ok": True, "draft_id": 1, "idea": {"title": "Backup Sentinel"}}

    monkeypatch.setattr(ConsoleAPI, "cluster_generate_idea", fake_generate)
    res = ConsoleAPI(path, str(REPO_CONFIG)).cluster_generate_idea_async(cid)
    assert res["ok"] is True
    assert res["kind"] == f"cluster-idea:{cid}"
    job = _wait(path, res["job_id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["idea"]["title"] == "Backup Sentinel"
    assert [s.split()[0] for s in stages] == ["reading", "drafting", "scoring", "saving"], \
        "the page maps these first words to its step list"


def test_a_failed_draft_is_a_failed_job_not_a_done_one(env, monkeypatch):
    path, db, cid = env
    monkeypatch.setattr(ConsoleAPI, "cluster_generate_idea",
                        lambda self, cluster_id, progress=None:
                        {"ok": False, "error": "the synthesizer quota is spent"})
    res = ConsoleAPI(path, str(REPO_CONFIG)).cluster_generate_idea_async(cid)
    job = _wait(path, res["job_id"])
    assert job["status"] == "error"
    assert "quota" in job["error"]


def test_drafting_an_unknown_theme_is_refused_before_any_job(env):
    path, db, cid = env
    res = ConsoleAPI(path, str(REPO_CONFIG)).cluster_generate_idea_async(cid + 999)
    assert res["ok"] is False
    assert "no cluster" in res["error"]
