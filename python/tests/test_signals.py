"""the collector problem-signal scaffold — the checks the milestone is graded on.

The first milestone asks for a field map, five labeled synthetic records and
one command that reproduces the export. These tests pin the properties that
make those artifacts trustworthy rather than merely present: a fixture row can
never be counted as live, a repeat import never duplicates, an edit is noticed,
a removal is recorded rather than deleted, and the CSV reconciles with the
table. The twenty-case fixture pipeline extends the same checks.
"""
import csv
import json

import pytest

from jester import signals as sig


@pytest.fixture()
def db(tmp_path):
    return sig.open_signals(str(tmp_path / "signals.db"))


def _one(**kw):
    base = dict(source_id="t3_a", source_url="u", community="r/x", query="q",
                text="a problem worth solving", match_reason="r", match_confidence=0.8)
    base.update(kw)
    return sig.Signal(**base)


# ---- the field map is the single source of the three schemas ---------------

def test_one_field_map_drives_sqlite_clickhouse_and_csv(db, tmp_path):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({sig.TABLE})")]
    assert cols == sig.FIELD_NAMES, "the SQLite table drifted from the field map"

    ch = sig.clickhouse_ddl()
    for name in sig.FIELD_NAMES:
        assert f"  {name} " in ch, f"{name} missing from the ClickHouse DDL"

    sig.upsert(db, [_one()])
    sig.export_csv(db, tmp_path)
    with (tmp_path / "signals.csv").open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == sig.FIELD_NAMES, "the CSV header drifted from the field map"


def test_field_map_is_printable_for_the_milestone(tmp_path):
    md = sig.field_map_markdown()
    assert md.startswith("| field |") and md.count("\n") >= len(sig.FIELDS)
    # The pipe inside a meaning is escaped so the table still renders.
    assert "`mode`" in md and r"live \| fixture" in md


# ---- mode: a fixture row can never be counted as live ----------------------

def test_every_synthetic_record_is_labeled_fixture():
    rows = sig.synthetic_signals()
    assert len(rows) == 5, "the milestone asks for five, not a round number"
    assert {r.mode for r in rows} == {"fixture"}
    assert all(r.row()["mode"] == "fixture" for r in rows)


def test_counts_are_grouped_by_mode_not_asserted(db):
    sig.upsert(db, sig.synthetic_signals())
    sig.upsert(db, [_one(source_id="t3_live", mode="live", relevant=1)])
    c = sig.counts(db)
    assert c["fixture"]["unique_collected"] == 5
    assert c["live"]["unique_collected"] == 1
    assert c["fixture"]["relevant"] == 3 and c["fixture"]["errors"] == 1


def test_unknown_stays_unknown(db):
    """A post is a signal; it is never evidence of a company or a purchase."""
    sig.upsert(db, sig.synthetic_signals())
    rows = db.execute(f"SELECT company, buyer_intent FROM {sig.TABLE}").fetchall()
    assert {r["company"] for r in rows} == {"unknown"}
    assert {r["buyer_intent"] for r in rows} == {"unknown"}


# ---- dedup, edits, removals ------------------------------------------------

def test_repeat_import_creates_no_duplicates(db):
    first = sig.upsert(db, sig.synthetic_signals())
    second = sig.upsert(db, sig.synthetic_signals())
    assert first == {"new": 5, "seen_again": 0, "edited": 0}
    assert second == {"new": 0, "seen_again": 5, "edited": 0}
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 5


def test_first_seen_is_never_overwritten_and_last_seen_moves(db):
    sig.upsert(db, [_one()], seen_at="2026-09-20T00:00:00+00:00")
    sig.upsert(db, [_one()], seen_at="2026-09-22T00:00:00+00:00")
    row = db.execute(f"SELECT first_seen_utc, last_seen_utc FROM {sig.TABLE}").fetchone()
    assert row["first_seen_utc"] == "2026-09-20T00:00:00+00:00"
    assert row["last_seen_utc"] == "2026-09-22T00:00:00+00:00"


def test_an_edit_is_noticed_counted_and_dated(db):
    sig.upsert(db, [_one(text="before")], seen_at="2026-09-20T00:00:00+00:00")
    stats = sig.upsert(db, [_one(text="after")], seen_at="2026-09-21T00:00:00+00:00")
    row = db.execute(f"SELECT revisions, edited_utc, excerpt FROM {sig.TABLE}").fetchone()
    assert stats["edited"] == 1
    assert row["revisions"] == 1 and row["edited_utc"] == "2026-09-21T00:00:00+00:00"
    assert row["excerpt"] == "after"
    # A second unchanged sighting must not inflate the revision count.
    sig.upsert(db, [_one(text="after")], seen_at="2026-09-22T00:00:00+00:00")
    assert db.execute(f"SELECT revisions FROM {sig.TABLE}").fetchone()[0] == 1


def test_a_removal_is_recorded_not_deleted(db):
    sig.upsert(db, [_one()])
    sig.mark_removed(db, ["reddit:t3_a"], at="2026-09-22T00:00:00+00:00")
    row = db.execute(f"SELECT removed_utc FROM {sig.TABLE}").fetchone()
    assert row["removed_utc"] == "2026-09-22T00:00:00+00:00"
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 1, \
        "the row survives its removal — the signal was real when it was collected"
    # Seeing it again clears the removal rather than creating a second row.
    sig.upsert(db, [_one()])
    assert db.execute(f"SELECT removed_utc FROM {sig.TABLE}").fetchone()[0] == ""


# ---- export reconciles -----------------------------------------------------

def test_export_reconciles_with_the_table(db, tmp_path):
    sig.upsert(db, sig.synthetic_signals())
    manifest = sig.export_csv(db, tmp_path, mode="fixture")
    assert manifest["rows_in_db"] == manifest["rows_in_csv"] == 5
    assert manifest["reconciles"] is True
    with (tmp_path / "signals-fixture.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 5 and {r["mode"] for r in rows} == {"fixture"}
    saved = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert saved["counts_by_mode"]["fixture"]["relevant"] == 3
    assert (tmp_path / "field-map.md").exists()
    assert (tmp_path / "schema.clickhouse.sql").exists()


def test_the_one_command_is_repeatable(tmp_path):
    db_path = str(tmp_path / "j.db")
    out = tmp_path / "exports"
    first = sig.run_fixture_export(db_path, out)
    second = sig.run_fixture_export(db_path, out)
    assert first["upsert"]["new"] == 5 and second["upsert"]["new"] == 0
    assert second["rows_in_csv"] == 5, "a second run must not grow the export"
    assert second["reconciles"] is True


def test_excerpt_is_truncated_but_the_hash_is_of_the_whole_text(db):
    long = "x" * (sig.EXCERPT_CHARS + 500)
    sig.upsert(db, [_one(text=long)])
    row = db.execute(f"SELECT excerpt, text_hash FROM {sig.TABLE}").fetchone()
    assert len(row["excerpt"]) == sig.EXCERPT_CHARS
    assert row["text_hash"] == sig.text_hash(long), \
        "an edit past the excerpt boundary still has to be detectable"
