"""Marking leads from the console (2026-09-29).

Until now the only way to record what came of a lead was `jester signals
outcome --record ... --set ...`, and 1,300 buyer signals sat with 0 outcomes.
The console showed outcomes and could filter by them, but could not set one.

Two things are pinned down here: the new `picked` step (read, chosen, not sent
yet), which must stay in the handover and must not count as worked; and the
console endpoint, which has to go through the same `set_outcome` as the CLI.
"""
from pathlib import Path

import pytest

from jester import signals as sig
from jester.console.api import ConsoleAPI
from jester.signals import leads
from jester.store import open_db

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _buyer(db, source_id, conf=0.9, audience="buyer"):
    s = sig.Signal(source_id=source_id, platform="hackernews",
                   source_url=f"https://news.ycombinator.com/item?id={source_id}",
                   community="hn/hiring", kind="post",
                   text=f"Acme {source_id} | contract | Python | apply: jobs@acme{source_id}.com",
                   mode="live", audience=audience, match_confidence=conf,
                   company=f"Acme {source_id}", buyer_intent="contract")
    sig.upsert(db, [s])
    return f"hackernews:{source_id}"


@pytest.fixture()
def signals(tmp_path):
    path = str(tmp_path / "signals-live.db")
    db = sig.open_signals(path)
    ids = [_buyer(db, "1", 0.95), _buyer(db, "2", 0.80), _buyer(db, "3", 0.70),
           _buyer(db, "4", 0.60, audience="practitioner")]
    db.close()
    return path, ids


def _api(tmp_path, signals_path, **kw):
    main = str(tmp_path / "jester.db")
    open_db(main).close()
    return ConsoleAPI(main, str(REPO_CONFIG), signals_db=signals_path, **kw)


# ---- the `picked` step --------------------------------------------------------

def test_picked_is_an_outcome_and_comes_before_contacted():
    assert sig.OUTCOMES.index("picked") < sig.OUTCOMES.index("contacted")


def test_a_pick_is_neither_worked_nor_untouched(signals):
    path, (a, b, c, _) = signals
    db = sig.open_signals(path)
    sig.set_outcome(db, a, "picked")
    sig.set_outcome(db, b, "contacted")
    o = sig.outcomes(db)
    assert (o["picked"], o["worked"], o["untouched"]) == (1, 1, 1)
    db.close()


def test_a_picked_lead_stays_in_the_handover_and_comes_first(signals, tmp_path):
    path, (a, b, c, _) = signals
    db = sig.open_signals(path)
    sig.set_outcome(db, c, "picked")      # the weakest one, picked by a person
    sig.set_outcome(db, b, "contacted")   # already somebody's: left out
    ids = [r["record_id"] for r in leads.gather(db)["actionable"]]
    assert ids == [c, a], "picked first, contacted not handed over twice"
    db.close()


# ---- the console endpoint -----------------------------------------------------

def test_the_console_records_an_outcome_with_its_note(signals, tmp_path):
    path, (a, *_rest) = signals
    res = _api(tmp_path, path).signal_outcome(a, "picked", "  strong fit, Python  ")
    assert res["ok"] and res["was"] == ""
    assert res["row"]["outcome"] == "picked"
    assert res["row"]["outcome_note"] == "strong fit, Python"
    assert res["row"]["outcome_at"]
    assert res["totals"]["picked"] == 1 and res["totals"]["worked"] == 0


def test_a_lead_moves_and_can_be_cleared(signals, tmp_path):
    path, (a, *_rest) = signals
    api = _api(tmp_path, path)
    api.signal_outcome(a, "picked")
    moved = api.signal_outcome(a, "replied", "wants a call Friday")
    assert moved["was"] == "picked" and moved["totals"]["replied_or_better"] == 1
    cleared = api.signal_outcome(a, "")
    assert cleared["row"]["outcome"] == "" and cleared["row"]["outcome_at"] == ""


def test_an_invented_outcome_or_record_is_refused_not_ignored(signals, tmp_path):
    path, (a, *_rest) = signals
    api = _api(tmp_path, path)
    bad = api.signal_outcome(a, "interested")
    assert bad["ok"] is False and "not an outcome" in bad["error"]
    missing = api.signal_outcome("hackernews:nope", "picked")
    assert missing["ok"] is False and "no record" in missing["error"]


def test_a_read_only_console_does_not_write(signals, tmp_path):
    path, (a, *_rest) = signals
    res = _api(tmp_path, path, read_only=True).signal_outcome(a, "picked")
    assert res == {"ok": False, "error": "this console is read-only"}
    db = sig.open_signals(path)
    assert sig.outcomes(db)["picked"] == 0
    db.close()


def test_no_archive_is_said_plainly(tmp_path):
    res = _api(tmp_path, str(tmp_path / "missing.db")).signal_outcome("x", "picked")
    assert res["ok"] is False and "no signal archive" in res["error"]


# ---- the work queue -----------------------------------------------------------

def test_untouched_filters_to_leads_nobody_has_marked(signals, tmp_path):
    path, (a, b, c, _) = signals
    api = _api(tmp_path, path)
    api.signal_outcome(a, "picked")
    queue = api.signals(audience="buyer", outcome="untouched")
    assert {r["record_id"] for r in queue["rows"]} == {b, c}
    assert queue["total"] == 2


def test_the_outcome_facet_can_be_clicked_to_untouched(signals, tmp_path):
    path, (a, *_rest) = signals
    api = _api(tmp_path, path)
    api.signal_outcome(a, "picked")
    facet = {f["value"]: f["n"] for f in api.signals(audience="buyer")["facets"]["outcome"]}
    assert facet == {"untouched": 2, "picked": 1}


# ---- the queue leaves Reddit out; AI-training gigs are not buyers -----------

def test_the_queue_is_unmarked_buyers_from_usable_sources(signals, tmp_path):
    path, (a, b, c, _) = signals
    db = sig.open_signals(path)
    sig.upsert(db, [sig.Signal(source_id="t3_x", platform="reddit", community="r/forhire",
                               kind="post", text="[Hiring] contract Python dev $80/hr",
                               mode="live", audience="buyer", match_confidence=0.9)])
    db.close()
    api = _api(tmp_path, path)
    api.signal_outcome(a, "picked")
    queue = api.signals(queue="1")
    assert {r["record_id"] for r in queue["rows"]} == {b, c}, \
        "marked leads, non-buyers and set-apart Reddit rooms are all left out"
    # The facets count inside the queue too, so a click never promises rows
    # the queue does not hold.
    assert {f["value"] for f in queue["facets"]["community"]} == {"hn/hiring"}


@pytest.mark.parametrize("headline", [
    "[Hiring] Python Software Engineers/ Programmers wanted for Remote AI Training | USD $50+ / hr",
    "[Hiring] Evaluators for AI training (English language)",
])
def test_an_ai_training_task_on_reddit_is_a_gig(headline):
    from jester.signals.from_archive import gig_or_not_engineering
    assert gig_or_not_engineering(headline) == "gig"


def test_an_ml_engineering_role_is_not_mistaken_for_one():
    from jester.signals.from_archive import gig_or_not_engineering
    from jester.signals.sources.jobboards import _is_gig_task
    assert gig_or_not_engineering("[Hiring] Senior ML Engineer, model training infrastructure") == ""
    assert not _is_gig_task("Senior ML Engineer, Training Infrastructure")
    assert _is_gig_task("Quality Control Specialist (AI Training)")
