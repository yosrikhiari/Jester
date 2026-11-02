"""A backup that has been restored (V3-23).

`backup_db` wrote one file beside the database and nothing ever opened it
again. These pin the two halves of "a backup is proven by a restore":

* the set covers the things that sit beside the archive (journal, chat store,
  the dotenv), records what it did NOT copy (`config-live/`), and can write a
  second copy to another disk;
* `restore_backup` writes to a NEW file, runs `PRAGMA integrity_check`,
  compares the table counts with the manifest, and refuses to overwrite.

Fixture databases and temp directories only: nothing here touches
`data/backups`, a real volume, or this checkout's `.env`.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from jester import backup
from jester.models import Nugget
from jester.store import insert_nugget, open_db


def _seed(path, n=5):
    db = open_db(str(path))
    for i in range(n):
        insert_nugget(db, Nugget(unique_key=f"k{i}", platform="reddit",
                                 raw_text=f"body {i}", extracted_insight=f"i {i}"))
    db.close()


def _sidecar(path):
    """A SQLite file with one user table, standing in for journal/chat."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO entries (body) VALUES ('kept')")
    conn.commit()
    conn.close()


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _seed(root / "jester.db", 7)
    _sidecar(root / "journal.db")
    _sidecar(root / "chat.db")
    env = tmp_path / "repo"
    env.mkdir()
    (env / ".env").write_text("GROQ_API_KEY=sk-not-a-real-key\n", encoding="utf-8")
    return {"root": root, "db": root / "jester.db", "env": env}


def _set(archive, out=None, **kw):
    return backup.backup_set(str(archive["db"]), str(out or archive["root"] / "backups"),
                             env_root=str(archive["env"]), **kw)


def _manifest(res):
    return json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))


# -- the set ----------------------------------------------------------------

def test_the_set_covers_the_things_beside_the_archive(archive):
    res = _set(archive)
    assert res["ok"] is True, res.get("error")

    files = _manifest(res)["files"]
    roles = {entry["role"] for entry in files.values()}
    assert roles == {"archive", "journal", "chat", "env"}
    # The dotenv is copied, and the manifest carries its hash -- not the key.
    env_name = next(n for n, e in files.items() if e["role"] == "env")
    copied = Path(res["path"]).parent / env_name
    assert "sk-not-a-real-key" in copied.read_text(encoding="utf-8")
    assert "sk-not-a-real-key" not in Path(res["manifest"]).read_text(encoding="utf-8")


def test_config_live_is_named_as_not_copied(archive):
    res = _set(archive)
    note = _manifest(res)["not_included"]
    assert "config-live/" in note
    assert "not copied" in note["config-live/"].lower()


def test_a_companion_that_is_not_there_is_absent_not_invented(archive):
    (archive["root"] / "journal.db").unlink()
    res = _set(archive)
    files = _manifest(res)["files"]
    assert {e["role"] for e in files.values()} == {"archive", "chat", "env"}
    assert "journal.db" in _manifest(res)["absent"]


def test_also_to_writes_a_second_copy_off_disk(archive, tmp_path):
    second = tmp_path / "other-disk"
    res = _set(archive, also_to=str(second))
    assert res["ok"] is True, res.get("error")
    assert {p.name for p in second.iterdir()} == {p.name for p in Path(res["path"]).parent.iterdir()}
    # And the copy is the same bytes.
    assert (second / Path(res["path"]).name).read_bytes() == Path(res["path"]).read_bytes()


def test_the_single_db_api_still_behaves_as_before(archive):
    res = backup.backup_db(str(archive["db"]), str(archive["root"] / "plain"))
    assert res["ok"] is True and Path(res["path"]).is_file()
    # The callers' shape: (path, out_dir, compress=...) with no fourth value.
    again = backup.backup_db(str(archive["db"]), str(archive["root"] / "plain"),
                             compress=False)
    assert again["ok"] is True and again["path"].endswith(".db")


# -- restore ----------------------------------------------------------------

def test_a_restored_backup_is_readable_and_matches_the_manifest(archive, tmp_path):
    res = _set(archive)
    dest = tmp_path / "restored" / "jester.db"

    got = backup.restore_backup(res["path"], str(dest))
    assert got["ok"] is True, got.get("error")
    assert got["integrity"] == "ok"
    assert got["matches_manifest"] is True
    assert got["counts"]["nuggets"] == 7

    check = backup.verify_backup(str(dest))
    assert check["ok"] is True
    assert check["counts"]["nuggets"] == 7


