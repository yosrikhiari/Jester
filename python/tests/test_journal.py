"""The journal store and its state machine.

The J0 promise is "a journal can move through every state, and every step
forward is refused until its gate is met". So the main test walks one article
from captured to measured, and at each step first proves the move is blocked
and says why, then satisfies the gate and moves.

Later slices (proof checkers J1, experiments J2, publishing J3, metrics J8)
will write the rows these gates read. Here the tests write them directly,
which is exactly the contract those slices must meet.
"""
from datetime import datetime, timedelta, timezone

import pytest

from jester import journal as J
from jester.journal import states as js

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
FULL_CHECKLIST = {k: "answered" for k, _ in J.CHECKLIST}


@pytest.fixture
def db(tmp_path):
    conn = J.open_journal(str(tmp_path / "nested" / "journal.db"))
    yield conn
    conn.close()


def _blocked(db, slug, to, **kw):
    with pytest.raises(js.MoveRefused) as exc:
        js.move(db, slug, to, **kw)
    return " | ".join(exc.value.reasons)


def _rev(db, j, sha="c1", content="h1"):
    return J.record_revision(db, j["id"], sha, content, "x/index.md")


def _pass_checks(db, rev_id):
    for kind in J.REQUIRED_CHECKS:
        J.record_check(db, rev_id, kind, True)


# ---- store ------------------------------------------------------------------

def test_open_creates_every_table_and_the_folder(db, tmp_path):
    assert (tmp_path / "nested" / "journal.db").exists()
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(J.TABLES) <= names


def test_slugify():
    assert J.slugify("Rules vs an 8B LLM: 500 posts!") == "rules-vs-an-8b-llm-500-posts"
    assert J.slugify("x" * 80) == "x" * 60
    assert J.slugify("???") == ""


def test_create_refuses_bad_input(db):
    J.create_journal(db, "First one")
    with pytest.raises(J.JournalError, match="already exists"):
        J.create_journal(db, "First one")
    with pytest.raises(J.JournalError, match="kind must be"):
        J.create_journal(db, "Other", kind="blogpost")
    with pytest.raises(J.JournalError, match="needs a title"):
        J.create_journal(db, "   ")
    with pytest.raises(J.JournalError, match="cannot make a slug"):
        J.create_journal(db, "???")
    with pytest.raises(J.JournalError, match="no journal"):
        J.get(db, "missing")


def test_new_journal_starts_captured(db):
    j = J.create_journal(db, "A question", question="  Does X beat Y?  ")
    assert (j["state"], j["kind"], j["question"]) == ("captured", "article", "Does X beat Y?")
    assert [r["slug"] for r in J.list_journals(db)] == ["a-question"]
    assert J.list_journals(db, state="drafting") == []


def test_update_brief_only_touches_brief_fields(db):
    J.create_journal(db, "Brief")
    j = J.update_brief(db, "brief", metric=" accuracy ", baseline=None)
    assert j["metric"] == "accuracy" and j["baseline"] == ""
    with pytest.raises(J.JournalError, match="not a brief field"):
        J.update_brief(db, "brief", state="published")
    with pytest.raises(J.JournalError, match="cannot be blank"):
        J.update_brief(db, "brief", title=" ")
    assert J.update_brief(db, "brief")["metric"] == "accuracy"


def test_checks_use_the_newest_result_per_kind(db):
    j = J.create_journal(db, "Checks")
    rev = _rev(db, j)
    J.record_check(db, rev["id"], "lint", False)
    J.record_check(db, rev["id"], "lint", True)
    J.record_check(db, rev["id"], "numbers", True)
    J.record_check(db, rev["id"], "numbers", False)
    assert J.latest_checks(db, rev["id"]) == {"lint": True, "numbers": False}
    with pytest.raises(J.JournalError, match="check must be"):
        J.record_check(db, rev["id"], "vibes", True)


def test_first_run_locks_the_plan(db):
    j = J.create_journal(db, "Lock")
    e = J.add_experiment(db, j["id"], hypothesis="h")
    assert J.experiments(db, j["id"])[0]["locked_at"] == ""
    J.add_run(db, e, started_at="2026-10-01T10:00:00+00:00")
    J.add_run(db, e, started_at="2026-10-02T10:00:00+00:00")
    assert J.experiments(db, j["id"])[0]["locked_at"] == "2026-10-01T10:00:00+00:00"


def test_checklist_answers_merge_and_reject_unknown_questions(db):
    j = J.create_journal(db, "List")
    e = J.add_experiment(db, j["id"])
    J.answer_checklist(db, e, {"tuned": "yes, both with defaults"})
    J.answer_checklist(db, e, {"errors": "  "})
    missing = J.missing_answers(J.experiments(db, j["id"])[0]["checklist"])
    assert "tuned" not in missing and "errors" in missing
    with pytest.raises(J.JournalError, match="not a checklist question"):
        J.answer_checklist(db, e, {"vibes": "good"})
    with pytest.raises(J.JournalError, match="no experiment"):
        J.answer_checklist(db, 999, {"tuned": "x"})


