import json
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


# ---- search: the archive was searchable and had no search ----------------


def _seed_searchable(db):
    from jester.models import Nugget
    from jester.store import insert_nugget

    for k, plat, text in [
        ("a", "reddit", "my backup cron dies without telling me and I lose a week"),
        ("b", "hackernews", "kubernetes upgrades break my ingress every single time"),
        ("c", "discourse", "the docs are wrong about how to restore a snapshot"),
    ]:
        insert_nugget(db, Nugget(unique_key=k, platform=plat, raw_text=text,
                                 extracted_insight=text, category="pain_point"))


def test_search_needs_a_query(api):
    res = api.search(q="   ")
    assert res["ok"] is False
    assert "search for" in res["error"]


def test_search_finds_by_text_when_vectors_are_unavailable(api):
    """The archive ships with embedding_provider: fake in test config, so this
    exercises the fallback — which must work rather than return a dead box."""
    _seed_searchable(api.db)
    res = api.search(q="backup cron")
    assert res["ok"] is True
    assert res["returned"] >= 1
    assert any("backup cron" in (r["raw_text"] or "") for r in res["results"])


def test_search_says_which_search_answered(api):
    """Semantic and substring return very different things. A user who thinks
    they got one when they got the other draws the wrong conclusion from an
    empty result."""
    _seed_searchable(api.db)
    res = api.search(q="backup")
    assert res["mode"] in ("semantic", "text")
    if res["mode"] == "text":
        assert res["detail"], "a fallback must explain itself"


def test_search_refuses_to_rank_by_hash_collision(api):
    """With embedding_provider: fake the vectors are SHA-256. Ranking by them
    would look like semantic search and be noise, so it must not claim to be
    semantic."""
    _seed_searchable(api.db)
    res = api.search(q="anything at all")
    if (api._cfg().thresholds.embedding_provider or "").lower() == "fake":
        assert res["mode"] == "text"
        assert "fake" in res["detail"]


def test_search_filters_apply_after_retrieval(api):
    _seed_searchable(api.db)
    res = api.search(q="break", platform="hackernews")
    assert all(r["platform"] == "hackernews" for r in res["results"])


def test_search_limit_is_bounded(api):
    _seed_searchable(api.db)
    assert api.search(q="the", limit=99999)["returned"] <= 200
    assert api.search(q="the", limit="nonsense")["ok"] is True


# ── console static assets ────────────────────────────────────────────────────
# app.js is not covered by any test runner, so a typo in an element id is
# invisible until the page is opened — and because the module wires listeners
# at load, one `$('#missing').addEventListener` throws and blanks EVERY tab,
# not just the one that owns the element. These two checks are cheap and catch
# the whole class.

STATIC = Path(__file__).resolve().parents[1] / "jester" / "console" / "static"


