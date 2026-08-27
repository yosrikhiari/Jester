"""Source parsing / persistence (jester.sources) and the console CRUD on top."""
import shutil
from pathlib import Path

import pytest

from jester.config import (
    ConfigError,
    Thresholds,
    load_thresholds,
    save_models,
    save_thresholds,
)
from jester.console.api import ConsoleAPI
from jester.sources import (
    PLATFORM_KINDS,
    Source,
    SourceError,
    duplicate_names,
    load_sources,
    parse_source,
    save_sources,
    unique_name,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def config_dir(tmp_path):
    """A writable copy of the repo config, so tests never edit the real one."""
    dst = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, dst)
    return dst


@pytest.fixture()
def api(tmp_path, config_dir):
    return ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(config_dir))


# ---- parsing ---------------------------------------------------------------


@pytest.mark.parametrize("raw,platform,kind,url", [
    ("r/selfhosted", "auto", "subreddit", "https://www.reddit.com/r/selfhosted/"),
    ("/r/homelab/", "auto", "subreddit", "https://www.reddit.com/r/homelab/"),
    ("selfhosted", "reddit", "subreddit", "https://www.reddit.com/r/selfhosted/"),
    ("https://www.reddit.com/r/selfhosted/", "auto", "subreddit",
     "https://www.reddit.com/r/selfhosted/"),
    ("https://www.reddit.com/r/selfhosted/comments/1abc2d/some_slug/", "auto", "thread",
     "https://www.reddit.com/r/selfhosted/comments/1abc2d/"),
    ("@fosdem", "youtube", "channel", "https://www.youtube.com/@fosdem"),
    ("https://www.youtube.com/@fosdem/videos", "auto", "channel",
     "https://www.youtube.com/@fosdem"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "auto", "video",
     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=30", "auto", "video",
     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "auto", "video",
     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("@indiehackers", "tiktok", "profile", "https://www.tiktok.com/@indiehackers"),
    ("https://www.tiktok.com/@indiehackers/video/7300000000000000000", "auto", "video",
     "https://www.tiktok.com/@indiehackers/video/7300000000000000000"),
    ("#buildinpublic", "tiktok", "hashtag", "https://www.tiktok.com/tag/buildinpublic"),
])
def test_parse_source_canonicalises(raw, platform, kind, url):
    s = parse_source(raw, platform)
    assert (s.kind, s.url) == (kind, url)
    assert s.enabled is True
    assert s.name  # always derives something usable as an id


def test_parse_source_platform_is_inferred_from_the_link():
    assert parse_source("https://www.tiktok.com/@indiehackers").platform == "tiktok"
    assert parse_source("https://youtu.be/dQw4w9WgXcQ").platform == "youtube"


def test_parse_source_rejects_a_platform_that_contradicts_the_link():
    with pytest.raises(SourceError, match="reddit link"):
        parse_source("https://www.reddit.com/r/selfhosted/", "youtube")


def test_parse_source_rejects_junk():
    with pytest.raises(SourceError):
        parse_source("not a link at all")
    with pytest.raises(SourceError):
        parse_source("", "reddit")


def test_supported_flags_match_the_worker_adapters():
    assert parse_source("r/selfhosted").supported is True
    assert parse_source("@fosdem", "youtube").supported is True
    # TikTok is configurable but has no adapter yet — say so, do not pretend.
    assert parse_source("@indiehackers", "tiktok").supported is False


def test_unique_name_suffixes_collisions():
    existing = [Source(name="selfhosted", platform="reddit", url="x")]
    assert unique_name(existing, "selfhosted") == "selfhosted-2"
    assert unique_name(existing, "homelab") == "homelab"
    assert unique_name(existing, "selfhosted", skip="selfhosted") == "selfhosted"


# ---- persistence -----------------------------------------------------------


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "sources.yaml"
    original = [
        parse_source("r/selfhosted"),
        parse_source("@fosdem", "youtube", enabled=False, notes="paused"),
    ]
    save_sources(path, original)
    again = load_sources(path)
    assert [(s.name, s.platform, s.kind, s.url, s.enabled) for s in again] == \
           [(s.name, s.platform, s.kind, s.url, s.enabled) for s in original]
    assert again[1].notes == "paused"


def test_legacy_entries_without_kind_still_load(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  - platform: reddit\n    name: selfhosted\n    url: https://www.reddit.com/r/selfhosted/\n"
        "  - platform: youtube\n    name: fosdem\n    url: https://www.youtube.com/@fosdem\n",
        encoding="utf-8",
    )
    got = load_sources(path)
    assert [s.kind for s in got] == ["subreddit", "channel"]
    assert all(s.enabled for s in got)  # absent `enabled` means on


def test_missing_file_loads_as_empty(tmp_path):
    assert load_sources(tmp_path / "nope.yaml") == []


# ---- console CRUD ----------------------------------------------------------


def test_add_source_writes_through_to_yaml(api, config_dir):
    before = len(api.sources()["sources"])
    res = api.add_source("r/kubernetes")
    assert res["ok"] is True
    assert res["source"]["kind"] == "subreddit"
    assert len(load_sources(config_dir / "sources.yaml")) == before + 1
    assert any(s["name"] == "kubernetes" for s in api.sources()["sources"])


def test_add_source_refuses_a_duplicate_url(api):
    api.add_source("r/kubernetes")
    res = api.add_source("https://www.reddit.com/r/kubernetes/")
    assert res["ok"] is False and "already" in res["error"]


def test_add_source_surfaces_the_parse_error(api):
    res = api.add_source("nonsense")
    assert res["ok"] is False and "platform" in res["error"]


def test_toggle_and_delete_source(api, config_dir):
    api.add_source("r/kubernetes")
    assert api.update_source("kubernetes", enabled=False)["ok"] is True
    assert [s for s in load_sources(config_dir / "sources.yaml") if s.name == "kubernetes"][0].enabled is False

    assert api.delete_source("kubernetes")["ok"] is True
    assert not [s for s in load_sources(config_dir / "sources.yaml") if s.name == "kubernetes"]
    assert api.delete_source("kubernetes")["ok"] is False


def test_rename_source_keeps_names_unique(api):
    api.add_source("r/kubernetes")
    api.add_source("r/rust")
    res = api.update_source("rust", new_name="kubernetes")
    assert res["ok"] is True
    assert res["source"]["name"] == "kubernetes-2"


def test_sources_payload_carries_the_ui_vocabulary(api):
    payload = api.sources()
    assert payload["ok"] is True
    assert set(payload["platform_kinds"]) == {
        "reddit", "hackernews", "discourse", "youtube", "tiktok",
        "stackexchange", "github", "lemmy", "steam"}
    assert ["reddit", "subreddit"] in payload["supported_kinds"]
    # Stack Exchange and GitHub are sanctioned APIs, so unlike tiktok they
    # ship with adapters behind them rather than as configurable placeholders.
    assert ["stackexchange", "site"] in payload["supported_kinds"]
    assert ["github", "repo"] in payload["supported_kinds"]
    # tiktok remains the one platform that is configurable and unimplemented,
    # deliberately (config/scraper.yaml §35.3).
    assert ["tiktok", "profile"] not in payload["supported_kinds"]
    assert all("supported" in s for s in payload["sources"])


def test_overview_summarises_sources(api):
    assert api.add_source("@buildinpublic", "tiktok")["ok"] is True
    summary = api.overview()["sources_summary"]
    assert summary["total"] >= 1
    assert summary["unsupported"] >= 1  # the TikTok entry has no adapter


def test_writes_stay_inside_the_configured_dir(api, config_dir, tmp_path):
    """A console pointed at a fixture dir must not touch the repo's config-live."""
    live = Path(__file__).resolve().parents[2] / "config-live" / "sources.yaml"
    before = live.read_text(encoding="utf-8") if live.exists() else None
    api.add_source("r/netsec")
    assert (config_dir / "sources.yaml").read_text(encoding="utf-8").count("name: netsec") == 1
    if before is not None:
        assert live.read_text(encoding="utf-8") == before


# ---- threshold editing -----------------------------------------------------


def test_save_thresholds_preserves_comments_and_untouched_keys(config_dir):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")
    assert before.lstrip().startswith("#")

    save_thresholds(path, {"min_upvotes": 4, "dedup_threshold": 0.9})
    after = path.read_text(encoding="utf-8")

    assert after.lstrip().startswith("#")            # header comment survived
    assert "models:" in after and "extractor:" in after  # nested block untouched
    t = load_thresholds(path)
    assert t.min_upvotes == 4
    assert t.dedup_threshold == 0.9
    assert t.prefilter_min_chars == 40               # untouched key unchanged


def test_save_thresholds_appends_a_key_the_file_never_had(config_dir):
    path = config_dir / "thresholds.yaml"
    save_thresholds(path, {"good_idea_min": 8.0})
    assert load_thresholds(path).good_idea_min == 8.0
    # the appended key goes before `models:` so the nested block stays last
    body = path.read_text(encoding="utf-8")
    assert body.index("good_idea_min") < body.index("models:")


def test_save_thresholds_refuses_out_of_range_without_writing(config_dir):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        save_thresholds(path, {"dedup_threshold": 2.0})
    assert path.read_text(encoding="utf-8") == before


def test_save_thresholds_refuses_unknown_and_non_numeric_keys(config_dir):
    path = config_dir / "thresholds.yaml"
    with pytest.raises(ConfigError, match="not an editable threshold"):
        save_thresholds(path, {"critic_model": "gpt-4"})
    with pytest.raises(ConfigError):
        save_thresholds(path, {"min_upvotes": "many"})


def test_cross_field_rule_uses_the_file_not_the_defaults(config_dir):
    path = config_dir / "thresholds.yaml"
    save_thresholds(path, {"prefilter_max_chars": 5000})
    save_thresholds(path, {"prefilter_min_chars": 900})   # legal against 5000
    assert load_thresholds(path).prefilter_min_chars == 900
    with pytest.raises(ConfigError, match="prefilter_min_chars"):
        save_thresholds(path, {"prefilter_min_chars": 6000})


def test_console_threshold_update_returns_the_applied_patch(api, config_dir):
    res = api.update_thresholds({"min_upvotes": 3})
    assert res["ok"] is True and res["applied"] == {"min_upvotes": 3}
    assert load_thresholds(config_dir / "thresholds.yaml").min_upvotes == 3

    bad = api.update_thresholds({"min_upvotes": -5})
    assert bad["ok"] is False and "min_upvotes" in bad["error"]


def test_thresholds_validate_rejects_bad_provider():
    with pytest.raises(ConfigError, match="llm_provider"):
        Thresholds(llm_provider="openai").validate()


# ---- agent models ----------------------------------------------------------


def test_save_models_edits_the_nested_block_in_place(config_dir):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")
    assert before.lstrip().startswith("#")
    # "Untouched" is a claim about this call, so it is measured against what
    # the file said a moment ago. Hardcoding the dataclass default made the
    # assertion a claim about the repo's shipped config instead, and it broke
    # the day someone set models.archivist there.
    archivist_before = load_thresholds(path).archivist_model
    upvotes_before = load_thresholds(path).min_upvotes

    applied = save_models(path, {"extractor": "qwen3:8b", "critic": "gemma3:12b"})
    assert applied == {"extractor": "qwen3:8b", "critic": "gemma3:12b"}

    body = path.read_text(encoding="utf-8")
    assert body.lstrip().startswith("#")          # header comment survived
    t = load_thresholds(path)
    assert t.extractor_model == "qwen3:8b"
    assert t.critic_model == "gemma3:12b"
    assert t.archivist_model == archivist_before   # untouched role
    assert t.min_upvotes == upvotes_before         # top-level untouched


def test_save_models_keeps_ollama_tags_unquoted(config_dir):
    path = config_dir / "thresholds.yaml"
    save_models(path, {"synthesizer": "qwen3:8b"})
    assert "synthesizer: qwen3:8b" in path.read_text(encoding="utf-8")


def test_save_models_handles_the_top_level_embedding_key(config_dir):
    path = config_dir / "thresholds.yaml"
    save_models(path, {"embedding_model": "mxbai-embed-large"})
    assert load_thresholds(path).embedding_model == "mxbai-embed-large"


def test_save_models_creates_the_block_when_absent(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("min_upvotes: 1\nembedding_model: nomic-embed-text\n", encoding="utf-8")
    save_models(path, {"extractor": "qwen3:8b"})
    assert load_thresholds(path).extractor_model == "qwen3:8b"


def test_save_models_rejects_junk(config_dir):
    path = config_dir / "thresholds.yaml"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="not a model slot"):
        save_models(path, {"min_upvotes": 3})
    with pytest.raises(ConfigError, match="cannot be blank"):
        save_models(path, {"critic": "  "})
    with pytest.raises(ConfigError, match="usable model name"):
        save_models(path, {"critic": "bad name: with colon"})
    assert path.read_text(encoding="utf-8") == before


def test_console_models_lists_roles_and_profiles(api):
    res = api.models()
    assert res["ok"] is True
    assert res["roles"] == ["extractor", "archivist", "synthesizer", "critic", "embedding_model"]
    assert set(res["models"]) == set(res["roles"])
    assert isinstance(res["installed"], list)
    assert res["ollama_up"] in (True, False)
    # A console pointed at a fixture dir offers only that dir, never the repo's.
    assert [p["name"] for p in res["profiles"]] == [Path(api.config_dir).name]


def test_console_models_update_writes_only_the_chosen_profile(api, config_dir):
    # Model choice is exactly what distinguishes config/ from config-live/, so
    # a model write must never be mirrored the way sources and thresholds are.
    live = Path(__file__).resolve().parents[2] / "config-live" / "thresholds.yaml"
    before = live.read_text(encoding="utf-8") if live.exists() else None

    res = api.update_models({"critic": "qwen3:8b"})
    assert res["ok"] is True and res["applied"] == {"critic": "qwen3:8b"}
    assert load_thresholds(config_dir / "thresholds.yaml").critic_model == "qwen3:8b"
    if before is not None:
        assert live.read_text(encoding="utf-8") == before


def test_console_models_rejects_an_unknown_profile(api):
    assert api.models("nope")["ok"] is False
    bad = api.update_models({"critic": "qwen3:8b"}, profile="nope")
    assert bad["ok"] is False and "profile" in bad["error"]


def test_missing_treats_bare_name_as_the_latest_tag():
    from jester.console.api import _model_installed

    assert _model_installed("nomic-embed-text", ["nomic-embed-text:latest"])
    assert _model_installed("qwen3:8b", ["qwen3:8b"])
    assert not _model_installed("qwen3", ["qwen3:8b"])
    assert not _model_installed("qwen3:8b", [])


# ---- legacy schema rescue --------------------------------------------------


def test_legacy_quota_table_with_rows_is_kept_aside_not_fatal(tmp_path):
    """A pre-§37.13 quota(day, units) table used to make every ledger read die
    on `no such column: used`, taking the whole console overview with it."""
    import sqlite3

    from jester.store import open_db, quota_add, quota_used

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE quota (day TEXT PRIMARY KEY, units INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO quota(day, units) VALUES('2026-08-01', 42);"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    conn.commit()
    conn.close()

    db = open_db(str(db_path))
    assert quota_used(db, "reddit", "2026-08-25") == 0   # reads, does not raise
    quota_add(db, "reddit", "2026-08-25", 3)
    assert quota_used(db, "reddit", "2026-08-25") == 3
    # rows are preserved for inspection, never re-attributed to a platform
    assert db.execute("SELECT units FROM quota_legacy").fetchone()[0] == 42


def test_legacy_empty_table_is_recreated_without_a_legacy_copy(tmp_path):
    import sqlite3

    from jester.store import open_db

    db_path = tmp_path / "legacy2.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE ideas (id INTEGER PRIMARY KEY, overall REAL);"
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, started TEXT, flags TEXT);"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    conn.commit()
    conn.close()

    db = open_db(str(db_path))
    assert db.execute("SELECT critic_model FROM ideas").fetchall() == []
    assert db.execute("SELECT run_id, started_at FROM runs").fetchall() == []
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ideas_legacy" not in tables   # nothing to preserve, nothing kept
    assert "runs_legacy" not in tables


def test_reconcile_leaves_a_current_database_untouched(tmp_path):
    from jester.store import open_db, reconcile_schema

    db_path = tmp_path / "fresh.db"
    db = open_db(str(db_path))
    assert reconcile_schema(db) == []


def test_open_db_is_idempotent_after_the_rescue(tmp_path):
    from jester.store import open_db, quota_add, quota_used

    db_path = tmp_path / "twice.db"
    db = open_db(str(db_path))
    quota_add(db, "reddit", "2026-08-25", 5)
    db.close()
    again = open_db(str(db_path))
    assert quota_used(again, "reddit", "2026-08-25") == 5   # not wiped on reopen


def test_enqueue_works_against_a_go_created_ingest_batch(tmp_path):
    """The Go worker owns ingest_batch and declares run_id / batch_index NOT
    NULL. Python used to omit both, so `run` died with `NOT NULL constraint
    failed: ingest_batch.run_id` on any database the worker had touched."""
    import sqlite3

    from jester.store import enqueue_batch, open_db, pending_batches

    db_path = tmp_path / "go-owned.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE ingest_batch ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL,"
        " platform TEXT NOT NULL,"
        " source TEXT NOT NULL,"
        " thread_id TEXT NOT NULL,"
        " batch_index INTEGER NOT NULL,"
        " comments TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " claimed_at TEXT,"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " error TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    conn.commit()
    conn.close()

    db = open_db(str(db_path))
    enqueue_batch(db, "reddit", "https://example.test", "t1",
                  [{"body": "x", "fingerprint": "f1", "upvotes": 1}], run_id="r-1")
    rows = pending_batches(db)
    assert len(rows) == 1
    # pending_batches() installs a Row factory, so compare as a tuple
    row = db.execute("SELECT run_id, batch_index FROM ingest_batch").fetchone()
    assert tuple(row) == ("r-1", 0)


def test_end_to_end_run_against_a_go_created_database(tmp_path):
    """The console's ▶ run pipeline over a worker-created database."""
    import sqlite3

    db_path = tmp_path / "e2e.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE ingest_batch ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,"
        " platform TEXT NOT NULL, source TEXT NOT NULL, thread_id TEXT NOT NULL,"
        " batch_index INTEGER NOT NULL, comments TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending', claimed_at TEXT,"
        " attempts INTEGER NOT NULL DEFAULT 0, error TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE TABLE quota (day TEXT PRIMARY KEY, units INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    conn.commit()
    conn.close()

    api = ConsoleAPI(db_path=str(db_path), config_dir=str(REPO_CONFIG))
    res = api.run_pipeline(run_id="legacy-e2e")
    assert res["ok"] is True, res
    assert api.runs()["runs"][0]["status"] == "completed"
    assert api.overview()["counts"]["nuggets"] > 0


def test_a_malformed_batch_does_not_abort_the_run(tmp_path):
    """The Go worker marshals an empty comment slice as JSON `null`. That one
    row used to kill the whole run with `'NoneType' object is not iterable`."""
    from jester.store import open_db

    db_path = tmp_path / "malformed.db"
    api = ConsoleAPI(db_path=str(db_path), config_dir=str(REPO_CONFIG))
    db = open_db(str(db_path))
    for payload in ("null", "not json at all", '{"not": "a list"}'):
        db.execute(
            "INSERT INTO ingest_batch(run_id, platform, source, thread_id, "
            "batch_index, comments, status) VALUES('go','reddit','s','t',0,?,'pending')",
            (payload,),
        )
    db.execute(
        "INSERT INTO ingest_batch(run_id, platform, source, thread_id, "
        "batch_index, comments, status) VALUES('go','reddit','s','good',0,?,'pending')",
        ('[{"body": "I keep losing my config every single weekend update and there '
         'is no way to roll back at all", "fingerprint": "ok1", "upvotes": 9}]',),
    )
    db.commit()

    res = api.run_pipeline(run_id="malformed-run")
    assert res["ok"] is True, res
    assert api.runs()["runs"][0]["status"] == "completed"
    # the healthy batch still made it through
    assert api.overview()["counts"]["nuggets"] >= 1
    # and no batch is left pending to jam the next run
    assert api.overview()["counts"]["pending_batches"] == 0


# ---- forum platforms (Hacker News, Discourse) ------------------------------


@pytest.mark.parametrize("raw,platform,kind,url,name", [
    ("ask", "hackernews", "feed", "https://news.ycombinator.com/ask", "hn-ask"),
    ("show", "hackernews", "feed", "https://news.ycombinator.com/show", "hn-show"),
    ("front", "hackernews", "feed", "https://news.ycombinator.com/news", "hn-front"),
    ("https://news.ycombinator.com/item?id=37392676", "auto", "story",
     "https://news.ycombinator.com/item?id=37392676", "hn-37392676"),
    ("https://news.ycombinator.com/ask", "auto", "feed",
     "https://news.ycombinator.com/ask", "hn-ask"),
    ("community.home-assistant.io", "discourse", "forum",
     "https://community.home-assistant.io", "community-home-assistant-io"),
    ("https://meta.discourse.org/latest", "discourse", "forum",
     "https://meta.discourse.org", "meta-discourse-org"),
    ("https://community.home-assistant.io/t/some-slug/114371", "discourse", "topic",
     "https://community.home-assistant.io/t/some-slug/114371", "community-114371"),
])
def test_forum_sources_parse(raw, platform, kind, url, name):
    s = parse_source(raw, platform)
    assert (s.kind, s.url, s.name) == (kind, url, name)
    assert s.supported is True   # both adapters exist in the Go worker


def test_hackernews_is_auto_detected_but_discourse_is_not():
    # HN has one known host, so inferring it is safe.
    assert parse_source("https://news.ycombinator.com/item?id=1").platform == "hackernews"
    # Discourse runs on arbitrary domains — guessing would claim every unknown
    # URL as a forum, so it must be chosen explicitly.
    with pytest.raises(SourceError, match="cannot tell which platform"):
        parse_source("community.home-assistant.io")


def test_forum_parse_errors_are_actionable():
    with pytest.raises(SourceError, match="Hacker News"):
        parse_source("wat", "hackernews")
    with pytest.raises(SourceError, match="Discourse"):
        parse_source("not a host", "discourse")


def test_new_platforms_are_in_the_console_vocabulary(api):
    payload = api.sources()
    assert {"hackernews", "discourse"} <= set(payload["platform_kinds"])
    assert ["hackernews", "feed"] in payload["supported_kinds"]
    assert ["discourse", "forum"] in payload["supported_kinds"]


def test_shipped_source_list_is_valid_and_ingestable():
    """Every enabled source we ship must have an adapter behind it."""
    srcs = load_sources(REPO_CONFIG / "sources.yaml")
    enabled = [s for s in srcs if s.enabled]
    assert len(enabled) >= 10, "the curated list should cover real ground"
    unsupported = [s.name for s in enabled if not s.supported]
    assert unsupported == [], f"enabled but no adapter: {unsupported}"
    platforms = {s.platform for s in enabled}
    assert {"reddit", "hackernews", "discourse"} <= platforms
    # Each entry must round-trip through the parser it claims to come from.
    for s in srcs:
        assert s.kind in PLATFORM_KINDS[s.platform], f"{s.name}: bad kind {s.kind}"


def test_adding_a_forum_through_the_console_writes_it_through(api, config_dir):
    # A host that will never be shipped, so this stays a test of the
    # new-source path. Two real forums have sat here and both were later added
    # to the curated list, which quietly turned this into a duplicate-URL test.
    # `.invalid` is reserved by RFC 2606 precisely so it can never be a real
    # host, which makes that impossible a third time.
    res = api.add_source("https://forum.jester-test.invalid", platform="discourse")
    assert res["ok"] is True
    assert res["source"]["kind"] == "forum"
    assert res["source"]["supported"] is True
    written = load_sources(config_dir / "sources.yaml")
    assert any(
        s.platform == "discourse" and s.name == "forum-jester-test-invalid"
        for s in written
    )


def test_adding_a_forum_that_is_already_shipped_is_refused(api, config_dir):
    """The branch the test above used to reach by accident once rclone joined
    the shipped list. Refusing beats silently adding a second entry on the same
    URL, which would scrape it twice every run."""
    before = len(load_sources(config_dir / "sources.yaml"))
    res = api.add_source("https://forum.rclone.org", platform="discourse")
    assert res["ok"] is False
    assert "already" in res["error"]
    after = load_sources(config_dir / "sources.yaml")
    assert len(after) == before, "a refused add must not write anything"

    hn = api.add_source("ask", platform="hackernews")
    # `ask` is already in the shipped list, so this must be refused as a dup
    # rather than quietly creating a second identical feed.
    assert hn["ok"] is False and "already" in hn["error"]


def test_every_shipped_threshold_key_is_known_to_python(tmp_path):
    """A key in the shared thresholds.yaml that Python's dataclass does not
    declare is silently dropped by load_thresholds — so the console cannot
    surface it and the Config page quietly stops being 'every knob'."""
    import yaml

    for name in ("config", "config-live"):
        path = REPO_CONFIG.parent / name / "thresholds.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = set(Thresholds.__dataclass_fields__) | {"models"}
        unknown = sorted(set(raw) - known)
        assert unknown == [], f"{name}/thresholds.yaml has keys Python ignores: {unknown}"


def test_go_only_knobs_are_still_editable_from_the_console():
    # min_comments_per_thread only affects the Go worker, but it lives in the
    # shared file, so the console must be able to read and write it.
    assert "min_comments_per_thread" in Thresholds._EDITABLE
    assert Thresholds(min_comments_per_thread=8).validate().min_comments_per_thread == 8
    with pytest.raises(ConfigError, match="min_comments_per_thread"):
        Thresholds(min_comments_per_thread=-1).validate()


# ── source names must be unique ──────────────────────────────────────────────
# Go keys source_state by name. Two sources sharing one share a last-fetched
# timestamp, a visit count and a cumulative yield, so rotation treats them as
# one stop and a productive source makes a dead one look alive. The shipped
# file grew two collisions (reddit r/devops vs devops.stackexchange.com, and
# lemmy.world/c/linux vs lemmy.ml/c/linux) purely from hand-editing.

def test_shipped_sources_have_unique_names():
    srcs = load_sources(REPO_CONFIG / "sources.yaml")
    assert srcs, "the shipped source list should not be empty"
    assert duplicate_names(srcs) == []


def test_duplicate_names_reports_each_clash_once():
    srcs = [
        Source(name="a", platform="reddit", url="https://reddit.com/r/a", kind="subreddit"),
        Source(name="a", platform="lemmy", url="https://lemmy.world/c/a", kind="community"),
        Source(name="a", platform="github", url="https://github.com/x/a", kind="repo"),
        Source(name="b", platform="reddit", url="https://reddit.com/r/b", kind="subreddit"),
    ]
    assert duplicate_names(srcs) == ["a"]


def test_blank_names_are_not_a_clash():
    # An unnamed source is a different defect, and TouchSource already ignores
    # a blank name rather than creating a row for it.
    srcs = [
        Source(name="", platform="reddit", url="https://reddit.com/r/a", kind="subreddit"),
        Source(name="  ", platform="reddit", url="https://reddit.com/r/b", kind="subreddit"),
    ]
    assert duplicate_names(srcs) == []


# ── per-source health (A13) ──────────────────────────────────────────────────

def test_never_visited_is_not_a_verdict():
    for state in (None, {}, {"visits": 0, "total_queued": 0, "measured_visits": 0}):
        status, _ = ConsoleAPI._source_health(state)
        assert status == "unknown", state


def test_a_quiet_round_does_not_condemn_a_producing_source():
    # last_queued is 0 — the only signal before total_queued existed — but this
    # source has produced plenty. Parking it would be the bug.
    status, _ = ConsoleAPI._source_health(
        {"visits": 9, "measured_visits": 9, "last_queued": 0, "total_queued": 240})
    assert status == "producing"


def test_barren_only_after_enough_visits():
    n = ConsoleAPI.PARK_AFTER_BARREN_VISITS
    below = {"visits": n - 1, "measured_visits": n - 1, "total_queued": 0}
    at = {"visits": n, "measured_visits": n, "total_queued": 0}
    assert ConsoleAPI._source_health(below)[0] == "quiet"
    assert ConsoleAPI._source_health(at)[0] == "barren"


def test_sources_payload_carries_health_and_clashes(api):
    res = api.sources()
    assert res["ok"]
    assert res["duplicate_names"] == []
    assert res["park_after_barren_visits"] == ConsoleAPI.PARK_AFTER_BARREN_VISITS
    # No worker has run against this database, so every source is unknown —
    # and every one of them still carries the field.
    assert all("health" in s and "health_reason" in s for s in res["sources"])
    assert {s["health"] for s in res["sources"]} == {"unknown"}


def test_health_survives_a_database_without_the_column(api):
    """source_state is Go-owned and may predate total_queued, or be absent
    entirely. Neither may take the Sources tab down."""
    api.db.execute(
        "CREATE TABLE source_state (name TEXT PRIMARY KEY, last_fetched_at TEXT, "
        "last_queued INTEGER, visits INTEGER)")
    api.db.execute(
        "INSERT INTO source_state VALUES ('hn-ask', datetime('now'), 3, 5)")
    api.db.commit()
    res = api.sources()
    assert res["ok"]
    hn = next(s for s in res["sources"] if s["name"] == "hn-ask")
    # The visit history is real and must survive. The yield behind those visits
    # was never recorded and cannot be recovered, so the verdict withholds
    # judgement instead of reading a column default as evidence of death.
    assert hn["state"]["visits"] == 5
    assert hn["state"]["measured_visits"] == 0
    assert hn["health"] == "unmeasured"
    assert "before yields were recorded" in hn["health_reason"]
    # And it must not be conflated with a source nobody has ever walked — the
    # page shows those two side by side and they say different things.
    untouched = next(s for s in res["sources"] if s["name"] != "hn-ask")
    assert untouched["health"] == "unknown"


# ── every platform must be registered everywhere ─────────────────────────────
# Stack Exchange, GitHub and Lemmy each shipped without an entry in the
# console's platform-label map, so three whole platforms printed as raw slugs
# for days. It is the same omission every time and nothing catches it, so a
# test does.

def test_every_platform_has_a_console_label():
    import re
    from pathlib import Path

    from jester.sources import PLATFORM_KINDS

    js = (Path(__file__).resolve().parents[1] / "jester" / "console" / "static"
          / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const PLATFORM_LABEL = \{(.*?)\};", js, re.S)
    assert block, "PLATFORM_LABEL should be a flat object literal in app.js"
    labelled = set(re.findall(r"(\w+)\s*:", block.group(1)))
    assert set(PLATFORM_KINDS) - labelled == set()


def test_every_platform_has_a_console_sort_position():
    import re
    from pathlib import Path

    from jester.sources import PLATFORM_KINDS

    js = (Path(__file__).resolve().parents[1] / "jester" / "console" / "static"
          / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const order = \{(.*?)\};", js, re.S)
    assert block, "the Sources tab should sort platforms by an explicit order map"
    ordered = set(re.findall(r"(\w+)\s*:", block.group(1)))
    # An unlisted platform falls into the `?? 9` bucket and interleaves
    # arbitrarily with every other unlisted one.
    assert set(PLATFORM_KINDS) - ordered == set()
