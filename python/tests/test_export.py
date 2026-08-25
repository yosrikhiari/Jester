"""CSV export: content split by type and origin, and §14 retention over it."""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jester.console.api import ConsoleAPI
from jester.export import (
    COMMENT_COLUMNS,
    IDEA_COLUMNS,
    MANIFEST,
    NUGGET_COLUMNS,
    default_export_dir,
    export_csv,
    prune_exports,
)
from jester.models import Idea, IdeaScores, Nugget
from jester.store import enqueue_batch, insert_idea, insert_nugget, open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def read_csv(path: Path):
    # utf-8-sig on the way out, so read it back the same way.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture()
def db(tmp_path):
    d = open_db(str(tmp_path / "export.db"))
    # scraped comments across two origins
    enqueue_batch(d, "reddit", "https://reddit.test/t/1", "t1", [
        {"body": "my config is wiped every upgrade", "fingerprint": "r1", "upvotes": 12},
        {"body": "same here, no rollback path", "fingerprint": "r2", "upvotes": 3},
    ], run_id="r1")
    enqueue_batch(d, "hackernews", "https://news.ycombinator.com/item?id=9", "hn-9", [
        {"body": "our deploy tooling is held together with bash", "fingerprint": "h1", "upvotes": 0},
    ], run_id="r1")
    # extracted nuggets across two origins and two categories
    for key, platform, category, insight in [
        ("reddit:t1:r1", "reddit", "pain_point", "config wiped on upgrade"),
        ("reddit:t1:r2", "reddit", "workaround", "restore the whole VM"),
        ("hackernews:hn-9:h1", "hackernews", "pain_point", "deploy tooling is bash"),
    ]:
        insert_nugget(d, Nugget(
            unique_key=key, platform=platform, category=category,
            thread_id=key.split(":")[1], source_url=f"https://{platform}.test",
            raw_text=f"raw for {key}", extracted_insight=insight, run_id="r1",
        ))
    return d


def seed_idea(db, checked=True):
    idea = Idea(
        title="Config-safe upgrades", problem_statement="p", proposed_solution="s",
        supporting_nuggets=["reddit:t1:r1", "missing:key"],
        source_threads=1, source_platforms=["reddit", "hackernews"],
        scores=IdeaScores(demand_signal=8.0, feasibility=7.0,
                          competition=4.0 if checked else None, overall=7.7),
        competition_checked=checked,
        competitor_notes="Timeshift" if checked else None,
    )
    idea.id = insert_idea(db, idea)
    return idea


# ---- layout ----------------------------------------------------------------


def test_export_splits_by_origin_and_content_type(db, tmp_path):
    out = tmp_path / "exports"
    manifest = export_csv(db, out)

    # by origin
    assert (out / "comments" / "reddit" / "reddit-001.csv").exists()
    assert (out / "comments" / "hackernews" / "hackernews-001.csv").exists()
    assert (out / "nuggets" / "reddit" / "reddit-001.csv").exists()
    assert (out / "nuggets" / "hackernews" / "hackernews-001.csv").exists()
    # by content type
    assert (out / "nuggets" / "by-category" / "pain_point" / "pain_point-001.csv").exists()
    assert (out / "nuggets" / "by-category" / "workaround" / "workaround-001.csv").exists()

    assert manifest["counts"] == {"comments": 3, "nuggets": 3, "ideas": 0, "citations": 0}


def test_rows_land_in_the_right_origin_file(db, tmp_path):
    out = tmp_path / "exports"
    export_csv(db, out)

    reddit = read_csv(out / "comments" / "reddit" / "reddit-001.csv")
    hn = read_csv(out / "comments" / "hackernews" / "hackernews-001.csv")
    assert len(reddit) == 2 and len(hn) == 1
    assert all(r["platform"] == "reddit" for r in reddit)
    assert hn[0]["body"] == "our deploy tooling is held together with bash"
    assert hn[0]["upvotes"] == "0"      # R29: absent points export as 0, not blank


