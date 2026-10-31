"""`jester evidence --run ID` produces a folder a stranger can follow (S-09).

The folder is checked the way a reader checks it: every file the README links
exists, and every citation in the pack resolves to a nugget in the archive.
"""
import json
from pathlib import Path

import pytest

from jester import cli, evidence, provenance
from jester.store import open_db

RUN = "run-2026-10-31T09:00:00Z"


@pytest.fixture
def archive(tmp_path):
    db = open_db(str(tmp_path / "jester.db"))
    db.execute(
        "INSERT INTO runs (run_id, status, started_at, finished_at, models_used, env)"
        " VALUES (?, 'completed', '2026-10-31 09:00:00', '2026-10-31 09:30:00', ?, ?)",
        (RUN,
         json.dumps({"models": ["m-extract", "m-synth", "m-critic"],
                     "roles": {"extractor": {"model": "m-extract", "digest": "abc"}},
                     "prompt_versions": {"extractor": 2}}),
         json.dumps({"python": "3.12", "machine": "test-box"})))
    db.execute(
        "INSERT INTO nuggets (unique_key, platform, extracted_insight, source_url,"
        " comment_url, extractor_model, run_id)"
        " VALUES ('k1', 'reddit', 'CI is slow', 'https://reddit.com/r/x',"
        " 'https://reddit.com/r/x/1', 'm-extract', ?)", (RUN,))
    db.execute(
        "INSERT INTO ideas (title, problem_statement, proposed_solution,"
        " supporting_nuggets, overall, status, run_id)"
        " VALUES ('Fast CI', 'CI is slow', 'Cache the build', ?, 7.5, 'new', ?)",
        (json.dumps(["k1"]), RUN))
    provenance.record_call(db, RUN, role="extractor", model="m-extract",
                           prompt="system\n\nbody", response='{"insight": "CI is slow"}',
                           latency_ms=12)
    db.commit()
    return db


def test_the_pack_writes_every_file_the_readme_advertises(archive, tmp_path):
    summary = evidence.write_pack(archive, RUN, tmp_path / "pack")
    out = Path(summary["out"])

    for name in ("README.md", "run.json", "calls.json", "ideas.json",
                 "nuggets.json", "sources.json", "citations.json",
                 "integrity.json"):
        assert (out / name).is_file(), name

    readme = (out / "README.md").read_text(encoding="utf-8")
    for name in ("calls.json", "run.json", "ideas.json", "sources.json",
                 "integrity.json"):
        assert name in readme
    assert "Citation integrity: PASS" in readme
    assert summary["n_calls"] == 1
    assert summary["n_ideas"] == 1
    assert summary["n_nuggets"] == 1
    assert summary["n_sources"] == 1
    assert summary["integrity_ok"] is True


def test_the_calls_file_carries_the_hashes_and_no_text_by_default(archive, tmp_path):
    evidence.write_pack(archive, RUN, tmp_path / "pack")
    calls = json.loads((tmp_path / "pack" / "calls.json").read_text())
    assert len(calls) == 1
    assert calls[0]["role"] == "extractor"
    assert len(calls[0]["prompt_sha256"]) == 64
    assert calls[0]["prompt_text"] is None


def test_a_broken_citation_fails_the_pack(archive, tmp_path):
    archive.execute("UPDATE ideas SET supporting_nuggets=? WHERE run_id=?",
                    (json.dumps(["k1", "gone-key"]), RUN))
    archive.commit()

    summary = evidence.write_pack(archive, RUN, tmp_path / "pack")

    assert summary["integrity_ok"] is False
    integrity = json.loads((tmp_path / "pack" / "integrity.json").read_text())
    assert integrity["missing"] == [{"idea_id": 1, "nugget_key": "gone-key"}]
    readme = (tmp_path / "pack" / "README.md").read_text(encoding="utf-8")
    assert "does not pass" in readme
    assert "gone-key" in readme


def test_an_unknown_run_names_the_problem(archive, tmp_path):
    with pytest.raises(LookupError, match="no run 'nope'"):
        evidence.write_pack(archive, "nope", tmp_path / "pack")


def test_the_cli_writes_the_pack_and_exits_zero(archive, tmp_path, capsys):
    db_path = tmp_path / "jester.db"
    out = tmp_path / "pack"
    cli.main(["evidence", "--run", RUN, "--db", str(db_path), "--out", str(out)])
    printed = capsys.readouterr().out
    assert "evidence pack:" in printed
    assert "citation integrity: PASS" in printed
    assert (out / "README.md").is_file()


def test_the_cli_exits_one_when_integrity_fails(archive, tmp_path, capsys):
    archive.execute("UPDATE ideas SET supporting_nuggets=? WHERE run_id=?",
                    (json.dumps(["gone-key"]), RUN))
    archive.commit()
    with pytest.raises(SystemExit) as exc:
        cli.main(["evidence", "--run", RUN, "--db", str(tmp_path / "jester.db"),
                  "--out", str(tmp_path / "pack")])
    assert exc.value.code == 1
    assert "citation integrity: FAIL" in capsys.readouterr().out


def test_a_report_does_not_migrate_the_archive_it_reads(tmp_path, capsys):
    # The live archive is deliberately un-migrated (S-05), and `evidence` is a
    # report: opening it with migrate=True would create llm_calls and apply the
    # ALTERs — a read command reshaping the owner's archive. It must say the
    # provenance table is absent rather than silently reporting zero calls.
    path = tmp_path / "old.db"
    db = open_db(str(path))
    db.execute(
        "INSERT INTO runs (run_id, status, started_at) VALUES (?, 'completed',"
        " '2026-10-31 09:00:00')", (RUN,))
    db.execute("DROP TABLE llm_calls")
    db.commit()
    db.close()

    cli.main(["evidence", "--run", RUN, "--db", str(path), "--out", str(tmp_path / "pack")])

    check = open_db(str(path), migrate=False)
    tables = {r[0] for r in check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check.close()
    assert "llm_calls" not in tables, "evidence must not create the table"
    readme = (tmp_path / "pack" / "README.md").read_text(encoding="utf-8")
    assert "not migrated" in readme
    assert "call provenance unavailable" in readme


def test_the_cli_refuses_a_missing_archive(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["evidence", "--run", RUN, "--db", str(tmp_path / "absent.db")])
    assert exc.value.code == 2
    assert "no archive" in capsys.readouterr().out
    assert not (tmp_path / "absent.db").exists(), "must not create an empty archive"
