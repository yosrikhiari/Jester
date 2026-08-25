"""TDD tests for jester config loading and schema_version guard."""
import sqlite3
import tempfile
from pathlib import Path

from jester import config
from jester.store import open_db, SchemaVersionError


def test_thresholds_defaults_valid():
    t = config.Thresholds()
    t.validate()  # must not raise


def test_thresholds_out_of_range_fails():
    t = config.Thresholds(dedup_threshold=1.5)
    try:
        t.validate()
    except config.ConfigError:
        return
    raise AssertionError("expected ConfigError for dedup_threshold=1.5")


def test_thresholds_load_and_validate(tmp_path):
    p = tmp_path / "thresholds.yaml"
    p.write_text(
        "dedup_threshold: 0.87\n"
        "embedding_model: nomic-embed-text\n"
        "prefilter_min_chars: 40\n"
    )
    t = config.load_thresholds(p)
    assert t.dedup_threshold == 0.87
    assert t.embedding_model == "nomic-embed-text"
    t.validate()


def test_schema_version_mismatch_refuses(tmp_path):
    db = tmp_path / "jester.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
    conn.commit()
    conn.close()
    try:
        open_db(str(db))
    except SchemaVersionError:
        return
    raise AssertionError("expected SchemaVersionError for db schema_version=2")
