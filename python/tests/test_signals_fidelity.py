"""A signal that changed, and a signal that is gone (V3-14).

`upsert` used to refresh a subset of fields and count a revision only when the
text hash moved, so a corrected title, company or permalink was overwritten
without a trace, and a Hacker News posting that was deleted stayed in the
archive forever. This file pins the two behaviours that fix it:

* a re-read updates every mutable field, and the row it replaced is kept in
  `problem_signal_revision` first, so a correction is recoverable;
* a COMPLETE read decides what is gone, an incomplete one decides nothing, and
  a windowed read (Hacker News searches `since`) only judges the records that
  window could have returned.

The names here are the contract: the revision table is keyed on `record_id`
so the retention sweep finds it without a second hard-coded list, and the
reads set is stored per run so "what did that run actually see" is a SELECT.
"""
import json
import urllib.error

import pytest

from jester import signals as sig
from jester.signals import run as sigrun
from jester.signals.filters import load_rules
from jester.signals.sources.hackernews import HackerNews


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """Canned Algolia pages, served in order."""

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        page = self.pages.pop(0) if self.pages else {"hits": [], "nbPages": 0}
        if isinstance(page, Exception):
            raise page
        return _Resp(page)


class BrokenSecondPage(FakeHTTP):
    """Page one arrives; the read dies before the last page does."""

    def __call__(self, req, timeout=None):
        if self.calls == 0:
            return super().__call__(req, timeout)
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", {}, None)


def _hit(oid, **kw):
    base = {"objectID": oid, "author": "someone",
            "created_at": "2026-11-01T10:00:00Z",
            "title": "We re-key every order by hand", "_tags": ["story", "ask_hn"],
            "story_text": "I run a small distributor and we do it manually every week."}
    base.update(kw)
    return base


def _hn(http):
    return HackerNews(opener=http, sleep=lambda s: None)


@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def _removed(db):
    return {r[0] for r in db.execute(
        f"SELECT record_id FROM {sig.TABLE} WHERE COALESCE(removed_utc, '') != ''")}


def _signal(**kw):
    base = dict(source_id="t3_a", platform="reddit", title="same title",
                text="same body", query="q")
    base.update(kw)
    return sig.Signal(**base)


# -- 7a: every mutable field is updated, and the old row is kept -------------

MUTABLE = [
    ("title", "Old title", "Corrected title"),
    ("company", "unknown", "Acme Ltd"),
    ("buyer_intent", "unknown", "contract delivery"),
    ("source_url", "https://example.test/old", "https://example.test/new"),
]


