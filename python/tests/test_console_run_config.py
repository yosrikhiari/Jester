"""The console's configured run: source selection, goals, honest reporting.

These lock down the contract the pipeline modal posts to `/api/run`. Every one
of them corresponds to something that was silently accepted and dropped: the
modal sent `source_ids` full of the string "undefined", a goal the run never
read, and got back `ok: true` from a run that had archived nothing.
"""
from pathlib import Path

import pytest

from jester.console.api import ConsoleAPI

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def api(tmp_path):
    return ConsoleAPI(db_path=str(tmp_path / "console.db"), config_dir=str(REPO_CONFIG))


# ---- refusals ------------------------------------------------------------
# A goal or a source the run cannot honour must stop the run, not vanish.

def test_unknown_source_is_refused_and_names_every_offender(api):
    r = api.run_pipeline(run_id="x", source_names=["ghost", "phantom"])
    assert r["ok"] is False
    assert "ghost" in r["error"] and "phantom" in r["error"]


def test_unknown_goal_type_is_refused(api):
    r = api.run_pipeline(run_id="x", goal={"type": "bananas", "value": 3})
    assert r["ok"] is False and "bananas" in r["error"]


@pytest.mark.parametrize("value", ["lots", None, "", 3.7j])
def test_goal_without_a_whole_number_is_refused(api, value):
    r = api.run_pipeline(run_id="x", goal={"type": "posts", "value": value})
    assert r["ok"] is False and "whole number" in r["error"]


@pytest.mark.parametrize("value", [0, -1, -100])
def test_goal_below_one_is_refused(api, value):
    r = api.run_pipeline(run_id="x", goal={"type": "ideas", "value": value})
    assert r["ok"] is False and "at least 1" in r["error"]


def test_goal_type_none_is_not_a_goal(api):
    """The select's default option posts type "none"; that is "no ceiling",
    not an unknown type to reject."""
    r = api.run_pipeline(run_id="x", goal={"type": "none"}, ingest=False)
    assert r["ok"] is True


def test_blank_source_names_do_not_trigger_a_fetch(api):
    """Whitespace-only entries are dropped rather than sent to the worker,
    where they would be a hard "no such source" error."""
    r = api.run_pipeline(run_id="x", source_names=["  ", ""], ingest=False)
    assert r["ok"] is True
    assert r["steps"] == []


# ---- honest reporting ----------------------------------------------------

def test_run_that_archives_nothing_reports_idle_not_success(api):
    """The failure that made the console feel broken: the fixture corpus is
    consumed on the first run and the skip-list rejects it forever after, so
    every later press completed in under a second having archived nothing —
    and said "ok"."""
    # Seed EXPLICITLY. `cmd_run` no longer conjures the fixture corpus on an
    # empty queue outside mock mode — a live cycle that fetched nothing used to
    # archive the canned sample as if it had scraped it.
    assert api.seed_mock()["queued_new_comments"] > 0
    first = api.run_pipeline(run_id="first", ingest=False)
    assert first["ok"] is True
    assert first["archived"]["nuggets"] > 0, "the seeded corpus should be archived"
    assert not first.get("idle")

    second = api.run_pipeline(run_id="second", ingest=False)
    assert second["ok"] is True          # it did complete
    assert second["idle"] is True        # but it did nothing
    assert second["archived"] == {"nuggets": 0, "ideas": 0}
    assert "archived nothing new" in second["note"]


def test_archived_counts_are_deltas_not_totals(api):
    api.seed_mock()
    api.run_pipeline(run_id="first", ingest=False)
    before = api._counts()["nuggets"]
    second = api.run_pipeline(run_id="second", ingest=False)
    assert before > 0
    # A total would report `before`; the delta reports what THIS run added.
    assert second["archived"]["nuggets"] == 0


# ---- the ingest argv the goals compile down to ---------------------------

def _argv(api, monkeypatch, **kw):
    """Capture the worker command line without running Go."""
    seen = {}

    class Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Proc()

    monkeypatch.setattr("jester.console.api.subprocess.run", fake_run)
    monkeypatch.setattr(ConsoleAPI, "_go_binary", lambda self: "go")
    api.ingest(**kw)
    return seen["cmd"]


def test_selected_sources_become_one_only_flag(api, monkeypatch):
    cmd = _argv(api, monkeypatch, only=["hn-ask", "devops"])
    assert "-only" in cmd
    assert cmd[cmd.index("-only") + 1] == "hn-ask,devops"


def test_blank_names_never_reach_the_only_flag(api, monkeypatch):
    cmd = _argv(api, monkeypatch, only=["  ", "", "devops"])
    assert cmd[cmd.index("-only") + 1] == "devops"


def test_no_selection_means_no_only_flag(api, monkeypatch):
    """An empty selection is "walk every enabled source", which the worker
    spells as the absence of the flag — not as an empty string, which would
    be indistinguishable from a selection that matched nothing."""
    assert "-only" not in _argv(api, monkeypatch, only=[])
    assert "-only" not in _argv(api, monkeypatch)


