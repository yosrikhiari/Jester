"""Every LLM call leaves a row with stable prompt/response hashes (S-08).

Before this, a run recorded which MODELS it used and the machine it ran on,
and nothing about what it actually asked. A score shift could be traced to a
role's settings but not to the instruction that produced an idea, and a rerun
could not prove it had asked the same question.

The hashes are the evidence; the text itself is opt-in (`JESTER_LLM_CALL_TEXT`)
because a full prompt carries a scraped comment into the archive twice.
"""
import json
import logging
import sqlite3
from pathlib import Path

from jester import llm, provenance
from jester.store import open_db

RUN = "run-2026-10-31T10:00:00Z"
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


class StubAgent(llm._ExtractorRole, llm._Agent):
    """A live-shaped agent whose transport is a function."""

    TIMEOUT_S = 1.0

    def __init__(self, reply='{"category": "pain_point", "insight": "x"}',
                 error=None):
        super().__init__("stub-model")
        self.reply, self.error = reply, error

    def _complete(self, system, user, temperature):
        if self.error is not None:
            raise self.error
        return self.reply


def _db():
    return open_db(":memory:")


def test_a_row_is_written_per_call_with_both_hashes():
    db = _db()
    agent = StubAgent()
    provenance.attach(db, RUN, agent)

    draft = agent.extract("my CI is slow")

    assert draft.extracted_insight == "x"
    rows = provenance.calls_for(db, RUN)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == RUN
    assert row["role"] == "extractor"
    assert row["model"] == "stub-model"
    assert row["ok"] == 1
    assert len(row["prompt_sha256"]) == 64
    assert len(row["response_sha256"]) == 64
    assert row["prompt_chars"] > 0 and row["response_chars"] > 0
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0
    assert row["prompt_text"] is None, "full text must be opt-in"


def test_the_same_prompt_hashes_the_same_across_reruns():
    db = _db()
    first = StubAgent()
    second = StubAgent()
    provenance.attach(db, RUN, first, second)

    first.extract("same comment")
    second.extract("same comment")

    a, b = provenance.calls_for(db, RUN)
    assert a["prompt_sha256"] == b["prompt_sha256"]
    assert a["response_sha256"] == b["response_sha256"]


def test_a_failed_call_is_recorded_without_a_response_hash():
    db = _db()
    agent = StubAgent(error=TimeoutError("read timed out"))
    provenance.attach(db, RUN, agent)

    agent.extract("anything")

    (row,) = provenance.calls_for(db, RUN)
    assert row["ok"] == 0
    assert row["response_sha256"] is None
    assert "TimeoutError" in row["error"]
    assert len(row["prompt_sha256"]) == 64, "the question was still asked"


def test_full_text_is_kept_only_when_asked(monkeypatch):
    db = _db()
    agent = StubAgent()
    provenance.attach(db, RUN, agent)
    monkeypatch.setenv(provenance.TEXT_ENV, "1")

    agent.extract("keep this body")

    (row,) = provenance.calls_for(db, RUN)
    assert "keep this body" in row["prompt_text"]
    assert row["response_text"] == agent.reply


def test_old_calls_are_pruned_and_new_ones_are_not():
    db = _db()
    db.execute(
        "INSERT INTO llm_calls (run_id, created_utc, role, prompt_sha256)"
        " VALUES ('old', '2020-01-01T00:00:00+00:00', 'extractor', 'x')")
    db.commit()
    agent = StubAgent()
    provenance.attach(db, RUN, agent)
    agent.extract("fresh")

    removed = provenance.prune_calls(db, keep_days=30)

    assert removed == 1
    assert [r["run_id"] for r in provenance.calls_for(db, "old")] == []
    assert len(provenance.calls_for(db, RUN)) == 1


def test_a_malformed_retention_setting_falls_back_instead_of_killing_the_run(monkeypatch):
    # prune_calls runs at the start of every pipeline run. int("30d") used to
    # raise there, so one typo in .env turned every night into a traceback
    # before the first model call.
    monkeypatch.setenv(provenance.KEEP_DAYS_ENV, "30d")
    db = _db()
    db.execute(
        "INSERT INTO llm_calls (run_id, created_utc, role, prompt_sha256)"
        " VALUES ('old', '2020-01-01T00:00:00+00:00', 'extractor', 'x')")
    db.commit()

    removed = provenance.prune_calls(db)

    assert removed == 1, "the malformed value falls back to the 30-day default"


def test_calls_for_is_scoped_to_one_run():
    db = _db()
    one, two = StubAgent(), StubAgent()
    provenance.attach(db, "run-a", one)
    provenance.attach(db, "run-b", two)
    one.extract("a")
    two.extract("b")
    assert len(provenance.calls_for(db, "run-a")) == 1
    assert len(provenance.calls_for(db, "run-b")) == 1


