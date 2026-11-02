"""Task 4 — the signals archive's own trust properties (V3-06).

Three claims that were promises rather than mechanisms:

* **An export is complete or absent.** The CSV is written to a temp file,
  flushed, fsynced and renamed; the manifest is written the same way and only
  after the CSV rename succeeded; a failure anywhere removes both, so a
  truncated file never sits beside a manifest claiming a reconciliation
  nobody performed.
* **Reconciliation is by identity, not count.** The manifest carries the
  sha256 of the sorted `record_id` set and of the file's own bytes, and these
  tests recompute both from the file on disk rather than trusting the flag.
  A file that lost a row while keeping its row count must not reconcile.
* **ClickHouse can evolve, and retention exists.** A field the table is
  missing is added with one `ALTER ... ADD COLUMN IF NOT EXISTS` and nothing
  is dropped; the loader records a schema version; and the archive can prune
  records past a window when told to — off by default, because deleting
  archived signals is a decision, not a default.

Every ClickHouse test here uses a fake client. A suite that needs a container
is a suite people stop running, and the SQL sent is what can silently be
wrong.
"""
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from jester import signals as sig
from jester.signals import clickhouse as ch


# ---- fixtures --------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "signals.db"))
    yield conn
    conn.close()


def _seeded(db, seen_at="2026-09-20T00:00:00+00:00"):
    sig.upsert(db, sig.synthetic_signals(run_id="t"), seen_at=seen_at)


def _signal(source_id, **kw):
    base = dict(source_id=source_id, source_url="u", community="r/x", query="q",
                text=f"a problem worth solving: {source_id}", match_reason="r",
                match_confidence=0.8)
    base.update(kw)
    return sig.Signal(**base)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ids_sha256(ids) -> str:
    return _sha256("\n".join(sorted(ids)).encode("utf-8"))


# ---- 4a: an export is complete or absent ------------------------------------

def test_the_manifest_proves_identity_and_the_file_hash(db, tmp_path):
    """The flag is not the evidence. Recompute both hashes from the file the
    export left behind, and compare the id set with the table's."""
    _seeded(db)
    manifest = sig.export_csv(db, tmp_path, mode="synthetic")

    path = tmp_path / "signals-synthetic.csv"
    raw = path.read_bytes()
    with path.open(encoding="utf-8", newline="") as fh:
        file_ids = sorted({r["record_id"] for r in csv.DictReader(fh)})
    db_ids = sorted(r[0] for r in db.execute(f"SELECT record_id FROM {sig.TABLE}"))

    assert file_ids == db_ids and len(file_ids) == 5
    assert manifest["rows_in_db"] == manifest["rows_in_csv"] == 5
    assert manifest["content_sha256"] == _sha256(raw), \
        "the manifest must hash the bytes that are on disk"
    assert manifest["row_ids_sha256"] == _ids_sha256(db_ids)
    assert manifest["reconciles"] is True

    saved = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert saved["content_sha256"] == manifest["content_sha256"]
    assert saved["row_ids_sha256"] == manifest["row_ids_sha256"]
    # The keys the CI step reads are untouched.
    assert saved["rows_in_db"] == saved["rows_in_csv"] == 5


def test_a_file_that_lost_a_row_does_not_reconcile_even_at_the_same_count(db, tmp_path, monkeypatch):
    """Identity, not count. Drop one id while leaving the row count alone: the
    count still matches and `reconciles` must still be False."""
    _seeded(db)
    real = sig._read_csv_ids

    def short_by_one(path):
        ids, rows = real(path)
        return ids[1:], rows

    monkeypatch.setattr(sig, "_read_csv_ids", short_by_one)
    manifest = sig.export_csv(db, tmp_path, mode="synthetic")

    assert manifest["rows_in_db"] == manifest["rows_in_csv"] == 5
    assert manifest["reconciles"] is False, \
        "a count that matches is not a reconciliation"


def test_a_crash_before_the_manifest_leaves_no_export(db, tmp_path, monkeypatch):
    """The window the old writer left open: CSV renamed, manifest not written.
    A crash there must leave neither, so a directory is never read as an
    export that half happened."""
    _seeded(db)
    sig.export_csv(db, tmp_path, mode="synthetic")
    assert (tmp_path / "manifest.json").exists(), "a good export first"

    real_replace = sig.os.replace

    def replace(src, dst, *a, **kw):
        if str(dst).endswith("manifest.json"):
            raise OSError("injected: crash between the CSV and the manifest")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(sig.os, "replace", replace)
    with pytest.raises(OSError, match="injected"):
        sig.export_csv(db, tmp_path, mode="synthetic")

    assert not (tmp_path / "signals-synthetic.csv").exists(), \
        "no final CSV: the export is absent, not half present"
    assert not (tmp_path / "manifest.json").exists(), \
        "no manifest may survive to claim a success"
    assert not list(tmp_path.glob("*.tmp")), "temp files are cleaned up"