def test_category_files_cut_across_origins(db, tmp_path):
    out = tmp_path / "exports"
    export_csv(db, out)
    pain = read_csv(out / "nuggets" / "by-category" / "pain_point" / "pain_point-001.csv")
    assert {r["platform"] for r in pain} == {"reddit", "hackernews"}
    assert len(pain) == 2


def test_headers_are_stable_and_complete(db, tmp_path):
    out = tmp_path / "exports"
    export_csv(db, out)
    with (out / "nuggets" / "reddit" / "reddit-001.csv").open(encoding="utf-8-sig") as fh:
        assert next(csv.reader(fh)) == NUGGET_COLUMNS


# ---- ideas + citations -----------------------------------------------------


def test_ideas_and_citations_export_relationally(db, tmp_path):
    idea = seed_idea(db)
    out = tmp_path / "exports"
    export_csv(db, out)

    ideas = read_csv(out / "ideas" / "ideas-001.csv")
    assert len(ideas) == 1
    assert ideas[0]["title"] == "Config-safe upgrades"
    assert ideas[0]["supporting_nugget_count"] == "2"
    assert ideas[0]["source_platforms"] == "reddit, hackernews"
    assert ideas[0]["competition_checked"] == "yes"

    cites = read_csv(out / "ideas" / "citations-001.csv")
    assert len(cites) == 2
    by_key = {c["nugget_key"]: c for c in cites}
    assert by_key["reddit:t1:r1"]["resolved"] == "yes"
    assert by_key["reddit:t1:r1"]["nugget_category"] == "pain_point"
    # A dangling citation is an archive defect doctor alarms on — the export
    # shows it rather than quietly dropping the row.
    assert by_key["missing:key"]["resolved"] == "no"
    assert all(int(c["idea_id"]) == idea.id for c in cites)


def test_unchecked_competition_exports_blank_not_the_imputed_five(db, tmp_path):
    seed_idea(db, checked=False)
    out = tmp_path / "exports"
    export_csv(db, out)
    row = read_csv(out / "ideas" / "ideas-001.csv")[0]
    # R29: §7.2 imputes 5 for the maths, but the export must not launder that
    # imputation into a spreadsheet cell as if it were a measurement.
    assert row["competition"] == ""
    assert row["competition_checked"] == "no"


# ---- robustness ------------------------------------------------------------


def test_malformed_batch_is_skipped_not_fatal(db, tmp_path):
    db.execute(
        "INSERT INTO ingest_batch(run_id, platform, source, thread_id, batch_index,"
        " comments, status) VALUES('r1','reddit','s','t9',0,'null','pending')")
    db.commit()
    manifest = export_csv(db, tmp_path / "exports")
    assert manifest["counts"]["comments"] == 3   # the good rows still exported


def test_empty_archive_exports_headers_only(tmp_path):
    d = open_db(str(tmp_path / "empty.db"))
    out = tmp_path / "exports"
    manifest = export_csv(d, out)
    assert manifest["counts"] == {"comments": 0, "nuggets": 0, "ideas": 0, "citations": 0}
    assert read_csv(out / "ideas" / "ideas-001.csv") == []
    with (out / "ideas" / "ideas-001.csv").open(encoding="utf-8-sig") as fh:
        assert next(csv.reader(fh)) == IDEA_COLUMNS


def test_unicode_survives_the_round_trip(tmp_path):
    d = open_db(str(tmp_path / "uni.db"))
    insert_nugget(d, Nugget(
        unique_key="reddit:t1:u1", platform="reddit", category="pain_point",
        thread_id="t1", source_url="https://x.test",
        raw_text="naïve — “smart quotes” and 日本語", extracted_insight="unicode holds",
    ))
    out = tmp_path / "exports"
    export_csv(d, out)
    row = read_csv(out / "nuggets" / "reddit" / "reddit-001.csv")[0]
    assert row["raw_text"] == "naïve — “smart quotes” and 日本語"