def test_a_recorder_that_raises_never_breaks_the_call(caplog):
    db = _db()
    agent = StubAgent()
    provenance.attach(db, RUN, agent)
    agent._recorder = lambda **kw: (_ for _ in ()).throw(sqlite3.OperationalError("db gone"))

    with caplog.at_level(logging.DEBUG, logger="jester.llm"):
        draft = agent.extract("still works")

    assert draft.extracted_insight == "x"
    # V3-07: swallowed on purpose, but not invisibly — a provenance table that
    # has stopped accepting rows is a fact about the run.
    assert "OperationalError" in caplog.text


# ---- a failure is a fact on the row (V3-07) -------------------------------

def test_a_malformed_reply_is_filed_as_a_failure():
    """A reply that is not a JSON object was recorded with no error, so the
    row said ok=1. A run whose every answer was garbage reported a clean
    provenance table."""
    db = _db()
    agent = StubAgent(reply="not json at all")
    provenance.attach(db, RUN, agent)

    agent.extract("my CI is slow")

    (row,) = provenance.calls_for(db, RUN)
    assert row["ok"] == 0
    assert "malformed" in row["error"]
    assert len(row["response_sha256"]) == 64, "the bytes are still the evidence"


def test_a_reply_the_role_check_refuses_is_filed_as_a_failure():
    """This answer parses but fails the role's own check (here: a batch reply
    that is not an N-item list). The bytes are already on the row, so the
    refusal is stamped on THAT row rather than written as a second call."""
    db = _db()
    agent = StubAgent(reply=json.dumps({"items": []}))
    provenance.attach(db, RUN, agent)

    agent.extract_batch(["one", "two"])

    rows = provenance.calls_for(db, RUN)
    assert len(rows) == 3, "one refused batch call, then two per-comment retries"
    assert rows[0]["ok"] == 0
    assert "N-item list" in rows[0]["error"]
    assert rows[1]["ok"] == 1 and rows[2]["ok"] == 1


# ---- a re-score files its calls too (V3-07) -------------------------------

def test_a_rescore_is_filed_under_the_run_that_owns_the_archive(
        tmp_path, offline_config, monkeypatch):
    """`jester rescore --pending` called the critic and attached nothing, so
    its calls left no llm_calls row: a score written by a re-score could not
    be traced to the question that produced it."""
    import jester.cli as cli
    from jester.llm import GroqCriticLLM
    from jester.models import Idea, IdeaScores
    from jester.store import insert_idea, start_run

    path = str(tmp_path / "j.db")
    db = open_db(path)
    start_run(db, RUN)  # a run is still live: the re-score belongs to it
    insert_idea(db, Idea(
        title="Backup Sentinel", problem_statement="p", proposed_solution="s",
        supporting_nuggets=["k1"], status="new", needs_score=True,
        scores=IdeaScores(demand_signal=None, feasibility=None,
                          competition=None, overall=None)))
    db.close()

    class _Client:
        def chat(self, **kw):
            return {"message": {"content": json.dumps(
                {"demand_signal": 8, "feasibility": 7, "competition": None})}}

    critic = GroqCriticLLM(client=_Client())
    monkeypatch.setattr(cli, "select_critic_llm", lambda cfg: critic)
    cli.main(["rescore", "--pending", "--limit", "1", "--db", path,
              "--config", str(offline_config)])

    db = open_db(path)
    (row,) = provenance.calls_for(db, RUN)
    assert row["role"] == "critic"
    assert row["ok"] == 1
    assert row["model"] == critic.name, "the model that actually ran, not config/"


def test_a_rescore_with_no_run_in_flight_records_nothing(tmp_path, offline_config,
                                                         monkeypatch):
    """No run to file under, so no row — provenance is per run, not a log."""
    import jester.cli as cli
    from jester.llm import GroqCriticLLM
    from jester.models import Idea, IdeaScores
    from jester.store import insert_idea

    path = str(tmp_path / "j.db")
    db = open_db(path)
    insert_idea(db, Idea(
        title="Backup Sentinel", problem_statement="p", proposed_solution="s",
        supporting_nuggets=["k1"], status="new", needs_score=True,
        scores=IdeaScores(demand_signal=None, feasibility=None,
                          competition=None, overall=None)))
    db.close()

    class _Client:
        def chat(self, **kw):
            return {"message": {"content": json.dumps(
                {"demand_signal": 8, "feasibility": 7, "competition": None})}}

    monkeypatch.setattr(cli, "select_critic_llm",
                        lambda cfg: GroqCriticLLM(client=_Client()))
    cli.main(["rescore", "--pending", "--limit", "1", "--db", path,
              "--config", str(offline_config)])

    db = open_db(path)
    assert db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    assert db.execute("SELECT demand_signal FROM ideas").fetchone()[0] == 8