def test_the_old_manifest_is_invalidated_before_the_csv_replaces_it(
        db, tmp_path, monkeypatch):
    """The hard-kill window, not the handled exception.

    A SIGKILL between the two renames cannot run the cleanup below, so what
    survives is whatever the two names hold at that instant. If the PREVIOUS
    manifest were still on disk when the new CSV landed, it would vouch for
    bytes (and possibly a `csv` name) it never hashed — a manifest that reads
    as a successful export. The old manifest must therefore be gone BEFORE
    the CSV rename: the worst case is then "manifest absent", which is
    detectably incomplete. The end state after a handled exception is clean
    either way, so the state at the CSV rename is what is asserted.
    """
    _seeded(db)
    sig.export_csv(db, tmp_path, mode="synthetic")
    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.exists(), "a good export first"

    real_replace = sig.os.replace
    manifest_at_csv_rename = "not observed"

    def replace(src, dst, *a, **kw):
        nonlocal manifest_at_csv_rename
        if str(dst).endswith("signals-synthetic.csv"):
            manifest_at_csv_rename = manifest_path.exists()
        if str(dst).endswith("manifest.json"):
            raise OSError("injected: killed before the new manifest landed")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(sig.os, "replace", replace)
    with pytest.raises(OSError, match="injected"):
        sig.export_csv(db, tmp_path, mode="synthetic")

    assert manifest_at_csv_rename is False, \
        "the previous manifest must be removed before the CSV rename"
    assert not manifest_path.exists(), \
        "no manifest may be left vouching for bytes it never hashed"
    assert not (tmp_path / "signals-synthetic.csv").exists(), \
        "the handled path still leaves no export"


