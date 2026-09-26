"""`jester signals from-archive`, driven through the CLI.

score_archive is tested directly elsewhere. This covers the command around
it: resolving the room list, opening two DIFFERENT databases (the nuggets
archive it reads and the signals archive it writes), reporting the discard
reasons, and writing the leads file. Around forty statements that only run
when the command runs.

The two-database part is the easy thing to get wrong. --archive is what the
worker fills; --db is the signals archive the verdicts go into. Collapsing
them would mean scoring an archive into itself.
"""
import json
import os
import sqlite3

import pytest

from jester import cli
from jester.store import open_db

CLIENT = ("[Hiring] Senior Backend Engineer ($120-$160/hr) - contract, "
          "remote. We are looking for a Python engineer. Apply: jobs@x.com")
SELLER = "[For Hire] I'm a developer, React and Node, $25/hr, remote"


@pytest.fixture(autouse=True)
def _keep_env():
    """cli.main calls load_env_once, which loads .env into os.environ and
    leaks into every test that runs after it."""
    before = dict(os.environ)
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
        (json.dumps([{"id": "a", "body": CLIENT, "author": "acme"},
                     {"id": "b", "body": SELLER, "author": "dev"}]),))
    db.commit()
    db.close()
    return str(tmp_path / "nuggets.db")


def _run(archive, tmp_path, extra=()):
    cli.main(["signals", "from-archive",
              "--db", str(tmp_path / "signals.db"),
              "--archive", archive,
              "--out", str(tmp_path / "out"),
              "--rules", "config/hiring_rules.yaml", *extra])


def test_it_scores_the_archive_into_the_signals_database(archive, tmp_path, capsys):
    _run(archive, tmp_path)
    out = capsys.readouterr().out
    assert "stored record(s)" in out

    db = sqlite3.connect(str(tmp_path / "signals.db"))
    rows = db.execute("SELECT audience FROM problem_signal").fetchall()
    db.close()
    assert [r[0] for r in rows] == ["buyer"]


def test_the_discards_are_reported_not_silent(archive, tmp_path, capsys):
    """"1 buyer" and "1 buyer after discarding 1 seller advert" describe very
    different archives, and only the second is the truth."""
    _run(archive, tmp_path)
    assert "discarded before scoring" in capsys.readouterr().out


def test_it_writes_the_leads_file(archive, tmp_path, capsys):
    _run(archive, tmp_path)
    assert "lead(s) written" in capsys.readouterr().out


def test_the_nuggets_archive_is_left_untouched(archive, tmp_path):
    """The whole justification for scheduling this against unapproved Reddit
    access is that it reads rather than collects. It must not write to the
    archive it reads either."""
    before = sqlite3.connect(archive).execute(
        "SELECT COUNT(*), SUM(LENGTH(comments)) FROM ingest_batch").fetchone()
    _run(archive, tmp_path)
    after = sqlite3.connect(archive).execute(
        "SELECT COUNT(*), SUM(LENGTH(comments)) FROM ingest_batch").fetchone()
    assert before == after


def test_an_archive_with_nothing_from_those_rooms_says_so(tmp_path, capsys):
    """An empty read and an empty verdict are different results, and only one
    of them is a reason to look at the rules."""
    empty = str(tmp_path / "empty.db")
    open_db(empty).close()
    _run(empty, tmp_path)
    assert "nothing stored from those rooms yet" in capsys.readouterr().out