def test_approve_rejects_unknown_decisions(db):
    j = J.create_journal(db, "Approve")
    with pytest.raises(J.JournalError, match="approval must be"):
        J.approve(db, j["id"], "vibe-check")


# ---- the whole walk ---------------------------------------------------------

def test_an_article_walks_every_state_and_each_gate_blocks_first(db):
    slug = J.create_journal(db, "Rules vs a decision model")["slug"]
    jid = J.get(db, slug)["id"]

    assert "write the question" in _blocked(db, slug, "proposed")
    J.update_brief(db, slug, question="Which sorts hiring posts best?")
    js.move(db, slug, "proposed")

    reasons = _blocked(db, slug, "chosen")
    for want in ("metric", "baseline", "already been written", "not chosen it"):
        assert want in reasons
    J.update_brief(db, slug, metric="macro F1 and ECE", baseline="the rule set",
                   prior_coverage="vendor posts on IMDb only; no domain data")
    J.approve(db, jid, "choose")
    js.move(db, slug, "chosen")
    js.move(db, slug, "researching")

    reasons = _blocked(db, slug, "experimenting")
    assert "experiment plan first" in reasons and "not approved the experiment plan" in reasons
    e = J.add_experiment(db, jid, hypothesis="rules lose on recall", metric="macro F1")
    reasons = _blocked(db, slug, "experimenting")
    assert "threshold" in reasons and "planned runs" in reasons
    db.execute("UPDATE experiment SET threshold='+0.05 F1', baseline='rules', n_planned=5 "
               "WHERE id=?", (e,))
    J.approve(db, jid, "plan")
    js.move(db, slug, "experimenting")

    reasons = _blocked(db, slug, "drafting")
    assert "no finished run" in reasons and "checklist unanswered" in reasons
    J.add_run(db, e, status="failed")
    assert "no finished run" in _blocked(db, slug, "drafting")
    J.add_run(db, e, status="ok", raw_path="runs/1.jsonl")
    J.answer_checklist(db, e, FULL_CHECKLIST)
    js.move(db, slug, "drafting")

    assert "no saved version" in _blocked(db, slug, "in_review")
    rev = _rev(db, J.get(db, slug))
    reasons = _blocked(db, slug, "in_review")
    assert all(f"no {k} check" in reasons for k in J.REQUIRED_CHECKS)
    _pass_checks(db, rev["id"])
    J.record_check(db, rev["id"], "numbers", False)
    assert "fails the numbers check" in _blocked(db, slug, "in_review")
    J.record_check(db, rev["id"], "numbers", True)
    js.move(db, slug, "in_review")

    assert "not approved version 1" in _blocked(db, slug, "ready")
    J.approve(db, jid, "review", revision_id=rev["id"])
    js.move(db, slug, "ready")

    reasons = _blocked(db, slug, "published")
    for want in ("approved publishing", "where the original lives", "how AI was used"):
        assert want in reasons
    J.approve(db, jid, "publish", revision_id=rev["id"])
    J.update_brief(db, slug, canonical_url="https://example.dev/journal/rules",
                   disclosure="hand-written; AI proofread")
    j = js.move(db, slug, "published", at="2026-10-05T12:00:00+00:00")
    assert j["published_at"] == "2026-10-05T12:00:00+00:00"

    assert "nothing has been posted" in _blocked(db, slug, "shared")
    pub = J.add_publication(db, jid, rev["id"], "devto", status="posted",
                            url="https://dev.to/x", posted_at=T0.isoformat())
    assert "devto post has no approval" in _blocked(db, slug, "shared")
    J.approve(db, jid, "post", publication_id=pub)
    js.move(db, slug, "shared")

    reasons = _blocked(db, slug, "measured")
    assert "7 days" in reasons and "30 days" in reasons
    J.add_metric(db, pub, (T0 + timedelta(days=7)).isoformat(), views=120)
    assert "30 days" in _blocked(db, slug, "measured")
    J.add_metric(db, pub, (T0 + timedelta(days=30)).isoformat(), views=410)
    js.move(db, slug, "measured")

    j = J.get(db, slug)
    assert j["state"] == "measured"
    assert js.next_step(db, j) == (None, [])
    walked = [h["to_state"] for h in J.history(db, jid)]
    assert walked == list(js.FORWARD[1:])
    assert all(h["actor"] == J.HUMAN for h in J.history(db, jid))


def test_a_til_skips_the_experiment(db):
    slug = J.create_journal(db, "TIL sqlite busy timeout", kind="til",
                            question="Why did writes fail?")["slug"]
    js.move(db, slug, "proposed")
    assert "not chosen it" in _blocked(db, slug, "chosen")
    J.approve(db, J.get(db, slug)["id"], "choose")
    js.move(db, slug, "chosen")
    js.move(db, slug, "researching")
    assert js.next_state(J.get(db, slug)) == "drafting"
    assert "only move forward to drafting" in _blocked(db, slug, "experimenting")
    js.move(db, slug, "drafting")
    assert "no experiment step" in _blocked(db, slug, "experimenting")


# ---- the rules that keep it honest ------------------------------------------