def test_odd_platform_names_cannot_escape_the_export_dir(tmp_path):
    d = open_db(str(tmp_path / "odd.db"))
    insert_nugget(d, Nugget(
        unique_key="x:1:1", platform="../../etc", category="",
        thread_id="1", source_url="", raw_text="x", extracted_insight="x",
    ))
    out = tmp_path / "exports"
    export_csv(d, out)
    written = [p for p in (out / "nuggets").rglob("*.csv")]
    assert written, "a nugget with an odd platform must still export"
    for p in written:
        assert out.resolve() in p.resolve().parents


# ---- §14 retention over exports -------------------------------------------


def test_retention_prunes_a_stale_export(tmp_path):
    d = open_db(str(tmp_path / "r.db"))
    out = tmp_path / "exports"
    export_csv(d, out, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert out.exists()
    # A CSV holding raw scraped text must not outlive the archive's own TTL.
    assert prune_exports(out, ttl_days=30, now=datetime(2026, 3, 1, tzinfo=timezone.utc)) == 1
    assert not out.exists()


def test_retention_keeps_a_fresh_export(tmp_path):
    d = open_db(str(tmp_path / "r.db"))
    out = tmp_path / "exports"
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    export_csv(d, out, now=now)
    assert prune_exports(out, ttl_days=30, now=now + timedelta(days=5)) == 0
    assert out.exists()


def test_prune_is_safe_on_a_missing_or_unmanaged_dir(tmp_path):
    assert prune_exports(tmp_path / "nope") == 0
    stray = tmp_path / "not-an-export"
    stray.mkdir()
    (stray / "keep.txt").write_text("mine", encoding="utf-8")
    # No manifest means jester did not write it; never delete someone else's dir.
    assert prune_exports(stray) == 0
    assert (stray / "keep.txt").exists()


def test_manifest_records_what_was_written_and_the_retention_caveat(db, tmp_path):
    out = tmp_path / "exports"
    export_csv(db, out)
    manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    # Manifest keys are always forward-slashed, so a manifest written on
    # Windows is readable by anything that consumes it elsewhere.
    assert manifest["files"]["comments/reddit/reddit-001.csv"] == 2
    assert all("\\" not in name for name in manifest["files"])
    assert "retention" in manifest["retention_note"].lower()
    assert manifest["exported_at"]


# ---- console + defaults ----------------------------------------------------


def test_console_export_action(tmp_path):
    api = ConsoleAPI(db_path=str(tmp_path / "c.db"), config_dir=str(REPO_CONFIG))
    api.seed_mock()
    res = api.export(str(tmp_path / "out"))
    assert res["ok"] is True
    assert res["counts"]["comments"] >= 1
    assert Path(res["out_dir"], "comments", "reddit", "reddit-001.csv").exists()


def test_default_export_dir_sits_beside_the_database(tmp_path):
    got = default_export_dir(str(tmp_path / "sub" / "jester.db"))
    assert Path(got) == (tmp_path / "sub" / "exports")


# ---- reconcile must not rename over an ALTER-able column -------------------


def test_reconcile_does_not_rename_a_table_an_alter_can_fix(tmp_path):
    """Several columns live in BASE_SCHEMA *and* the ALTER list. An older
    database missing one must be patched in place, not renamed aside — that
    would move real run history out of the active table."""
    import sqlite3

    from jester.store import open_db, reconcile_schema

    db_path = tmp_path / "older.db"
    conn = sqlite3.connect(db_path)
    # A `runs` table from before top_near_misses / platform_counts existed,
    # holding a row so the "rename aside" branch would fire if it were wrong.
    conn.executescript(
        "CREATE TABLE runs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'running',"
        " started_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT,"
        " models_used TEXT, n_comments INTEGER, n_batches INTEGER,"
        " n_nuggets_kept INTEGER, n_discarded_trivial INTEGER, n_ideas INTEGER,"
        " floor_flags TEXT, phase_checkpoints TEXT, error TEXT,"
        " created_at TEXT);"
        "INSERT INTO runs(run_id, status) VALUES('historic','completed');"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    conn.commit()
    conn.close()

    db = open_db(str(db_path))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "runs_legacy" not in tables, "history was moved aside instead of patched"
    assert db.execute("SELECT run_id FROM runs").fetchone()[0] == "historic"
    # and the new column is usable
    assert db.execute("SELECT platform_counts FROM runs").fetchone()[0] is None
    assert reconcile_schema(db) == []


# ---- M3.2: per-platform counts in the run summary --------------------------


def test_run_reports_per_platform_counts(tmp_path):
    """Phase 3 exit gate: 'a run reports per-platform counts'. Without it one
    dead adapter hides behind a healthy total."""
    import json as _json

    from jester.cli import _platform_counts
    from jester.store import enqueue_batch, get_runs, open_db, start_run, update_run_summary

    meta = [
        ("reddit", "s", "t1", "f1"), ("reddit", "s", "t1", "f2"),
        ("hackernews", "s", "hn-1", "f3"), ("discourse", "s", "dc-1", "f4"),
        ("", "s", "t9", "f5"),   # a platform-less row is counted, not dropped
    ]
    assert _platform_counts(meta) == {
        "discourse": 1, "hackernews": 1, "reddit": 2, "unknown": 1}

    db = open_db(str(tmp_path / "pc.db"))
    start_run(db, "r1")
    update_run_summary(db, "r1", platform_counts=_platform_counts(meta))
    stored = _json.loads(get_runs(db)[0]["platform_counts"])
    assert stored["reddit"] == 2 and stored["hackernews"] == 1


def test_console_runs_payload_exposes_platform_counts(tmp_path):
    from jester.store import open_db, start_run, update_run_summary

    api = ConsoleAPI(db_path=str(tmp_path / "pc2.db"), config_dir=str(REPO_CONFIG))
    start_run(api.db, "r1")
    update_run_summary(api.db, "r1", platform_counts={"reddit": 3, "hackernews": 1})
    run = api.runs()["runs"][0]
    assert run["platform_counts_obj"] == {"reddit": 3, "hackernews": 1}
    assert "platform_counts" not in run   # popped into the parsed field


# ---- automatic export at the end of a run ---------------------------------


def test_bool_coercion_does_not_treat_false_as_true():
    """`bool("false")` is True in Python — the obvious caster would turn every
    string a user types into an enabled flag."""
    from jester.config import ConfigError, Thresholds, coerce_thresholds_patch

    for falsey in ("false", "False", "0", "no", "off", False):
        assert coerce_thresholds_patch({"export_after_run": falsey})["export_after_run"] is False
    for truthy in ("true", "TRUE", "1", "yes", "on", True):
        assert coerce_thresholds_patch({"export_after_run": truthy})["export_after_run"] is True
    with pytest.raises(ConfigError, match="boolean"):
        coerce_thresholds_patch({"export_after_run": "maybe"})
    assert Thresholds().export_after_run is False   # dataclass default: no drift


def test_run_exports_automatically_when_enabled(tmp_path, capsys):
    """A nightly run nobody watches should leave the CSVs behind by itself."""
    import shutil

    from jester.cli import cmd_run

    cfg_dir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cfg_dir)
    assert "export_after_run: true" in (cfg_dir / "thresholds.yaml").read_text(encoding="utf-8")

    exports = tmp_path / "exports"
    args = type("A", (), {})()
    args.db, args.config, args.run, args.exports = str(tmp_path / "auto.db"), str(cfg_dir), "auto", str(exports)
    cmd_run(args)

    out = capsys.readouterr().out
    assert "exported" in out
    assert (exports / "nuggets").exists()
    assert (exports / "ideas" / "ideas-001.csv").exists()
    assert (exports / MANIFEST).exists()


def test_run_does_not_export_when_disabled(tmp_path):
    import shutil

    from jester.cli import cmd_run

    cfg_dir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cfg_dir)
    t = cfg_dir / "thresholds.yaml"
    t.write_text(t.read_text(encoding="utf-8").replace(
        "export_after_run: true", "export_after_run: false"), encoding="utf-8")

    exports = tmp_path / "exports"
    args = type("A", (), {})()
    args.db, args.config, args.run, args.exports = str(tmp_path / "off.db"), str(cfg_dir), "off", str(exports)
    cmd_run(args)
    assert not exports.exists()


