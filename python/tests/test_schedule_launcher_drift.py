"""Stale launchers are found, reported and rewritten (2026-09-29).

On 28 Sep the hiring task's launcher had been written before the job-board
fallback and the Reddit step existed. The 09:30 run read one thread and
stopped, exit 0, for days -- while the console's run-now button, which builds
its command fresh, did the whole chain. A launcher is a snapshot of the code
that wrote it, and nothing compared the two.

Every test writes launchers under a temporary repo root, never this machine's.
"""
from pathlib import Path

import pytest

from jester import schedule

PY = r"C:\Python312\python.exe"


@pytest.fixture(autouse=True)
def _keep_env():
    """cli.main loads .env into os.environ. Left there, JESTER_QDRANT_URL
    turned later doctor tests into checks against a real Qdrant."""
    import os
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)
    # Installed unless a test says otherwise; never asks the real scheduler.
    monkeypatch.setattr(schedule, "status",
                        lambda task=None: {"installed": True, "scheduler": "schtasks"})


def _age(command, old_argv):
    """Make an installed launcher look like older code wrote it."""
    path = schedule.launcher_path(command)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(schedule.argv_text(command), old_argv), encoding="utf-8")


def test_a_launcher_is_read_back_with_its_settings():
    schedule.write_launcher("data/jester.db", "config", PY, command="ingest",
                            extra=["--only", "forhire,techjobs", "--max-posts", "5"])
    got = schedule.read_launcher("ingest")
    assert got["argv"] == "ingest"
    assert got["db"] == "data/jester.db"
    assert got["config"] == "config"
    assert got["python"] == PY
    assert got["extra"] == ["--only", "forhire,techjobs", "--max-posts", "5"]


def test_a_launcher_this_code_wrote_is_not_stale():
    for command in ("signals-hiring", "signals-sweep", "signals-brief"):
        schedule.write_launcher("data/signals-live.db", "config", PY, command=command)
        assert schedule.launcher_drift(command)["stale"] is False, command


def test_the_launcher_that_missed_the_boards_is_caught():
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-hiring")
    _age("signals-hiring", "signals run --source hackernews-hiring --rules "
                           "config/hiring_rules.yaml --query 2 --limit 1000")
    drift = schedule.launcher_drift("signals-hiring")
    assert drift["stale"] is True
    assert "--fallback" not in drift["argv"]
    assert "--fallback himalayas,remotive,jobicy" in drift["want"]


def test_no_launcher_is_not_a_finding():
    assert schedule.launcher_drift("signals-brief") is None
    assert schedule.stale_launchers() == []


def test_a_leftover_file_without_a_task_is_not_reported_by_default(monkeypatch):
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-brief")
    _age("signals-brief", "signals digest")
    monkeypatch.setattr(schedule, "status", lambda task=None: {"installed": False})
    assert schedule.stale_launchers() == []
    assert [d["command"] for d in schedule.stale_launchers(installed_only=False)] == ["signals-brief"]


def test_refresh_rewrites_only_the_stale_one_and_keeps_its_settings():
    schedule.write_launcher("D:/x/signals-live.db", "config", PY, command="signals-hiring")
    schedule.write_launcher("data/jester.db", "config", PY, command="ingest",
                            extra=["--only", "forhire"])
    _age("signals-hiring", "signals run --source hackernews-hiring")
    fresh_before = schedule.launcher_path("ingest").read_bytes()

    done = schedule.refresh_launchers()

    assert [d["command"] for d in done] == ["signals-hiring"]
    assert done[0]["was"] == "signals run --source hackernews-hiring"
    after = schedule.read_launcher("signals-hiring")
    assert after["argv"] == schedule.argv_text("signals-hiring")
    assert after["db"] == "D:/x/signals-live.db", "the operator's database is kept"
    assert after["python"] == PY
    assert schedule.launcher_path("ingest").read_bytes() == fresh_before, "a current one is untouched"
    assert schedule.stale_launchers() == []


def test_a_rewritten_launcher_is_byte_identical_to_a_fresh_install():
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-sweep")
    want = schedule.launcher_path("signals-sweep").read_bytes()
    _age("signals-sweep", "signals run --source himalayas --query developer")
    schedule.refresh_launchers()
    assert schedule.launcher_path("signals-sweep").read_bytes() == want


def test_status_warns_and_says_how_to_fix(capsys):
    from jester import cli
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-hiring")
    _age("signals-hiring", "signals run --source hackernews-hiring")
    cli.main(["schedule", "status", "--command", "signals-hiring"])
    out = capsys.readouterr().out
    assert "WARNING: the launcher runs an old command" in out
    assert "fix   : jester schedule refresh" in out


def test_the_refresh_command_reports_what_it_rewrote(capsys):
    from jester import cli
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-hiring")
    _age("signals-hiring", "signals run --source hackernews-hiring")
    cli.main(["schedule", "refresh"])
    assert "1 launcher(s) rewritten" in capsys.readouterr().out
    cli.main(["schedule", "refresh"])
    assert "every launcher already runs what this code would write" in capsys.readouterr().out


def test_doctor_lists_a_stale_launcher():
    from jester import cli
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-hiring")
    _age("signals-hiring", "signals run --source hackernews-hiring")
    findings = cli.launcher_findings()
    assert len(findings) == 1
    assert "scheduled signals-hiring runs an old command" in findings[0]


def test_the_console_card_shows_it_and_can_fix_it(tmp_path):
    from jester.console.api import ConsoleAPI
    from jester.store import open_db
    main = str(tmp_path / "jester.db")
    open_db(main).close()
    config = Path(__file__).resolve().parents[2] / "config"
    api = ConsoleAPI(main, str(config))
    schedule.write_launcher("data/signals-live.db", "config", PY, command="signals-hiring")
    _age("signals-hiring", "signals run --source hackernews-hiring")

    card = api.jobs_scraper()["launcher"]
    assert card["stale"] is True
    assert card["runs"] == "jester signals run --source hackernews-hiring"

    assert ConsoleAPI(main, str(config), read_only=True).schedule_refresh()["ok"] is False
    res = api.schedule_refresh()
    assert res["ok"] and [r["command"] for r in res["refreshed"]] == ["signals-hiring"]
    assert api.jobs_scraper()["launcher"]["stale"] is False