def test_a_crash_while_the_csv_is_written_leaves_nothing(db, tmp_path, monkeypatch):
    def die(fd):
        raise OSError("injected: fsync failed")

    _seeded(db)
    monkeypatch.setattr(sig.os, "fsync", die)
    with pytest.raises(OSError, match="injected"):
        sig.export_csv(db, tmp_path, mode="synthetic")

    assert not (tmp_path / "signals-synthetic.csv").exists()
    assert not (tmp_path / "manifest.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_a_successful_export_leaves_no_temp_files(db, tmp_path):
    out = tmp_path / "out"
    _seeded(db)
    sig.export_csv(db, out, mode="synthetic")
    assert sorted(p.name for p in out.iterdir()) == [
        "field-map.md", "manifest.json", "schema.clickhouse.sql", "signals-synthetic.csv"]


# ---- 4b: ClickHouse can gain a field ---------------------------------------

class FakeClient:
    """A ClickHouse client stand-in: logs the SQL it is asked to run and
    answers `DESCRIBE` from a canned column list.

    Duck-typed on purpose. What is under test is the sequence of statements —
    one ALTER for the missing field, none for a current schema, no DROP
    anywhere — not one private method of `Client`.
    """

    def __init__(self, columns):
        self.columns = list(columns)
        self.calls = []

    def execute(self, sql, body="", **params):
        self.calls.append(sql)
        upper = sql.upper()
        if upper.startswith("DESCRIBE"):
            return "".join(f"{name}\tString\n" for name in self.columns)
        # A real server's DESCRIBE changes after an ADD COLUMN; the fake has
        # to as well, or a second evolve is judged against a stale picture.
        marker = "ADD COLUMN IF NOT EXISTS "
        at = upper.find(marker)
        if "ALTER TABLE" in upper and at != -1:
            name = sql[at + len(marker):].split()[0]
            if name not in self.columns:
                self.columns.append(name)
        return ""


def _alters(client):
    return [s for s in client.calls if "ALTER TABLE" in s.upper()]


def _drops(client):
    return [s for s in client.calls if "DROP" in s.upper()]


def test_a_new_field_triggers_one_alter_and_no_recreate():
    client = FakeClient([n for n in sig.FIELD_NAMES if n != "outcome_note"])

    res = ch.evolve_schema(client)

    alters = _alters(client)
    assert len(alters) == 1, f"expected one ALTER, got {alters}"
    assert "ADD COLUMN IF NOT EXISTS outcome_note" in alters[0]
    assert res["columns_added"] == ["outcome_note"]
    assert _drops(client) == [], "adding a field must never drop anything"


def test_a_current_schema_triggers_no_alter():
    client = FakeClient(sig.FIELD_NAMES)
    res = ch.evolve_schema(client)
    assert _alters(client) == []
    assert res["columns_added"] == []


def test_a_second_evolve_after_the_column_lands_issues_no_alter():
    """Schema evolution must converge. The first load adds the missing field
    (one ALTER); a second load against the table as it now is must add
    nothing, or a loader that compares against a stale picture would issue
    the same ALTER on every run."""
    client = FakeClient([n for n in sig.FIELD_NAMES if n != "outcome_note"])

    first = ch.evolve_schema(client)
    assert first["columns_added"] == ["outcome_note"]
    assert len(_alters(client)) == 1

    client.calls.clear()
    second = ch.evolve_schema(client)
    assert second["columns_added"] == []
    assert _alters(client) == [], "a column already present is never altered twice"
    assert _drops(client) == []


def test_a_column_the_code_no_longer_knows_is_left_alone():
    """The field map only ever adds. A column someone added by hand — or the
    code stopped writing — is not evidence to drop it, and the DDL may not
    start removing fields because a table has more than the map."""
    client = FakeClient(sig.FIELD_NAMES + ["hand_added"])
    ch.evolve_schema(client)
    assert _alters(client) == []
    assert _drops(client) == []
    assert "DROP" not in sig.clickhouse_ddl().upper()


def test_the_schema_version_is_recorded_beside_the_table():
    client = FakeClient(sig.FIELD_NAMES)
    ch.evolve_schema(client)

    assert sig.SCHEMA_VERSION >= 2
    assert any(f"CREATE TABLE IF NOT EXISTS {sig.SCHEMA_VERSION_TABLE}" in s
               for s in client.calls)
    inserted = [s for s in client.calls if "INSERT INTO" in s.upper()]
    assert len(inserted) == 1
    assert str(sig.SCHEMA_VERSION) in inserted[0]
    assert sig.TABLE in inserted[0]


def test_an_unreadable_describe_adds_nothing():
    """A table that cannot be described is not evidence that all 33 columns
    are missing; blind ALTERs against a live server are not a repair."""
    client = FakeClient([])
    res = ch.evolve_schema(client)
    assert _alters(client) == []
    assert res["columns_added"] == []


class _Response:
    def __init__(self, body=""):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Transport:
    """A fake HTTP transport for `ch.Client`."""

    def __init__(self, describe=None):
        self.describe = describe
        self.calls = []

    def __call__(self, req, timeout=None):
        import urllib.parse
        sql = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.full_url).query).get("query", [""])[0]
        self.calls.append(sql)
        if self.describe is not None and sql.upper().startswith("DESCRIBE"):
            return _Response("".join(f"{n}\tString\n" for n in self.describe))
        return _Response("")


def test_the_load_evolves_the_live_table_and_says_what_it_added(db):
    """Through the real Client, so the wiring is exercised and not only the
    function: `load` is what actually runs on a schedule."""
    _seeded(db)
    transport = _Transport(describe=[n for n in sig.FIELD_NAMES if n != "error"])

    res = ch.load(db, ch.Client(opener=transport), optimize=False)

    queries = transport.calls
    assert any("ADD COLUMN IF NOT EXISTS error" in q for q in queries)
    assert not any("DROP" in q.upper() for q in queries)
    assert res["rows_sent"] == 5
    assert res["schema"]["columns_added"] == ["error"]


# ---- 4c: retention ----------------------------------------------------------

def test_retention_removes_only_rows_past_the_window(db):
    sig.upsert(db, [_signal("t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_new")], seen_at="2026-09-20T00:00:00+00:00")

    deleted = sig.retention(db, days=30, now=datetime(2026, 10, 1, tzinfo=timezone.utc))

    remaining = {r[0] for r in db.execute(f"SELECT record_id FROM {sig.TABLE}")}
    assert deleted == 1
    assert remaining == {"reddit:t3_new"}, "a fresh record is never swept"