@pytest.mark.parametrize("field,old,new", MUTABLE)
def test_a_corrected_field_is_written_and_the_old_row_is_recoverable(db, field, old, new):
    sig.upsert(db, [_signal(**{field: old})], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(db, [_signal(**{field: new})], seen_at="2026-11-02T00:00:00+00:00")

    row = db.execute(f"SELECT {field} FROM {sig.TABLE} WHERE record_id='reddit:t3_a'").fetchone()
    assert row[field] == new, f"{field} is updated, not frozen at the first sighting"

    rev = db.execute(
        f"SELECT {field}, revision, snapshot_utc FROM {sig.REVISION_TABLE} "
        "WHERE record_id='reddit:t3_a'").fetchone()
    assert rev is not None, "the replaced row was written before the update"
    assert rev[field] == old
    assert rev["revision"] == 0
    assert rev["snapshot_utc"] == "2026-11-02T00:00:00+00:00"


def test_a_changed_title_counts_a_revision_even_though_the_text_did_not_change(db):
    """The old detector was `text_hash != text_hash`. A title correction leaves
    the body untouched, and that was exactly the edit that vanished."""
    sig.upsert(db, [_signal(title="We re-key every order by hand")],
               seen_at="2026-11-01T00:00:00+00:00")
    stats = sig.upsert(db, [_signal(title="We re-key every order by hand, weekly")],
                       seen_at="2026-11-02T00:00:00+00:00")
    row = db.execute(
        f"SELECT title, revisions, edited_utc, text_hash FROM {sig.TABLE}").fetchone()
    assert stats["edited"] == 1
    assert row["title"].endswith("weekly")
    assert row["revisions"] == 1
    assert row["edited_utc"] == "2026-11-02T00:00:00+00:00"


def test_an_unchanged_sighting_writes_no_snapshot(db):
    sig.upsert(db, [_signal()], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(db, [_signal()], seen_at="2026-11-02T00:00:00+00:00")
    assert db.execute(f"SELECT COUNT(*) FROM {sig.REVISION_TABLE}").fetchone()[0] == 0


def test_run_bookkeeping_is_not_an_edit(db):
    """run_id and run_status move on every pass by design; counting them would
    make `revisions` say "twice a day" about a record nobody corrected."""
    sig.upsert(db, [_signal(source_id="t3_b")], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(db, [sig.Signal(source_id="t3_b", platform="reddit", title="same title",
                               text="same body", query="q", run_id="run-2")],
               seen_at="2026-11-02T00:00:00+00:00")
    row = db.execute(f"SELECT revisions, run_id FROM {sig.TABLE}").fetchone()
    assert row["run_id"] == "run-2"
    assert row["revisions"] == 0


def test_a_persons_outcome_survives_a_later_read(db):
    sig.upsert(db, [_signal()], seen_at="2026-11-01T00:00:00+00:00")
    sig.set_outcome(db, "reddit:t3_a", "contacted", note="sent the note")
    sig.upsert(db, [_signal(title="changed title")], seen_at="2026-11-02T00:00:00+00:00")
    row = db.execute(f"SELECT outcome, outcome_note FROM {sig.TABLE}").fetchone()
    assert (row["outcome"], row["outcome_note"]) == ("contacted", "sent the note")


def test_a_restored_revision_puts_the_old_row_back_and_keeps_the_undo(db):
    sig.upsert(db, [_signal(title="Good title")], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(db, [_signal(title="Bad title")], seen_at="2026-11-02T00:00:00+00:00")

    res = sig.restore_revision(db, "reddit:t3_a")
    row = db.execute(f"SELECT title, revisions FROM {sig.TABLE}").fetchone()
    assert row["title"] == "Good title"
    assert row["revisions"] == 2
    assert res["title"] == "Good title"
    # The restore is itself a correction: the bad row is now recoverable too.
    titles = [r[0] for r in db.execute(
        f"SELECT title FROM {sig.REVISION_TABLE} WHERE record_id='reddit:t3_a' "
        "ORDER BY revision_id")]
    assert titles == ["Good title", "Bad title"]


def test_restoring_an_unknown_record_says_so(db):
    with pytest.raises(ValueError):
        sig.restore_revision(db, "reddit:never-seen")


def test_the_revisions_command_lists_and_restores(tmp_path, capsys):
    """Recoverable has to mean reachable from a prompt: the snapshot table is
    useless to the person who noticed the bad correction if putting it back
    needs a Python session."""
    from jester import cli

    path = str(tmp_path / "s.db")
    writer = sig.open_signals(path)
    sig.upsert(writer, [_signal()], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(writer, [_signal(title="bad title")], seen_at="2026-11-02T00:00:00+00:00")
    writer.close()

    cli.main(["signals", "revisions", "--db", path, "--record", "reddit:t3_a"])
    listing = capsys.readouterr().out
    assert "r0" in listing and "same title" in listing

    cli.main(["signals", "revisions", "--db", path, "--record", "reddit:t3_a",
              "--restore"])
    out = capsys.readouterr().out
    assert "restored reddit:t3_a to revision 0" in out

    reader = sig.open_signals(path)
    try:
        assert reader.execute(f"SELECT title FROM {sig.TABLE}").fetchone()[0] == "same title"
    finally:
        reader.close()


# -- 7a: deletions, decided only by a complete read --------------------------

def test_a_posting_gone_from_a_complete_read_is_marked_removed(db, rules):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("1"), _hit("2")], "nbPages": 1}])))
    row = sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("1")], "nbPages": 1}])))

    assert _removed(db) == {"hackernews:2"}
    assert "marked deleted at source" in row["note"]


