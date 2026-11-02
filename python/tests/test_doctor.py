"""jester doctor tests (§37.11): health findings + exit-code contract."""
import sqlite3
from pathlib import Path

import pytest

from jester.models import Idea, IdeaScores, Nugget
from jester.store import (
    enqueue_batch,
    finish_run,
    insert_idea,
    insert_nugget,
    mark_batch_failed,
    open_db,
    start_run,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _seed_healthy(db):
    start_run(db, "h1")
    finish_run(db, "h1", status="completed", duration_actual_s=1.0)
    insert_nugget(db, Nugget(unique_key="reddit:a:c1", platform="reddit",
                             category="pain_point", extracted_insight="x",
                             needs_reembed=False))
    insert_idea(db, Idea(title="t", supporting_nuggets=["reddit:a:c1"],
                         source_platforms=[], scores=IdeaScores()))


# --- Task 1: healthy DBs --------------------------------------------------------

def test_fresh_db_is_healthy(tmp_path):
    from jester.cli import evaluate_doctor

    assert evaluate_doctor(open_db(str(tmp_path / "j.db"))) == []


def test_seeded_healthy_db_has_no_findings(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    _seed_healthy(db)
    assert evaluate_doctor(db) == []


def test_disabled_generative_tasks_are_reported_without_failing(
        tmp_path, monkeypatch, capsys):
    """The loop is paused by configuration, not broken. Doctor must say so
    plainly and stay exit-0: a deliberate pause is not a health failure."""
    import jester.cli as cli

    states = {
        "JesterNightly": {"installed": True, "enabled": False,
                          "last_run": "2026-10-02 16:35:00"},
        "JesterNightlyScrape": {"installed": True, "enabled": False,
                                "last_run": "2026-10-02 16:35:00"},
        "JesterNightlyTreat": {"installed": True, "enabled": False,
                               "last_run": "2026-10-02 16:35:00"},
    }
    monkeypatch.setattr(
        cli._schedule, "status",
        lambda task_name="JesterNightly": states.get(task_name, {"installed": False}))

    notes = cli.pipeline_paused_notes()
    assert len(notes) == 3
    assert all("paused" in n and "disabled" in n for n in notes)

    cli.cmd_doctor(_ns(db=str(tmp_path / "j.db")))
    out = capsys.readouterr().out
    assert "doctor OK" in out
    assert "paused" in out


# --- ND-07: a diagnostic that only reads ---------------------------------------

def _archive(tmp_path):
    db_path = tmp_path / "old.db"
    db = open_db(str(db_path))
    _seed_healthy(db)
    db.close()
    return db_path


def _tables(db_path):
    db = sqlite3.connect(db_path)
    try:
        return {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def test_doctor_no_migrate_names_the_absent_subsystems_and_changes_nothing(
        tmp_path, capsys):
    """`doctor` used to migrate the archive it was diagnosing, so asking
    "has this been migrated?" could itself make the answer yes. Read-only mode
    must name what is missing and leave the file byte-identical."""
    from jester.cli import LAZY_SUBSYSTEM_TABLES, MIGRATED_TABLES, cmd_doctor

    db_path = _archive(tmp_path)
    db = sqlite3.connect(db_path)
    for table in MIGRATED_TABLES + LAZY_SUBSYSTEM_TABLES:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    db.commit()
    db.close()
    before = db_path.read_bytes()

    cmd_doctor(_ns(db=str(db_path), no_migrate=True))

    out = capsys.readouterr().out
    assert "no migration" in out
    assert "not been migrated" in out
    assert "llm_calls" in out
    assert db_path.read_bytes() == before, "a diagnostic must not write"
    assert "llm_calls" not in _tables(db_path)


def test_doctor_no_migrate_says_so_when_a_migrated_archive_is_whole(tmp_path, capsys):
    from jester.cli import cmd_doctor

    db_path = _archive(tmp_path)

    cmd_doctor(_ns(db=str(db_path), no_migrate=True))

    out = capsys.readouterr().out
    assert "carries the migrated schema" in out
    assert "has not been migrated" not in out
    # These two are created by their subsystems on first use, so a fresh
    # migration legitimately lacks them; Doctor says which, rather than
    # treating "never run" as "not migrated".
    assert "never used since migration" in out
    assert "extraction_specs" in out and "maintenance_events" in out


def test_doctor_no_migrate_on_a_file_with_no_schema_is_one_finding(tmp_path, capsys):
    from jester.cli import cmd_doctor

    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    with pytest.raises(SystemExit) as exc:
        cmd_doctor(_ns(db=str(db_path), no_migrate=True))

    assert exc.value.code == 1
    assert "no Jester schema" in capsys.readouterr().out


def test_doctor_no_migrate_does_not_create_a_missing_archive(tmp_path, capsys):
    """open_db creates the file and its parent on connect. A read-only
    diagnostic pointed at a path that is not an archive must say so, not bring
    an empty database into existence."""
    from jester.cli import cmd_doctor

    db_path = tmp_path / "never" / "there.db"

    with pytest.raises(SystemExit) as exc:
        cmd_doctor(_ns(db=str(db_path), no_migrate=True))

    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().out
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_doctor_default_still_migrates(tmp_path, capsys):
    from jester.cli import cmd_doctor

    db_path = _archive(tmp_path)
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE IF EXISTS llm_calls")
    db.commit()
    db.close()

    cmd_doctor(_ns(db=str(db_path)))

    assert "schema migrated" in capsys.readouterr().out
    assert "llm_calls" in _tables(db_path)


# --- Task 2: each finding fires independently ------------------------------------

def test_stale_running_row_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "crashed")  # never finished
    findings = evaluate_doctor(db)
    assert any("running" in f for f in findings)


def test_failed_batch_detected_with_requeue_hint(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)
    findings = evaluate_doctor(db)
    assert any("failed batch" in f and "requeue" in f for f in findings)


def test_reembed_backlog_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(unique_key="k1", category="pain_point",
                             extracted_insight="x", needs_reembed=True))
    findings = evaluate_doctor(db)
    assert any("reembed_backlog" in f for f in findings)


def test_orphaned_citation_detected(tmp_path):
    from jester.cli import evaluate_doctor

    db = open_db(str(tmp_path / "j.db"))
    insert_idea(db, Idea(title="ghost", supporting_nuggets=["missing:key"],
                         source_platforms=[], scores=IdeaScores()))
    findings = evaluate_doctor(db)
    assert any("orphaned" in f for f in findings)


# --- Task 3: cmd_doctor CLI contract ----------------------------------------------

def test_cmd_doctor_clean_exits_zero_and_prints_ok(tmp_path, capsys):
    from jester.cli import cmd_doctor

    p = tmp_path / "j.db"
    db = open_db(str(p))
    _seed_healthy(db)

    cmd_doctor(_ns(db=str(p)))  # must not raise SystemExit
    assert "doctor OK" in capsys.readouterr().out


def test_cmd_doctor_unhealthy_exits_one_with_findings(tmp_path, capsys):
    from jester.cli import cmd_doctor

    p = tmp_path / "j.db"
    db = open_db(str(p))
    start_run(db, "stale-run")

    with pytest.raises(SystemExit) as exc:
        cmd_doctor(_ns(db=str(p)))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "running" in out


def test_parity_probe_asks_about_this_databases_collection(tmp_path, monkeypatch):
    """Collections are namespaced per database. Probing the bare "nuggets"
    name reported 0 points against a healthy 1,771-point store and raised a
    mismatch finding for a system that was fine."""
    from jester.cli import collection_for, evaluate_doctor
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db_path = tmp_path / "probe.db"
    db = open_db(str(db_path))
    insert_nugget(db, Nugget(unique_key="k1", platform="reddit", raw_text="x"))

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://qdrant.test:6333")
    asked = {}

    def fake_count(url, collection=None):
        asked["collection"] = collection
        return 1  # matches the single row, so no finding is raised

    monkeypatch.setattr("jester.cli._qdrant_point_count", fake_count)
    findings = evaluate_doctor(db)

    assert asked["collection"] == collection_for(str(db_path)), asked
    assert not [f for f in findings if "parity" in f], findings


def test_parity_mismatch_names_the_collection(tmp_path, monkeypatch):
    """A mismatch must say WHICH store disagrees — with per-database
    namespacing there is more than one."""
    from jester.cli import evaluate_doctor
    from jester.models import Nugget
    from jester.store import insert_nugget, open_db

    db = open_db(str(tmp_path / "probe.db"))
    insert_nugget(db, Nugget(unique_key="k1", platform="reddit", raw_text="x"))
    monkeypatch.setenv("JESTER_QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setattr("jester.cli._qdrant_point_count", lambda url, c=None: 0)

    findings = [f for f in evaluate_doctor(db) if "parity" in f]
    assert findings, "a real mismatch must still be reported"
    assert "nuggets__probe" in findings[0], findings[0]


# ---- a run that never starts never fails -----------------------------------

def _run_at(db, hours_ago, run_id="r1"):
    from datetime import datetime, timedelta, timezone

    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    db.execute("DELETE FROM runs")
    db.execute("INSERT INTO runs (run_id, status, started_at) VALUES (?, ?, ?)",
               (run_id, "completed", ts))
    db.commit()


def test_silence_is_a_finding(tmp_path):
    """Every other check reacts to something going wrong. This one reacts to
    nothing happening, which the notifier cannot see: JESTER_NOTIFY_CMD fires
    when a run fails, and a run that never started never fails.

    That is the exact shape Task Scheduler produces on a laptop — refuses to
    start on battery, never retries a slot missed while asleep. The symptom is
    a quiet gap that looks identical to a quiet week."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 30, "r-gone")
    found = _schedule_silence(db)
    assert found and "no run in 30h" in found[0]
    assert "r-gone" in found[0], "the finding must name the last run"


def test_a_run_that_drifted_an_hour_does_not_cry_wolf(tmp_path):
    """26 hours, not 24, so a run that starts late — or catches up after the
    machine woke — is not reported every morning."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 25, "r-late")
    assert _schedule_silence(db) == []


def test_a_fresh_checkout_is_not_scolded(tmp_path):
    """Never having run is a real state, but it is also what a clean clone
    looks like, and telling someone their schedule is silent before they have
    installed one is noise."""
    from jester.cli import _schedule_silence
    from jester.store import open_db

    assert _schedule_silence(open_db(str(tmp_path / "d.db"))) == []


def test_silence_reaches_the_doctor_findings(tmp_path):
    from jester.cli import evaluate_doctor
    from jester.store import open_db

    db = open_db(str(tmp_path / "d.db"))
    _run_at(db, 40, "r-old")
    res = evaluate_doctor(db)
    findings = res["findings"] if isinstance(res, dict) else res
    assert any("no run in" in str(f) for f in findings)


# ---- V3-08: the WAL note is advice, not a mode change ------------------------

def test_doctor_notes_delete_journal_on_a_local_disk(tmp_path, monkeypatch, capsys):
    """WAL stays opt-in. Doctor may point at it, but must not switch it: the
    note names the variable and the rollback so an operator can decide."""
    import jester.cli as cli

    monkeypatch.setattr(cli, "_on_local_disk", lambda path: True)
    p = tmp_path / "j.db"
    _seed_healthy(open_db(str(p)))

    cli.cmd_doctor(_ns(db=str(p)))

    out = capsys.readouterr().out
    assert "JESTER_SQLITE_JOURNAL_MODE=wal" in out
    assert "delete" in out.lower() and "checkpoint" in out.lower()


def test_doctor_does_not_advise_wal_on_a_network_path(tmp_path, monkeypatch, capsys):
    import jester.cli as cli

    monkeypatch.setattr(cli, "_on_local_disk", lambda path: False)
    p = tmp_path / "j.db"
    _seed_healthy(open_db(str(p)))

    cli.cmd_doctor(_ns(db=str(p)))

    assert "JESTER_SQLITE_JOURNAL_MODE" not in capsys.readouterr().out


def test_doctor_does_not_advise_wal_when_the_archive_is_already_wal(tmp_path, monkeypatch):
    import jester.cli as cli

    monkeypatch.setattr(cli, "_on_local_disk", lambda path: True)
    db = open_db(str(tmp_path / "j.db"))
    db.execute("PRAGMA journal_mode=wal")

    assert cli.journal_note(db) == []


def _mounts(tmp_path, text):
    """Write a /proc/mounts-shaped table: device, mount point, fstype, opts."""
    path = tmp_path / "proc-mounts"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_local_mount_covering_the_path_is_local(tmp_path):
    import jester.cli as cli

    mounts = _mounts(tmp_path, "/dev/sda2 / ext4 rw,relatime 0 0\n")
    assert cli._posix_local_disk("/srv/jester/data/jester.db",
                                 mounts_path=mounts) is True


def test_a_network_mount_is_not_local(tmp_path):
    """Advice on a network mount is how a WAL switch breaks sidecars."""
    import jester.cli as cli

    mounts = _mounts(
        tmp_path,
        "/dev/sda2 / ext4 rw 0 0\n"
        "192.168.1.5:/export /mnt/nas nfs4 rw 0 0\n")
    assert cli._posix_local_disk("/mnt/nas/jester/data/jester.db",
                                 mounts_path=mounts) is False


def test_an_unclassifiable_path_is_not_local(tmp_path):
    """Fix round 1, minor: the docstring promises False when the filesystem
    cannot be identified, but a mount table with no line covering the path
    left `fstype` at its "unknown" sentinel and the function returned True —
    advising a WAL switch on a mount nobody classified."""
    import jester.cli as cli

    mounts = _mounts(tmp_path, "proc /proc proc rw 0 0\nsysfs /sys sysfs rw 0 0\n")
    assert cli._posix_local_disk("/srv/jester/data/jester.db",
                                 mounts_path=mounts) is False


def test_an_empty_mount_table_is_not_local(tmp_path):
    import jester.cli as cli

    mounts = _mounts(tmp_path, "")
    assert cli._posix_local_disk("/srv/jester/data/jester.db",
                                 mounts_path=mounts) is False


def test_a_system_without_proc_mounts_is_not_local(tmp_path):
    """macOS has no /proc/mounts. "Cannot be identified" is False, not a
    guess — the same rule the docstring states."""
    import jester.cli as cli

    missing = str(tmp_path / "no-such-proc-mounts")
    assert cli._posix_local_disk("/srv/jester/data/jester.db",
                                 mounts_path=missing) is False
