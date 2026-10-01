"""`jester journal ...` end to end, with a real git repository in tmp.

The one thing these tests guard most closely: article files are committed to
the journal's OWN repository. The default root sits inside Jester's checkout
(under the ignored `data/`), so "the repository this folder is in" would be
Jester's -- `test_root_inside_another_repo_gets_its_own` proves the commit
never lands there.
"""
import subprocess

import pytest

from jester import cli
from jester import journal as J
from jester.journal import files as jfiles


@pytest.fixture
def run(tmp_path, capsys):
    db, root = str(tmp_path / "journal.db"), str(tmp_path / "articles")

    def _run(*argv, ok=True):
        args = ["journal", argv[0], *argv[1:], "--db", db, "--root", root]
        if ok:
            cli.main(args)
        else:
            with pytest.raises(SystemExit) as exc:
                cli.main(args)
            assert exc.value.code == 1
        return capsys.readouterr().out

    _run.db, _run.root = db, root
    return _run


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_new_creates_the_file_and_commits_version_one(run, tmp_path):
    out = run("new", "Rules vs a decision model", "--question", "Which sorts best?")
    assert "created rules-vs-a-decision-model (article, captured)" in out
    path = tmp_path / "articles" / "rules-vs-a-decision-model" / "index.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Rules vs a decision model") and "## Reproduce it" in text
    assert (tmp_path / "articles" / ".git").exists()
    assert _git(tmp_path / "articles", "rev-list", "--count", "HEAD") == "1"
    db = J.open_journal(run.db)
    j = J.get(db, "rules-vs-a-decision-model")
    rev = J.latest_revision(db, j["id"])
    assert (j["question"], rev["n"]) == ("Which sorts best?", 1)
    assert rev["git_sha"] == _git(tmp_path / "articles", "rev-parse", "HEAD")
    db.close()


def test_a_til_gets_a_short_skeleton(run, tmp_path):
    run("new", "TIL busy timeout", "--kind", "til", "--slug", "til-busy")
    text = (tmp_path / "articles" / "til-busy" / "index.md").read_text(encoding="utf-8")
    assert "## Setup" not in text and "What happened" in text


def test_duplicates_and_unknown_slugs_are_refused(run):
    run("new", "Same title")
    assert "already exists" in run("new", "Same title", ok=False)
    assert "no journal called 'nope'" in run("show", "nope", ok=False)


def test_show_names_what_blocks_the_next_step_and_state_refuses(run):
    run("new", "Blocked")
    out = run("show", "blocked")
    assert "blocked · article · captured" in out
    assert "next: proposed, blocked by:" in out and "write the question" in out
    assert "(saved)" in out and "versions: 1" in out
    out = run("state", "blocked", "proposed", ok=False)
    assert "blocked cannot move to proposed:" in out and "write the question" in out


def test_walk_to_chosen_through_the_cli(run):
    run("new", "Walk")
    assert "nothing to set" in run("set", "walk", ok=False)
    run("set", "walk", "--question", "Does it hold?", "--metric", "F1",
        "--baseline", "rules", "--prior-coverage", "only vendor numbers")
    assert "walk is now proposed" in run("state", "walk", "proposed")
    assert "recorded your approval: walk choose" in run("approve", "walk", "choose")
    assert "walk is now chosen" in run("state", "walk", "chosen")
    out = run("show", "walk")
    assert "approved choose by human" in out
    assert "captured -> proposed by human" in out and "proposed -> chosen by human" in out
    assert "next: researching is open -> jester journal state walk researching" in out


def test_save_commits_only_real_changes(run, tmp_path):
    run("new", "Saving")
    root = tmp_path / "articles"
    assert "no changes since version 1" in run("save", "saving")
    path = root / "saving" / "index.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nFirst real paragraph.\n", encoding="utf-8")
    assert "(unsaved changes)" in run("show", "saving")
    assert "unsaved changes; save first" in run("approve", "saving", "review", ok=False)
    out = run("save", "saving", "-m", "first paragraph")
    assert "saved saving version 2" in out
    assert _git(root, "rev-list", "--count", "HEAD") == "2"
    assert "first paragraph" in _git(root, "log", "-1", "--format=%s")
    assert "recorded your approval: saving review on version 2" in run("approve", "saving", "review")


def test_save_refuses_a_missing_file(run, tmp_path):
    run("new", "Gone")
    (tmp_path / "articles" / "gone" / "index.md").unlink()
    assert "article file is missing" in run("save", "gone", ok=False)
    assert "(missing)" in run("show", "gone")


def test_post_approvals_wait_for_publishing(run):
    run("new", "Post")
    assert "publishing arrives in slice J3" in run("approve", "post", "post", ok=False)


def test_list(run):
    assert "no journals yet" in run("list")
    run("new", "One")
    run("new", "Two", "--kind", "note", "--question", "q")
    out = run("list")
    assert "one" in out and "-> proposed: 1 thing(s) to do" in out
    assert "-> proposed: open" in out and "2 captured" in out
    assert "two" not in run("list", "--state", "drafting")


def test_list_shows_parked_and_finished(run):
    run("new", "Paused", "--kind", "note", "--question", "q")
    run("state", "paused", "parked", "--note", "later")
    out = run("list")
    assert "-> captured: open" in out
    run("state", "paused", "abandoned", "--reason", "superseded")
    assert "done" in run("list")
    out = run("show", "paused")
    assert "abandoned: superseded" in out and "nothing, this journal is finished" in out


def test_show_lists_experiments_checks_and_agent_approvals(run):
    run("new", "Detail")
    db = J.open_journal(run.db)
    j = J.get(db, "detail")
    e = J.add_experiment(db, j["id"], n_planned=3)
    J.add_run(db, e, raw_path="r.jsonl")
    J.record_check(db, J.latest_revision(db, j["id"])["id"], "lint", False)
    J.approve(db, j["id"], "choose", actor="angle-proposer")
    db.close()
    out = run("show", "detail")
    assert "experiment 1: 1/3 run(s), locked" in out
    assert "checks on latest: lint FAIL" in out
    assert "not counted: not a human decision" in out


def test_root_inside_another_repo_gets_its_own(tmp_path, capsys):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    root = outer / "data" / "journal"
    cli.main(["journal", "new", "Nested", "--db", str(tmp_path / "j.db"), "--root", str(root)])
    capsys.readouterr()
    assert (root / ".git").exists()
    assert _git(root, "rev-list", "--count", "HEAD") == "1"
    assert subprocess.run(["git", "-C", str(outer), "rev-parse", "HEAD"],
                          capture_output=True).returncode != 0


def test_commit_works_with_no_git_identity(tmp_path, monkeypatch):
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "bare"
    path = jfiles.create_article(root, "x", "X", "note")
    jfiles.commit(root, path, "x: start")
    assert _git(root, "log", "-1", "--format=%an") == "Jester journal"
    with pytest.raises(jfiles.GitError, match="already exists"):
        jfiles.create_article(root, "x", "X", "note")


def test_git_errors_are_reported(tmp_path):
    with pytest.raises(jfiles.GitError, match="rev-parse"):
        jfiles._git(tmp_path, "rev-parse", "--verify", "no-such-ref")