def test_a_corrupt_backup_is_refused_and_leaves_nothing(archive, tmp_path):
    bad = tmp_path / "backups" / "jester-broken.db.gz"
    bad.parent.mkdir()
    bad.write_bytes(b"this is not gzip")
    dest = tmp_path / "restored.db"

    got = backup.restore_backup(str(bad), str(dest))
    assert got["ok"] is False
    assert "unreadable" in got["error"] or "gzip" in got["error"].lower()
    assert not dest.exists(), "a refused restore must not leave a file that looks restored"


def test_an_existing_destination_is_not_overwritten(archive, tmp_path):
    res = _set(archive)
    dest = tmp_path / "already-there.db"
    dest.write_bytes(b"do not touch")

    got = backup.restore_backup(res["path"], str(dest))
    assert got["ok"] is False
    assert "refus" in got["error"].lower()
    assert dest.read_bytes() == b"do not touch"


def test_a_count_mismatch_is_refused(archive, tmp_path):
    res = _set(archive)
    manifest_path = Path(res["manifest"])
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["files"][data["backup"]]["counts"]["nuggets"] = 999
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    dest = tmp_path / "restored.db"
    got = backup.restore_backup(res["path"], str(dest))
    assert got["ok"] is False
    assert "counts" in got["error"]
    assert not dest.exists()


def test_a_restore_without_a_manifest_says_so_rather_than_pretending(archive, tmp_path):
    res = _set(archive)
    Path(res["manifest"]).unlink()
    got = backup.restore_backup(res["path"], str(tmp_path / "restored.db"))
    assert got["ok"] is True
    assert got["matches_manifest"] is None
    assert "manifest" in got["note"]


def test_a_backup_that_does_not_match_its_manifest_hash_is_refused(archive, tmp_path):
    """The counts are an aggregate: a file edited in place can still hold the
    right number of rows. The hash in the manifest is what says it is the file
    that was written, so restoring has to check it before writing anything."""
    res = _set(archive, compress=False)
    backup_file = Path(res["path"])
    backup_file.write_bytes(backup_file.read_bytes() + b"\x00")
    dest = tmp_path / "restored.db"

    got = backup.restore_backup(res["path"], str(dest))
    assert got["ok"] is False
    assert "does not match its manifest hash" in got["error"]
    assert not dest.exists(), "a refused restore must not leave a file behind"


def test_a_companion_whose_counts_are_all_unknown_says_it_did_not_compare(archive, tmp_path):
    """`journal.db` has none of the counted tables, so its manifest entry is
    `{nuggets: None, ...}`. Comparing two dictionaries of Nones "matched" and
    said so, which is a promise about a check that never happened."""
    res = _set(archive)
    files = _manifest(res)["files"]
    name = next(n for n, e in files.items() if e["role"] == "journal")
    src = Path(res["path"]).parent / name
    # The set's manifest is one file, and every entry in it is keyed by the
    # file it describes; `restore_backup` looks for it beside the file it is
    # restoring.
    Path(str(src) + ".manifest.json").write_text(
        Path(res["manifest"]).read_text(encoding="utf-8"), encoding="utf-8")

    got = backup.restore_backup(str(src), str(tmp_path / "journal.db"))
    assert got["ok"] is True, got.get("error")
    assert got["matches_manifest"] is None, got
    assert got["expected"] is None
    assert "counts match" not in got["note"]
    assert "no comparable counts" in got["note"], got["note"]


def test_an_unwritable_destination_is_reported_not_raised(archive, tmp_path):
    """A parent that cannot be made is an error like any other: the caller
    reads `ok`/`error`, and a traceback out of a restore is a crash in the one
    command someone runs when the archive is already gone."""
    res = _set(archive)
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory", encoding="utf-8")

    got = backup.restore_backup(res["path"], str(blocked / "restored.db"))
    assert got["ok"] is False
    assert got["error"]