def _to_drafting(db, kind="note"):
    slug = J.create_journal(db, f"A {kind}", kind=kind, question="q")["slug"]
    jid = J.get(db, slug)["id"]
    J.approve(db, jid, "choose")
    for s in ("proposed", "chosen", "researching", "drafting"):
        js.move(db, slug, s)
    return slug, jid


def test_an_agent_approval_is_recorded_but_never_counted(db):
    slug = J.create_journal(db, "Agent", kind="til", question="q")["slug"]
    js.move(db, slug, "proposed")
    J.approve(db, J.get(db, slug)["id"], "choose", actor="angle-proposer")
    assert "not chosen it" in _blocked(db, slug, "chosen")


def test_a_new_version_needs_a_new_review(db):
    slug, jid = _to_drafting(db)
    r1 = _rev(db, J.get(db, slug), "c1", "h1")
    _pass_checks(db, r1["id"])
    js.move(db, slug, "in_review")
    J.approve(db, jid, "review", revision_id=r1["id"])
    _rev(db, J.get(db, slug), "c2", "h2")
    assert "not approved version 2" in _blocked(db, slug, "ready")


def test_unsaved_changes_block_review_and_ready(db):
    slug, jid = _to_drafting(db)
    r1 = _rev(db, J.get(db, slug), "c1", "h1")
    _pass_checks(db, r1["id"])
    assert "not saved" in _blocked(db, slug, "in_review", file_sha256="edited")
    js.move(db, slug, "in_review", file_sha256="h1")
    J.approve(db, jid, "review", revision_id=r1["id"])
    assert "not saved" in _blocked(db, slug, "ready", file_sha256="edited")
    js.move(db, slug, "ready", file_sha256="h1")


def test_checks_on_an_older_version_do_not_count(db):
    slug, _ = _to_drafting(db)
    r1 = _rev(db, J.get(db, slug), "c1", "h1")
    _pass_checks(db, r1["id"])
    _rev(db, J.get(db, slug), "c2", "h2")
    assert "version 2 has no citations check" in _blocked(db, slug, "in_review")


def test_park_and_resume(db):
    slug, _ = _to_drafting(db)
    j = js.move(db, slug, "parked", note="waiting for GPU time")
    assert (j["state"], j["parked_from"]) == ("parked", "drafting")
    assert js.next_step(db, j) == ("drafting", [])
    assert "resume it to drafting" in _blocked(db, slug, "in_review")
    j = js.move(db, slug, "drafting")
    assert (j["state"], j["parked_from"]) == ("drafting", "")


def test_abandon_needs_a_reason_and_keeps_the_records(db):
    slug, jid = _to_drafting(db)
    _rev(db, J.get(db, slug))
    assert "say why" in _blocked(db, slug, "abandoned")
    j = js.move(db, slug, "abandoned", reason="the effect vanished at N=500")
    assert j["abandoned_reason"] == "the effect vanished at N=500"
    assert len(J.revisions(db, jid)) == 1
    assert js.next_step(db, j) == (None, [])
    assert "cannot be parked" in _blocked(db, slug, "parked")
    assert "only move forward" in _blocked(db, slug, "drafting")


def test_a_parked_journal_can_be_abandoned(db):
    slug, _ = _to_drafting(db)
    js.move(db, slug, "parked")
    assert js.move(db, slug, "abandoned", reason="superseded")["state"] == "abandoned"


def test_published_work_cannot_be_abandoned(db):
    slug, _ = _to_drafting(db)
    db.execute("UPDATE journal SET state='published' WHERE slug=?", (slug,))
    assert "cannot be abandoned" in _blocked(db, slug, "abandoned", reason="x")
    assert "cannot be parked" in _blocked(db, slug, "parked")


def test_going_back_has_no_gate(db):
    # An article cannot reach drafting without passing every gate; force the
    # state so this tests the backward edges only.
    j = J.create_journal(db, "Back", kind="article")
    slug, jid = j["slug"], j["id"]
    db.execute("UPDATE journal SET state='drafting' WHERE id=?", (jid,))
    assert js.move(db, slug, "experimenting")["state"] == "experimenting"
    assert js.move(db, slug, "researching")["state"] == "researching"
    assert J.history(db, jid)[-1]["checks"] == "[]"


def test_bad_targets(db):
    slug = J.create_journal(db, "Targets")["slug"]
    assert "not a state" in _blocked(db, slug, "shipped")
    assert "already captured" in _blocked(db, slug, "captured")
    assert "only move forward to proposed" in _blocked(db, slug, "drafting")


def test_measured_needs_a_posting_time(db):
    slug, jid = _to_drafting(db)
    rev = _rev(db, J.get(db, slug))
    db.execute("UPDATE journal SET state='shared' WHERE slug=?", (slug,))
    J.add_publication(db, jid, rev["id"], "bluesky", status="posted", posted_at="")
    J.add_publication(db, jid, rev["id"], "mastodon", status="draft")
    reasons = _blocked(db, slug, "measured")
    assert "bluesky post has no posting time" in reasons
    assert "mastodon" not in reasons


def test_counts_by_state(db):
    J.create_journal(db, "One")
    J.create_journal(db, "Two")
    assert J.counts_by_state(db) == {"captured": 2}
