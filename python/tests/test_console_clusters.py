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
    DETACHED_CLUSTER_ID,
    insert_cluster_idea,
    insert_nugget,
    list_clusters,
    open_db,
    promote_cluster_idea,
    replace_clusters,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _seed(db, incoherent=False):
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
        "incoherent": incoherent,
        "members": [{"nugget_key": "n1", "chunk_index": 0, "text": "x", "similarity": 0.9}],
    }])
    return list_clusters(db)[0]["id"]


def _n3_only(db):
    """A fresh pass that no longer groups the comments the draft was built on."""
    replace_clusters(db, "r2", [{
        "label": "only n3", "problem_statement": "ps",
        "size": 1, "n_nuggets": 1, "n_authors": 1, "authors_unknown": 0,
        "n_threads": 1, "platforms": '["reddit"]',
        "nugget_keys": json.dumps(["n3"]),
        "coherence": 0.9, "embedding_model": "nomic-embed-text",
        "single_author": False, "single_thread": True,
        "members": [{"nugget_key": "n3", "chunk_index": 0, "text": "x", "similarity": 0.9}],
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


class _RacingSynth:
    """Stands in for the synthesizer — and for what a background
    re-clustering does to the archive WHILE a draft is being drafted:
    `cluster_run_async` works on its own connection, so its committed pass
    deletes the theme and mints fresh ids right inside this window."""

    fallbacks = 0
    last_error = None

    def __init__(self, path, payload):
        self._path, self._payload = path, payload

    def synthesize(self, nuggets):
        wdb = open_db(self._path)
        try:
            replace_clusters(wdb, "r2", self._payload)
        finally:
            wdb.close()
        return Idea(title="Backup Sentinel", problem_statement="p",
                    proposed_solution="s", supporting_nuggets=["n1", "n2"],
                    scores=IdeaScores(demand_signal=8, feasibility=6,
                                      competition=None, overall=7.0),
                    synthesis_model="racing-synth")


def _still_cites_n1_n2():
    return {
        "label": "re-clustered · n1 n2", "problem_statement": "ps",
        "size": 2, "n_nuggets": 2, "n_authors": 2, "authors_unknown": 0,
        "n_threads": 1, "platforms": '["reddit"]',
        "nugget_keys": json.dumps(["n1", "n2"]),
        "coherence": 0.9, "embedding_model": "nomic-embed-text",
        "single_author": False, "single_thread": True,
        "members": [{"nugget_key": "n1", "chunk_index": 0, "text": "x", "similarity": 0.9},
                    {"nugget_key": "n2", "chunk_index": 0, "text": "y", "similarity": 0.9}],
    }


def test_a_recluster_during_drafting_never_inserts_under_a_dead_theme(env, monkeypatch):
    """F14: the theme existence check happens before ~30 s of model work;
    the insert must decide the home at insert time, in the same
    transaction — follow the comments or detach, never a dead id."""
    path, db, cid = env
    monkeypatch.setattr("jester.llm.select_synthesizer_llm",
                        lambda th: _RacingSynth(path, [_still_cites_n1_n2()]))
    monkeypatch.setattr("jester.agents.critic.apply_scores",
                        lambda *a, **k: True)
    api = ConsoleAPI(path, str(REPO_CONFIG))

    res = api.cluster_generate_idea(cid)
    assert res["ok"] is True, res

    home = db.execute("SELECT cluster_id FROM cluster_ideas WHERE id=?",
                      (res["draft_id"],)).fetchone()[0]
    live = {c[0] for c in db.execute("SELECT id FROM clusters")}
    assert home != cid, "cid was deleted mid-flight; a dead id must never be written"
    assert home == DETACHED_CLUSTER_ID or home in live


def test_the_detached_group_is_refused_server_side_not_just_in_the_ui(env):
    """The page hides the draft button; that is not the enforcement. The API
    itself must refuse id 0 on the request path AND as a job, with a message
    that names why - 0 is a sentinel for "no theme cited this draft", not an
    unknown id."""
    path, db, cid = env
    insert_cluster_idea(db, cid, _draft())
    _n3_only(db)  # the draft moves to DETACHED_CLUSTER_ID
    api = ConsoleAPI(path, str(REPO_CONFIG))

    res = api.cluster_generate_idea(0)
    assert res["ok"] is False
    assert "detached" in res["error"]

    res = api.cluster_generate_idea_async(0)
    assert res["ok"] is False
    assert "detached" in res["error"]
    assert res.get("job_id") is None and res.get("job") is None, \
        "the refusal must not have started a job"


def test_the_sync_draft_route_refuses_a_missing_id_the_same_way(env):
    """F16: the job route says "no cluster id given" when the id is absent or
    not a number; the synchronous one took `int(body.get(...) or 0)` and so
    answered with the detached-group refusal — a message about a group the
    operator never mentioned, for a request that mentioned nothing."""
    path, db, cid = env
    api = ConsoleAPI(path, str(REPO_CONFIG))

    for missing in (None, "", "  ", "abc", {}):
        res = api.cluster_generate_idea(missing)
        assert res["ok"] is False, missing
        assert res["error"] == "no cluster id given", missing
        assert api.cluster_generate_idea_async(missing)["error"] == "no cluster id given"


def test_promoting_a_draft_with_an_unreadable_nugget_list_still_saves_it(env):
    """F17: every other reader degrades a corrupt supporting_nuggets to an
    empty list; this one raised, so the save button answered with a crash
    instead of the idea. The draft keeps its own bytes — nothing is lost by
    reading the provenance as unknown."""
    path, db, cid = env
    draft = insert_cluster_idea(db, cid, _draft())
    db.execute("UPDATE cluster_ideas SET supporting_nuggets='not json' WHERE id=?",
               (draft,))
    db.commit()

    out = ConsoleAPI(path, str(REPO_CONFIG)).cluster_save_idea(draft)
    assert out["ok"] is True, out
    saved = db.execute("SELECT * FROM ideas WHERE id=?", (out["idea_id"],)).fetchone()
    assert json.loads(saved["supporting_nuggets"]) == []
    assert saved["source_threads"] == 0 and json.loads(saved["source_platforms"]) == []


def test_an_incoherent_theme_reaches_the_list_and_detail(tmp_path):
    """The labeller can say the members share no subject. That verdict must
    survive the store round-trip so the page can mark it."""
    path = str(tmp_path / "c.db")
    db = open_db(path)
    cid = _seed(db, incoherent=True)
    api = ConsoleAPI(path, str(REPO_CONFIG))

    row = next(c for c in api.clusters()["clusters"] if c["id"] == cid)
    assert row["incoherent"] == 1
    assert api.cluster(cid)["cluster"]["incoherent"] == 1


def test_detached_drafts_are_a_visible_group_not_a_lost_row(env):
    path, db, cid = env
    insert_cluster_idea(db, cid, _draft())
    _n3_only(db)  # the draft's comments no longer share a theme

    api = ConsoleAPI(path, str(REPO_CONFIG))
    groups = {c["id"]: c for c in api.clusters()["clusters"]}
    assert DETACHED_CLUSTER_ID in groups, \
        "a draft outside every theme must still have a row to click"
    detached = groups[DETACHED_CLUSTER_ID]
    assert detached["detached"] is True
    assert detached["n_ideas"] == 1 and detached["n_nuggets"] == 0

    detail = api.cluster(DETACHED_CLUSTER_ID)
    assert detail["ok"] is True
    assert detail["cluster"]["ideas"][0]["title"] == "Backup Sentinel"