def test_a_backup_whose_hash_cannot_be_read_is_reported_not_raised(archive, tmp_path, monkeypatch):
    """The manifest read, hash and comparison ran outside any try, so a backup
    the process cannot open (locked on Windows, a permission bit on POSIX)
    raised out of the restore instead of answering. That is the same crash
    class the unwritable-destination fix removed, one step earlier."""
    res = _set(archive, compress=False)

    def locked(path):
        raise PermissionError(13, "The process cannot access the file: locked", str(path))

    monkeypatch.setattr(backup, "_sha256_file", locked)

    dest = tmp_path / "restored.db"
    got = backup.restore_backup(res["path"], str(dest))
    assert got["ok"] is False
    assert "unreadable" in got["error"]
    assert not dest.exists(), "nothing was written, so nothing may look restored"


# -- the drill --------------------------------------------------------------

def test_the_drill_restores_into_a_temporary_directory_and_reports(archive, monkeypatch):
    res = _set(archive)
    got = backup.restore_drill(res["path"])
    assert got["ok"] is True, got.get("error")
    assert got["counts"]["nuggets"] == 7
    # The temp directory is removed; the report keeps what it proved.
    assert not Path(got["path"]).exists()
    assert got["seconds"] >= 0
    # And the archive's own backup directory was not written to.
    assert "restored" not in " ".join(p.name for p in Path(res["path"]).parent.iterdir())


def test_the_drill_finds_the_newest_backup_when_none_is_named(archive):
    first = _set(archive)
    got = backup.restore_drill(db_path=str(archive["db"]),
                               out_dir=str(archive["root"] / "backups"))
    assert got["ok"] is True, got.get("error")
    assert got["backup"] == first["path"]


def test_the_drill_reports_a_corrupt_newest_backup_as_a_failure(archive, tmp_path):
    out = archive["root"] / "backups"
    out.mkdir(exist_ok=True)
    (out / "jester-29991231T000000Z.db.gz").write_bytes(b"not gzip")
    got = backup.restore_drill(db_path=str(archive["db"]), out_dir=str(out))
    assert got["ok"] is False
    assert "unreadable" in got["error"] or "gzip" in got["error"].lower()