def _static(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_element_id_the_console_script_wants_exists():
    import re

    js, html = _static("app.js"), _static("index.html")
    # Scan CODE, not prose. A comment explaining why a lookup was avoided
    # contains the very pattern this searches for, and flagging that as a
    # missing element would make the test dictate how comments are worded.
    code = "\n".join(
        line for line in js.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    wanted = sorted(set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", code)))
    assert wanted, "expected app.js to look elements up by id"
    have = set(re.findall(r'id="([^"]+)"', html))
    assert [i for i in wanted if i not in have] == []


def test_console_markup_declares_no_duplicate_ids():
    import re
    from collections import Counter

    ids = re.findall(r'id="([^"]+)"', _static("index.html"))
    dupes = sorted(n for n, c in Counter(ids).items() if c > 1)
    # $() returns the FIRST match, so a duplicate id silently wires half the
    # page to the wrong element.
    assert dupes == []


# ── the queue (scraped, not yet treated) ─────────────────────────────────────
# Ingestion and treatment run on separate clocks by design, so the gap between
# them is a standing state that can hold tens of thousands of comments for
# days. It was visible only as one number on the Overview page.

def _seed_queue(api, rows):
    """rows: (status, platform, source, comments_json)"""
    api.db.execute(
        "CREATE TABLE IF NOT EXISTS ingest_batch ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, platform TEXT, source TEXT,"
        " thread_id TEXT, batch_index INTEGER DEFAULT 0, comments TEXT, thread_meta TEXT,"
        " status TEXT DEFAULT 'pending', claimed_at TEXT, attempts INTEGER DEFAULT 0,"
        " error TEXT, created_at TEXT DEFAULT (datetime('now')))"
    )
    for status, platform, source, comments in rows:
        api.db.execute(
            "INSERT INTO ingest_batch (run_id, platform, source, thread_id, comments, status)"
            " VALUES ('r1', ?, ?, ?, ?, ?)",
            (platform, source, source, comments, status),
        )
    api.db.commit()


def test_queue_counts_without_loading_the_bodies(api):
    """646 batches hold tens of megabytes of comment JSON. A page that parses
    all of it to show a count is a page nobody opens twice, so the list carries
    counts and the bodies are fetched one batch at a time."""
    big = json.dumps([{"body": "x" * 50, "fingerprint": f"f{i}"} for i in range(40)])
    _seed_queue(api, [("pending", "hackernews", "hn-1", big),
                      ("pending", "reddit", "r-1", json.dumps([{"body": "a"}]))])
    res = api.queue()
    assert res["ok"]
    assert res["total_batches"] == 2
    assert res["total_comments"] == 41
    for b in res["batches"]:
        assert "comments" not in b, "the list must not carry comment bodies"
        assert b["n_comments"] is not None


def test_queue_says_what_it_is_showing(api):
    """R55: a capped list must never read as the whole queue."""
    _seed_queue(api, [("pending", "hackernews", f"hn-{i}", json.dumps([{"body": "b"}]))
                      for i in range(12)])
    res = api.queue(limit=5)
    assert res["shown"] == 5
    assert res["total_batches"] == 12
    assert res["truncated"] is True
    assert api.queue(limit=50)["truncated"] is False


def test_queue_shows_where_the_backlog_came_from(api):
    """A queue that is mostly one platform is a skew about to be baked into the
    archive, not a backlog — better seen before it is treated."""
    hn = json.dumps([{"body": "b"} for _ in range(90)])
    rd = json.dumps([{"body": "b"} for _ in range(10)])
    _seed_queue(api, [("pending", "hackernews", "hn-1", hn),
                      ("pending", "reddit", "r-1", rd)])
    mix = {r["platform"]: r for r in api.queue()["by_platform"]}
    assert mix["hackernews"]["comments"] == 90
    assert mix["reddit"]["comments"] == 10
    # Ordered by weight, so the dominant one leads.
    assert api.queue()["by_platform"][0]["platform"] == "hackernews"


def test_queue_separates_statuses(api):
    _seed_queue(api, [("pending", "hackernews", "a", json.dumps([{"body": "b"}])),
                      ("failed", "reddit", "b", json.dumps([{"body": "b"}])),
                      ("done", "lemmy", "c", json.dumps([{"body": "b"}]))])
    assert api.queue("pending")["total_batches"] == 1
    assert api.queue("failed")["total_batches"] == 1
    assert api.queue("done")["total_batches"] == 1
    # And the totals name all three, so the page can show the whole picture.
    assert set(api.queue()["totals"]) == {"pending", "failed", "done"}


def test_an_unreadable_batch_is_not_reported_as_empty(api):
    """A batch whose JSON will not parse is WHY it is stuck. Counting it as 0
    comments hides a defect behind a plausible number."""
    _seed_queue(api, [("pending", "reddit", "broken", "{not json")])
    res = api.queue()
    assert res["batches"][0]["n_comments"] is None
    detail = api.queue_batch(res["batches"][0]["id"])
    assert detail["ok"] is False
    assert "unreadable" in detail["error"]


def test_opening_a_batch_returns_its_comments_and_post(api):
    _seed_queue(api, [("pending", "steam", "app-1",
                       json.dumps([{"body": "crashes on export", "fingerprint": "f1"}]))])
    bid = api.queue()["batches"][0]["id"]
    api.db.execute("UPDATE ingest_batch SET thread_meta=? WHERE id=?",
                   (json.dumps({"title": "Aseprite", "community": "Aseprite"}), bid))
    api.db.commit()
    res = api.queue_batch(bid)
    assert res["ok"]
    assert res["comments"][0]["body"] == "crashes on export"
    assert res["post"]["title"] == "Aseprite"


def test_a_missing_batch_is_refused(api):
    _seed_queue(api, [])
    assert api.queue_batch(99999)["ok"] is False


def test_the_queue_reads_the_block_the_treat_command_writes(api):
    """The console originally looked up thresholds.llm_provider, which is
    "fake" in the default profile — so it read nothing and showed a queue as
    free to drain while treatment was in fact shut for another 22 minutes.
    cmd_treat files the block under one literal name and this must match it."""
    from jester import llm_quota

    _seed_queue(api, [])
    llm_quota.record_block(api.db, llm_quota.TREATMENT_PROVIDER, 900, reason="daily cap")
    t = api.queue()["treatment"]
    assert t["provider"] == llm_quota.TREATMENT_PROVIDER
    assert t["blocked"] is True
    assert t["seconds_remaining"] > 0
    assert "daily cap" in t["reason"]


def test_a_queue_on_a_database_with_no_batches_table_is_empty_not_an_error(api):
    """ingest_batch is Go-owned; a database the worker has never opened does
    not have it, and the Queue page must still render."""
    res = api.queue()
    assert res["ok"] is True
    assert res["total_batches"] == 0
    assert res["batches"] == []


# ── extraction provenance (D1 groundwork) ────────────────────────────────────
# The extractor runs once per comment against a 1,000-request daily allowance,
# so any run over the 17,000-comment queue crosses into the deterministic
# stand-in partway through. Synthesis has refused to archive its own degraded
# output for months; extraction archived it silently, and nothing on the row
# said which rows were real.

def test_every_draft_names_the_model_that_made_it():
    from jester.llm import FakeLLM, NuggetDraft

    d = FakeLLM().extract("the backup fails silently every single week")
    assert d.model == FakeLLM.name
    # The field exists on the dataclass rather than being bolted on by callers.
    assert "model" in NuggetDraft.__dataclass_fields__


def test_a_real_extractor_names_itself_and_its_fallback_names_the_standin():
    from jester.llm import FakeLLM, OllamaLLM

    class Answers:
        def chat(self, **kw):
            return {"message": {"content":
                    '{"category":"pain_point","insight":"Backups fail silently."}'}}

    class Refuses:
        def chat(self, **kw):
            return {"message": {"content": "not json at all"}}

    good = OllamaLLM(model="some-model", client=Answers()).extract("x")
    assert good.model == "some-model"
    assert good.extracted_insight == "Backups fail silently."

    bad = OllamaLLM(model="some-model", client=Refuses()).extract("x")
    # The row must say the STAND-IN produced it, not the model that failed.
    assert bad.model == FakeLLM.name


def test_the_nugget_carries_the_extractor_that_made_it():
    from jester.agents.extractor import extract
    from jester.llm import FakeLLM

    nuggets = extract(
        [{"body": "every deploy breaks the staging database", "fingerprint": "f1"}],
        FakeLLM(), platform="reddit", thread_id="t1",
        source_url="https://example.invalid/t1", run_id="r1",
    )
    assert nuggets[0].extractor_model == FakeLLM.name


def test_provenance_survives_a_round_trip_through_the_archive(api):
    from jester.models import Nugget
    from jester.store import insert_nugget, list_nuggets

    insert_nugget(api.db, Nugget(
        unique_key="reddit:t1:c1", platform="reddit", raw_text="x",
        extracted_insight="y", category="pain_point",
        extractor_model="openai/gpt-oss-20b",
    ))
    api.db.commit()
    row = next(r for r in list_nuggets(api.db) if r["unique_key"] == "reddit:t1:c1")
    assert row["extractor_model"] == "openai/gpt-oss-20b"