def test_a_failing_export_does_not_fail_the_run(tmp_path, capsys, monkeypatch):
    """Reporting must never turn a good pipeline run into a failed one."""
    import shutil

    from jester import cli as cli_mod

    cfg_dir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cfg_dir)
    monkeypatch.setattr(cli_mod, "export_csv",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    args = type("A", (), {})()
    args.db, args.config, args.run = str(tmp_path / "fail.db"), str(cfg_dir), "boom"
    args.exports = str(tmp_path / "exports")
    cli_mod.cmd_run(args)          # must not raise, must not sys.exit
    assert "export skipped: disk full" in capsys.readouterr().out


# ---- folder per source, sharded ---------------------------------------------


def test_each_origin_gets_a_folder_of_numbered_shards(tmp_path):
    """`reddit/` holds reddit-001.csv, reddit-002.csv, … so no single sheet
    grows past what a spreadsheet will open."""
    from jester.store import enqueue_batch, open_db

    d = open_db(str(tmp_path / "shard.db"))
    enqueue_batch(d, "reddit", "https://r.test", "t1", [
        {"body": f"a distinct reddit complaint number {i}", "fingerprint": f"r{i}", "upvotes": i}
        for i in range(25)
    ], run_id="r1")
    enqueue_batch(d, "hackernews", "https://hn.test", "hn1", [
        {"body": f"a distinct hn complaint number {i}", "fingerprint": f"h{i}", "upvotes": 0}
        for i in range(7)
    ], run_id="r1")

    out = tmp_path / "exports"
    manifest = export_csv(d, out, rows_per_file=10)

    reddit_dir = out / "comments" / "reddit"
    shards = sorted(p.name for p in reddit_dir.glob("*.csv"))
    assert shards == ["reddit-001.csv", "reddit-002.csv", "reddit-003.csv"]
    assert [len(read_csv(reddit_dir / s)) for s in shards] == [10, 10, 5]

    # A smaller origin still gets its own folder, one shard.
    hn = sorted(p.name for p in (out / "comments" / "hackernews").glob("*.csv"))
    assert hn == ["hackernews-001.csv"]
    assert len(read_csv(out / "comments" / "hackernews" / "hackernews-001.csv")) == 7

    assert manifest["rows_per_file"] == 10
    assert manifest["counts"]["comments"] == 32


def test_every_shard_carries_the_full_header(tmp_path):
    """A later shard without headers is unreadable on its own."""
    from jester.store import enqueue_batch, open_db

    d = open_db(str(tmp_path / "hdr.db"))
    enqueue_batch(d, "reddit", "s", "t1", [
        {"body": f"unique body {i}", "fingerprint": f"f{i}", "upvotes": 1} for i in range(5)
    ], run_id="r1")
    out = tmp_path / "exports"
    export_csv(d, out, rows_per_file=2)
    for shard in sorted((out / "comments" / "reddit").glob("*.csv")):
        with shard.open(encoding="utf-8-sig") as fh:
            assert next(csv.reader(fh)) == COMMENT_COLUMNS, f"{shard.name} lost its header"


def test_an_empty_origin_still_writes_one_headers_only_shard(tmp_path):
    d = open_db(str(tmp_path / "none.db"))
    out = tmp_path / "exports"
    export_csv(d, out)
    # Ideas always exist as a set even with nothing in them — a missing file
    # would read as "this was never produced".
    assert (out / "ideas" / "ideas-001.csv").exists()
    assert read_csv(out / "ideas" / "ideas-001.csv") == []


# ---- no repeated content ----------------------------------------------------


def test_identical_comment_text_is_written_once(tmp_path):
    """The same words twice — a crosspost, a quoted reply — is repeated
    content wherever it lands."""
    from jester.store import enqueue_batch, open_db

    d = open_db(str(tmp_path / "dup.db"))
    enqueue_batch(d, "reddit", "https://r.test/1", "t1", [
        {"body": "My config is wiped by every weekend update", "fingerprint": "a1", "upvotes": 5},
        {"body": "My config is wiped by every weekend update", "fingerprint": "a2", "upvotes": 9},
        {"body": "   MY CONFIG IS WIPED   by every weekend   update  ", "fingerprint": "a3", "upvotes": 1},
        {"body": "something genuinely different", "fingerprint": "a4", "upvotes": 2},
    ], run_id="r1")

    out = tmp_path / "exports"
    manifest = export_csv(d, out)

    rows = read_csv(out / "comments" / "reddit" / "reddit-001.csv")
    bodies = [r["body"] for r in rows]
    assert len(rows) == 2, f"duplicates survived: {bodies}"
    # Case and whitespace are formatting, not content.
    assert manifest["duplicates_dropped"] == 2
    assert manifest["counts_before_dedup"]["comments"] == 4


def test_duplicates_are_audited_not_silently_dropped(tmp_path):
    from jester.store import enqueue_batch, open_db

    d = open_db(str(tmp_path / "audit.db"))
    enqueue_batch(d, "reddit", "s", "t1", [
        {"body": "same text here", "fingerprint": "keepme", "upvotes": 1},
    ], run_id="r1")
    enqueue_batch(d, "hackernews", "s", "hn1", [
        {"body": "same text here", "fingerprint": "dropme", "upvotes": 1},
    ], run_id="r1")

    out = tmp_path / "exports"
    export_csv(d, out)
    audit = read_csv(out / "duplicates.csv")
    assert len(audit) == 1
    row = audit[0]
    # The gap between archive and export must be explainable.
    assert row["kind"] == "comment"
    assert row["kept_id"] == "keepme" and row["kept_origin"] == "reddit"
    assert row["dropped_id"] == "dropme" and row["dropped_origin"] == "hackernews"
    assert "same text here" in row["content_preview"]


def test_duplicate_nuggets_collapse_on_their_insight(tmp_path):
    d = open_db(str(tmp_path / "dupn.db"))
    for key, insight in [
        ("reddit:t1:a", "Backups break on every upgrade"),
        ("reddit:t1:b", "backups break on every upgrade"),   # case only
        ("hackernews:h1:c", "Backups break on every upgrade"),
        ("reddit:t1:d", "A completely separate problem"),
    ]:
        insert_nugget(d, Nugget(
            unique_key=key, platform=key.split(":")[0], category="pain_point",
            thread_id="t1", source_url="https://x.test",
            raw_text=insight, extracted_insight=insight,
        ))
    out = tmp_path / "exports"
    manifest = export_csv(d, out)
    assert manifest["counts"]["nuggets"] == 2
    assert manifest["counts_before_dedup"]["nuggets"] == 4


def test_blank_content_rows_are_not_all_collapsed_into_one(tmp_path):
    """Empty text is not evidence two rows are the same row."""
    from jester.export import dedup

    rows = [{"body": ""}, {"body": ""}, {"body": "real"}]
    kept, dropped = dedup(rows, lambda r: _norm_body(r), "comment")
    assert len(kept) == 3 and dropped == []


def _norm_body(row):
    from jester.export import _norm

    return _norm(row.get("body"))


def test_no_duplicates_file_when_nothing_was_collapsed(tmp_path):
    d = open_db(str(tmp_path / "clean.db"))
    insert_nugget(d, Nugget(
        unique_key="reddit:t1:x", platform="reddit", category="pain_point",
        thread_id="t1", source_url="", raw_text="unique", extracted_insight="unique",
    ))
    out = tmp_path / "exports"
    manifest = export_csv(d, out)
    assert manifest["duplicates_dropped"] == 0
    assert not (out / "duplicates.csv").exists()