def test_retention_is_idempotent(db):
    sig.upsert(db, [_signal("t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert sig.retention(db, days=30, now=now) == 1
    assert sig.retention(db, days=30, now=now) == 0, "a second run has nothing to do"


def test_retention_uses_last_seen_not_first_seen(db):
    """A record collected long ago and still being seen is still current: the
    window is about how long the archive has held it unwatched."""
    sig.upsert(db, [_signal("t3_kept")], seen_at="2025-01-01T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_kept")], seen_at="2026-09-25T00:00:00+00:00")
    assert sig.retention(db, days=30,
                         now=datetime(2026, 10, 1, tzinfo=timezone.utc)) == 0


def test_retention_refuses_a_window_that_is_not_a_window(db):
    with pytest.raises(ValueError):
        sig.retention(db, days=0)


def test_retention_never_deletes_a_row_whose_age_it_cannot_read(db):
    """A table that predates `last_seen_utc` leaves '' there, and an empty
    string compares as older than every date. Deleting on a date that is not
    there is the destructive guess this command must not make."""
    sig.upsert(db, [_signal("t3_legacy")], seen_at="2025-01-01T00:00:00+00:00")
    db.execute(f"UPDATE {sig.TABLE} SET last_seen_utc = ''")
    db.commit()

    deleted = sig.retention(db, days=30, now=datetime(2026, 10, 1, tzinfo=timezone.utc))

    assert deleted == 0
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 1


def test_retention_takes_revision_rows_with_it(db):
    """Task 7 adds revision rows. Retention must not leave them orphaned
    behind a deleted record, and it must find them without being told the
    table's name."""
    # A real edit through `upsert`, not a hand-made table: the contract is
    # that the snapshot Task 7 writes is swept, and a fixture with invented
    # columns would keep passing after the real table drifted.
    sig.upsert(db, [_signal("t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_old", title="edited")], seen_at="2025-01-02T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_new")], seen_at="2026-09-20T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_new", title="edited")], seen_at="2026-09-20T00:00:00+00:00")
    assert db.execute(
        f"SELECT COUNT(*) FROM {sig.REVISION_TABLE}").fetchone()[0] == 2

    assert sig.retention(db, days=30,
                         now=datetime(2026, 10, 1, tzinfo=timezone.utc)) == 1

    left = {r[0] for r in db.execute(f"SELECT record_id FROM {sig.REVISION_TABLE}")}
    assert left == {"reddit:t3_new"}


def test_the_retention_command_deletes_and_says_so(tmp_path, capsys):
    from jester import cli

    # The command reads the real clock, so the fresh row is dated relative to
    # it rather than to the fixed stamps the other retention tests use.
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    path = str(tmp_path / "s.db")
    db = sig.open_signals(path)
    sig.upsert(db, [_signal("t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    sig.upsert(db, [_signal("t3_new")], seen_at=fresh)
    db.close()

    cli.main(["signals", "retention", "--db", path, "--days", "30"])

    out = capsys.readouterr().out
    assert "deleted 1 record" in out

    db = sig.open_signals(path)
    try:
        left = {r[0] for r in db.execute(f"SELECT record_id FROM {sig.TABLE}")}
    finally:
        db.close()
    assert left == {"reddit:t3_new"}


def test_the_retention_command_refuses_an_out_of_range_window(tmp_path, capsys):
    """`--days 0` is not "everything" — it is refused, exits 1, and deletes
    nothing. The library guard is tested above; this covers the CLI branch
    that turns it into an exit code and a message."""
    from jester import cli

    path = str(tmp_path / "s.db")
    db = sig.open_signals(path)
    sig.upsert(db, [_signal("t3_old")], seen_at="2025-01-01T00:00:00+00:00")
    db.close()

    with pytest.raises(SystemExit) as exc:
        cli.main(["signals", "retention", "--db", path, "--days", "0"])

    assert exc.value.code == 1
    assert "retention:" in capsys.readouterr().out

    db = sig.open_signals(path)
    try:
        left = {r[0] for r in db.execute(f"SELECT record_id FROM {sig.TABLE}")}
    finally:
        db.close()
    assert left == {"reddit:t3_old"}, "a refused window deletes nothing"


# ---- the declaration: retention exists and is off by default ---------------

def test_signals_retention_is_declared_and_paused():
    from jester import schedule

    specs = {s["command"]: s for s in schedule.load_desired()}

    assert schedule.TASK_FOR_COMMAND["signals-retention"] == \
        "JesterSignalsRetention"
    spec = specs["signals-retention"]
    assert spec["enabled"] is False, \
        "deleting archived signals is the owner's decision, not an install side effect"
    assert schedule.describe_cadence(spec) == "weekly on SUN at 04:00"

    argv = schedule.command_argv("signals-retention")
    assert argv[:2] == ("signals", "retention")
    assert argv[argv.index("--days") + 1] == "365"
    assert schedule.command_takes_config("signals-retention") is False