def test_an_incomplete_read_marks_nothing_removed(db, rules):
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("1"), _hit("2")], "nbPages": 1}])))
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(BrokenSecondPage([
        {"hits": [_hit("1")], "nbPages": 2}])))

    assert _removed(db) == set(), "a read that died early cannot say what is gone"


def test_a_windowed_read_judges_only_what_the_window_could_hold(db, rules):
    """A seven-day HN search says nothing about a record last seen last year:
    it is outside the window, not deleted."""
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("1")], "nbPages": 1}])),
        seen_at="2025-01-01T00:00:00+00:00")
    sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("2")], "nbPages": 1}])))
    assert _removed(db) == set()


def test_the_reads_a_run_made_are_recorded_for_it(db, rules):
    row = sigrun.collect(db, rules=rules, queries=["q"], source=_hn(FakeHTTP([
        {"hits": [_hit("1"), _hit("2")], "nbPages": 1}])))

    reads = db.execute(
        f"SELECT record_id, platform, query, complete FROM {sigrun.READS_TABLE} "
        "WHERE run_id=? ORDER BY record_id", (row["run_id"],)).fetchall()
    assert [r["record_id"] for r in reads] == ["hackernews:1", "hackernews:2"]
    assert {r["platform"] for r in reads} == {"hackernews"}
    assert {r["query"] for r in reads} == {"q"}
    assert all(r["complete"] == 1 for r in reads)


def test_a_failed_read_is_stored_as_incomplete(db, rules):
    row = sigrun.collect(db, rules=rules, queries=["q"], source=_hn(BrokenSecondPage([
        {"hits": [_hit("1")], "nbPages": 2}])))
    reads = db.execute(
        f"SELECT record_id, complete FROM {sigrun.READS_TABLE} WHERE run_id=?",
        (row["run_id"],)).fetchall()
    assert [(r["record_id"], r["complete"]) for r in reads] == [("hackernews:1", 0)]


# -- 7a: export and retention stay reconciled -------------------------------

def test_an_export_carries_removed_rows_and_says_how_many(tmp_path):
    db = sig.open_signals(str(tmp_path / "s.db"))
    try:
        sig.upsert(db, [_signal()], seen_at="2026-11-01T00:00:00+00:00")
        sig.mark_removed(db, ["reddit:t3_a"], at="2026-11-02T00:00:00+00:00")
        manifest = sig.export_csv(db, tmp_path / "out")
    finally:
        db.close()

    # The row stays in the CSV (it was real when collected); the manifest says
    # how many of the rows it carries are marked removed, so id-based
    # reconciliation is still exact.
    assert manifest["rows_in_db"] == 1
    assert manifest["removed_rows"] == 1
    assert manifest["reconciles"] is True
    text = (tmp_path / "out" / "signals.csv").read_text(encoding="utf-8")
    assert "reddit:t3_a" in text and "2026-11-02T00:00:00+00:00" in text


def test_retention_takes_the_revisions_it_should(db):
    """The contract with Task 4's sweep: revision rows are found by discovery
    (a `<TABLE>_` name with a `record_id` column) and die with their record."""
    sig.upsert(db, [_signal(source_id="t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    sig.upsert(db, [_signal(source_id="t3_old", title="edited")],
               seen_at="2025-01-02T00:00:00+00:00")
    sig.upsert(db, [_signal(source_id="t3_new")], seen_at="2026-11-01T00:00:00+00:00")
    sig.upsert(db, [_signal(source_id="t3_new", title="edited")],
               seen_at="2026-11-02T00:00:00+00:00")
    assert db.execute(f"SELECT COUNT(*) FROM {sig.REVISION_TABLE}").fetchone()[0] == 2

    from datetime import datetime, timezone
    assert sig.retention(db, days=30,
                         now=datetime(2026, 11, 3, tzinfo=timezone.utc)) == 1

    left = {r[0] for r in db.execute(f"SELECT record_id FROM {sig.REVISION_TABLE}")}
    assert left == {"reddit:t3_new"}