def test_comment_and_post_goals_become_worker_ceilings(api, monkeypatch):
    cmd = _argv(api, monkeypatch, max_comments=50)
    assert cmd[cmd.index("-max-comments") + 1] == "50"
    assert "-max-posts" not in cmd

    cmd = _argv(api, monkeypatch, max_posts=2)
    assert cmd[cmd.index("-max-posts") + 1] == "2"
    assert "-max-comments" not in cmd


def test_worker_output_is_decoded_as_utf8_not_the_locale_codec(api, monkeypatch):
    """The worker echoes scraped comment text. With subprocess's `text=True`
    the Windows locale codec raises inside the reader thread, subprocess hands
    back stdout=None, and the console loses the worker's whole account of the
    run — which read as a clean run that happened to queue nothing."""
    seen = {}

    class Proc:
        returncode, stdout, stderr = 0, "queued 3 new comment(s)", ""

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return Proc()

    monkeypatch.setattr("jester.console.api.subprocess.run", fake_run)
    monkeypatch.setattr(ConsoleAPI, "_go_binary", lambda self: "go")
    api.ingest()
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"
    assert "text" not in seen, "text=True would re-introduce the locale codec"


def test_ingest_failure_stops_the_run_before_the_pipeline(api, monkeypatch):
    """A failed fetch followed by a pipeline pass over an empty queue would
    report `ok` for a run that fetched nothing."""
    class Proc:
        returncode, stdout, stderr = 1, "", "cdp session refused"

    monkeypatch.setattr("jester.console.api.subprocess.run", lambda cmd, **kw: Proc())
    monkeypatch.setattr(ConsoleAPI, "_go_binary", lambda self: "go")
    r = api.run_pipeline(run_id="x", source_names=["hn-ask"])
    assert r["ok"] is False
    assert "ingest failed" in r["error"]
    assert r["steps"] and r["steps"][0]["step"] == "ingest"


def test_selection_without_a_worker_is_refused_rather_than_ignored(api, monkeypatch):
    """Without Go there is no way to honour "fetch these sources", and running
    the pipeline half anyway would look like the selection had been applied."""
    monkeypatch.setattr(ConsoleAPI, "_go_binary", lambda self: None)
    r = api.run_pipeline(run_id="x", source_names=["hn-ask"])
    assert r["ok"] is False and "Go worker" in r["error"]


# ---- the schedule's scrape scope -----------------------------------------
# The Schedule page could pick a cadence and nothing else, so an unattended
# tick always meant "every enabled source, no ceiling".

def test_schedule_scope_accepts_a_real_selection(api):
    scope, err = api._scrape_scope({"only": ["hn-ask"], "max_posts": 3})
    assert err is None and scope == {"only": ["hn-ask"], "max_posts": 3}


def test_schedule_scope_refuses_an_unknown_source(api):
    """Same rule as a manual run: a name the worker cannot resolve would make
    the whole tick fail, and on a timer nobody would be watching."""
    scope, err = api._scrape_scope({"only": ["ghost"]})
    assert scope is None and "ghost" in err


@pytest.mark.parametrize("bad", ["abc", "3.5.1", [], {}])
def test_schedule_scope_refuses_a_non_numeric_ceiling(api, bad):
    scope, err = api._scrape_scope({"max_posts": bad})
    assert scope is None and "whole number" in err


@pytest.mark.parametrize("key,bad", [
    ("max_posts", 9999), ("max_posts", -1),
    ("max_comments", 10 ** 9), ("max_ideas", 5000),
])
def test_schedule_scope_enforces_its_ranges(api, key, bad):
    scope, err = api._scrape_scope({key: bad})
    assert scope is None and "between" in err


def test_schedule_scope_treats_absent_and_zero_as_no_ceiling(api):
    """A ceiling of zero is a tick that fetches nothing; the form's empty
    field must not become one."""
    for value in (None, "", 0, "0"):
        scope, err = api._scrape_scope({"max_posts": value})
        assert err is None and "max_posts" not in scope


def test_empty_selection_means_every_enabled_source(api):
    """Posting an empty list keeps the schedule following the Sources tab
    instead of freezing today's names into the launcher."""
    scope, err = api._scrape_scope({"only": []})
    assert err is None and scope["only"] == []


def test_schedule_install_refuses_a_bad_scope_before_touching_the_scheduler(api, monkeypatch):
    called = []
    monkeypatch.setattr("jester.console.api._schedule.install",
                        lambda **kw: called.append(kw) or {"ok": True, "detail": "", "action": "create"})
    res = api.schedule_install(every=30, options={"only": ["ghost"]})
    assert res["ok"] is False
    assert not called, "a refused scope must not reach the host scheduler"