def test_pruning_takes_the_manifest_with_the_backup(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    for stamp in ("20260101T000000Z", "20260102T000000Z"):
        (d / f"jester-{stamp}.db.gz").write_bytes(b"x")
        (d / f"jester-{stamp}.db.gz.manifest.json").write_bytes(b"{}")

    backup.prune_backups(str(d), keep=1, stem="jester")
    assert [p.name for p in d.iterdir()] == [
        "jester-20260102T000000Z.db.gz",
        "jester-20260102T000000Z.db.gz.manifest.json",
    ]


# -- the command ------------------------------------------------------------
#
# The library above is only half of it: `restore_backup` and `also_to` were
# reachable from a Python prompt and nowhere else, so "a backup that has been
# restored" was not something an operator could do. These drive `cli.main`.

def _cli(argv, monkeypatch):
    """Run the CLI with the repo's dotenv kept out of os.environ.

    `cli.main` calls `load_env_once()`, which reads this checkout's `.env`
    into the process and leaves it there — the same trap `test_schedule.py`
    documents. Every test here gets its environment back.

    `main` returns None, so these assert on what the command printed and what
    it left on disk: the artifact is the claim.
    """
    import os

    from jester import cli

    before = os.environ.copy()
    try:
        return cli.main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(before)


def _run_backup(archive, out, monkeypatch, *extra):
    return _cli(["backup", "--db", str(archive["db"]), "--out", str(out),
                 "--env-root", str(archive["env"]), *extra], monkeypatch)


def _newest(out, pattern="jester-*.db.gz"):
    found = sorted(Path(out).glob(pattern))
    assert found, f"no {pattern} in {out}"
    return found[-1]


def test_the_command_writes_a_set_and_says_what_it_covers(archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cmd-backups"
    _run_backup(archive, out, monkeypatch)

    printed = capsys.readouterr().out
    assert "wrote " in printed and "verified: integrity ok" in printed
    # The companions are named, because "the backup covers the journal" is a
    # claim the owner has to be able to check.
    assert "also wrote:" in printed and "journal" in printed
    assert "manifest:" in printed
    made = _newest(out)
    assert Path(str(made) + ".manifest.json").is_file()


def test_also_to_is_the_flag_that_writes_the_second_copy(archive, tmp_path, monkeypatch, capsys):
    out, second = tmp_path / "cmd-backups", tmp_path / "elsewhere"
    _run_backup(archive, out, monkeypatch, "--also-to", str(second))
    assert "second copy:" in capsys.readouterr().out
    assert {p.name for p in second.iterdir()} == {p.name for p in out.iterdir()}


def test_no_second_directory_is_written_when_the_flag_is_absent(archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "only-one"
    before = set(tmp_path.iterdir())
    _run_backup(archive, out, monkeypatch)
    assert "second copy:" not in capsys.readouterr().out
    # One directory, and it is the one that was asked for.
    assert out.is_dir()
    assert set(tmp_path.iterdir()) - before == {out}


def test_the_restore_command_writes_a_new_file_and_proves_it(archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cmd-backups"
    _run_backup(archive, out, monkeypatch)
    made = _newest(out)
    dest = tmp_path / "put-back" / "jester.db"

    _cli(["backup", "restore", str(made), str(dest)], monkeypatch)

    printed = capsys.readouterr().out
    assert "restored " in printed and "nuggets=7" in printed
    assert "never overwrites" in printed
    check = backup.verify_backup(str(dest))
    assert check["ok"] is True and check["counts"]["nuggets"] == 7


def test_the_restore_command_refuses_an_existing_destination(archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cmd-backups"
    _run_backup(archive, out, monkeypatch)
    made = _newest(out)
    dest = tmp_path / "already-here.db"
    dest.write_bytes(b"keep me")

    with pytest.raises(SystemExit) as exit_info:
        _cli(["backup", "restore", str(made), str(dest)], monkeypatch)

    assert exit_info.value.code == 1
    assert "refus" in capsys.readouterr().out.lower()
    assert dest.read_bytes() == b"keep me"


def test_the_restore_command_refuses_a_corrupt_backup(archive, tmp_path, monkeypatch, capsys):
    bad = tmp_path / "jester-broken.db.gz"
    bad.write_bytes(b"not gzip at all")
    dest = tmp_path / "never-written.db"

    with pytest.raises(SystemExit) as exit_info:
        _cli(["backup", "restore", str(bad), str(dest)], monkeypatch)

    assert exit_info.value.code == 1
    assert "restore refused" in capsys.readouterr().out
    assert not dest.exists()


def test_the_drill_command_restores_the_newest_backup_into_a_temp_dir(
        archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cmd-backups"
    _run_backup(archive, out, monkeypatch)
    made = _newest(out)
    before = {p.name for p in out.iterdir()}

    _cli(["backup", "drill", "--db", str(archive["db"]),
          "--out", str(out)], monkeypatch)

    printed = capsys.readouterr().out
    assert "drill OK" in printed and "nuggets=7" in printed
    assert str(made) in printed, "the drill says WHICH backup it opened"
    # Rehearsal, not a restore into place: nothing was added beside the
    # archive, and the temp copy is reported as removed.
    assert {p.name for p in out.iterdir()} == before
    assert "temp directory was removed" in printed


def test_the_drill_command_fails_loudly_on_a_corrupt_newest_backup(archive, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cmd-backups"
    out.mkdir()
    (out / "jester-29991231T000000Z.db.gz").write_bytes(b"not gzip")

    with pytest.raises(SystemExit) as exit_info:
        _cli(["backup", "drill", "--db", str(archive["db"]),
              "--out", str(out)], monkeypatch)

    assert exit_info.value.code == 1
    assert "drill FAILED" in capsys.readouterr().out


def test_pruning_keeps_whole_sets_not_the_newest_of_each_kind(archive, tmp_path):
    """Extending the set to journal/chat/dotenv without this would keep the
    newest 14 of EVERY kind and never remove a set."""
    out = tmp_path / "sets"
    out.mkdir()
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        for stem in ("jester", "journal", "chat"):
            (out / f"{stem}-{stamp}.db.gz").write_bytes(b"x")
        (out / f"dotenv-{stamp}.env").write_bytes(b"x")

    removed = backup.prune_backup_sets(str(out), keep=2)

    left = sorted({backup._set_stamp(p.name) for p in out.iterdir()})
    assert left == ["20260102T000000Z", "20260103T000000Z"]
    assert len(removed) == 4, "the oldest set went whole: 3 files + the dotenv"
