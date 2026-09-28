"""`jester signals run --fallback ... --also-reddit-archive`, driven through the CLI.

collect_or_fall_back and score_archive are tested directly elsewhere. This
covers the command around them: validating the fallback names before any
fetch, filing hand-started runs as "manual", appending the Reddit-archive
step as its own ledger row, and the exit code when a run fails.

Collection is faked at `run.collect`, so nothing here touches the network.
"""
import json
import os
import sqlite3

import pytest

from jester import cli
from jester.signals import run as sigrun
from jester.store import open_db

CLIENT = ("[Hiring] Senior Backend Engineer ($120-$160/hr) - contract, "
          "remote. We are looking for a Python engineer. Apply: jobs@x.com")


@pytest.fixture(autouse=True)
def _keep_env(monkeypatch):
    """cli.main loads .env into os.environ; keep it from leaking, and make
    sure no JESTER_ORIGIN from the shell decides the run's kind."""
    before = dict(os.environ)
    monkeypatch.delenv("JESTER_ORIGIN", raising=False)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture
def archive(tmp_path):
    db = open_db(str(tmp_path / "nuggets.db"))
    db.execute(
        "INSERT INTO ingest_batch (platform, source, thread_id, comments, "
        "status, run_id, kind) VALUES ('reddit', "
        "'https://www.reddit.com/r/forhire/', 't1', ?, 'pending', 'r', 'comment')",
        (json.dumps([{"id": "a", "body": CLIENT, "author": "acme"}]),))
    db.commit()
    db.close()
    return str(tmp_path / "nuggets.db")


@pytest.fixture
def fake_collect(monkeypatch):
    """Every source returns a clean run with nothing new, unless told otherwise."""
    calls, results = [], {}

    def collect(db, *, source_name="hackernews", **kw):
        calls.append(source_name)
        collect.queries[source_name] = kw.get("queries")
        row = {"run_id": f"r-{source_name}", "source": source_name, "mode": "live",
               "kind": kw.get("kind", "scheduled"), "started_utc": "t", "finished_utc": "t",
               "since": "7d", "queries": "[]", "collected": 3, "new": 0, "seen_again": 3,
               "edited": 0, "relevant": 0, "buyer": 0, "practitioner": 0, "errors": 0,
               "status": "ok", "note": "", **results.get(source_name, {})}
        sigrun.ensure_run_table(db)   # the real collect() does this first
        sigrun._save_run(db, row)
        return row

    monkeypatch.setattr(sigrun, "collect", collect)
    collect.calls, collect.results, collect.queries = calls, results, {}
    return collect


def _run(tmp_path, *extra):
    cli.main(["signals", "run", "--db", str(tmp_path / "signals.db"),
              "--source", "hackernews-hiring", "--rules", "config/hiring_rules.yaml",
              "--query", "2", *extra])


def _ledger(tmp_path):
    db = sqlite3.connect(str(tmp_path / "signals.db"))
    return {r[0]: (r[1], r[2]) for r in db.execute("SELECT source, kind, note FROM signal_run")}


def test_the_chain_and_the_reddit_step_each_get_a_ledger_row(tmp_path, archive, fake_collect, capsys):
    fake_collect.results["remotive"] = {"new": 4}
    _run(tmp_path, "--fallback", "himalayas,remotive,jobicy",
         "--also-reddit-archive", "--archive", archive)

    assert fake_collect.calls == ["hackernews-hiring", "himalayas", "remotive"]
    ledger = _ledger(tmp_path)
    assert set(ledger) == {"hackernews-hiring", "himalayas", "remotive", "reddit-archive"}
    # Started from a prompt, not the scheduler's launcher.
    assert {kind for kind, _ in ledger.values()} == {"manual"}
    assert "no requests to Reddit" in ledger["reddit-archive"][1]
    out = capsys.readouterr().out
    assert "fell back to himalayas" in out
    assert "reddit-archive" in out


def test_the_launcher_origin_is_recorded_as_scheduled(tmp_path, fake_collect, monkeypatch):
    monkeypatch.setenv("JESTER_ORIGIN", "scheduled")
    _run(tmp_path)
    assert _ledger(tmp_path)["hackernews-hiring"][0] == "scheduled"


def test_an_unknown_fallback_is_refused_before_anything_is_fetched(tmp_path, fake_collect, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, "--fallback", "himalayas,nosuchboard")
    assert exc.value.code == 1
    assert fake_collect.calls == []
    assert "nosuchboard" in capsys.readouterr().out


def test_a_failed_run_exits_non_zero(tmp_path, fake_collect):
    fake_collect.results["hackernews-hiring"] = {"status": "failed", "errors": 1}
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, "--fallback", "himalayas")
    assert exc.value.code == 1
    # A failure is not "nothing new": the chain stops at the first source.
    assert fake_collect.calls == ["hackernews-hiring"]


def test_a_board_run_directly_searches_its_own_terms(tmp_path, fake_collect):
    """It used to search the hiring rules' queries ("12", twelve months to
    Hacker News), which is a meaningless keyword to a job board."""
    cli.main(["signals", "run", "--db", str(tmp_path / "signals.db"),
              "--source", "himalayas", "--rules", "config/hiring_rules.yaml"])
    assert fake_collect.queries["himalayas"] == ["developer", "engineer", "automation", "data"]


def test_explicit_queries_still_win(tmp_path, fake_collect):
    cli.main(["signals", "run", "--db", str(tmp_path / "signals.db"),
              "--source", "himalayas", "--rules", "config/hiring_rules.yaml", "--query", "golang"])
    assert fake_collect.queries["himalayas"] == ["golang"]
