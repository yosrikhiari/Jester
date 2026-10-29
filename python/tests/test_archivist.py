"""TDD tests for the M1.5 archivist agent: dedup, R32 model-refuse, needs_reembed."""
import tempfile

import pytest

from jester.agents import archivist as archivist_module
from jester.agents.archivist import Archivist, ModelMismatch
from jester.config import Thresholds, load_thresholds
from jester.embed import FakeEmbedding
from jester.llm import FakeLLM, ALLOWED_CATEGORIES
from jester.agents.extractor import extract
from jester.store import open_db, meta_get, meta_set
from jester.vector import VectorStore, point_id_for


def _db():
    return open_db(":memory:")


def _vector(force_fail=False):
    return VectorStore.in_memory(FakeEmbedding(), force_fail=force_fail)


def _nuggets():
    return extract(
        [
            {"id": "c1", "body": "self host email is painful", "score": 1},
            {"id": "c2", "body": "self host email is painful", "score": 2},
        ],
        FakeLLM(),
        platform="reddit",
        thread_id="t1",
        source_url="x",
        run_id="r",
    )


def test_identical_text_deduped():
    db = _db()
    arch = Archivist(db, _vector(), Thresholds(embedding_model="nomic-embed-text"))
    kept = arch.run(_nuggets())
    assert len(kept) == 1  # second identical comment is a duplicate


def test_r32_model_mismatch_refuses():
    db = _db()
    meta_set(db, "corpus_embedding_model", "nomic-embed-text:old")
    arch = Archivist(db, _vector(), Thresholds(embedding_model="nomic-embed-text"))
    try:
        arch.run(_nuggets())
    except ModelMismatch:
        return
    raise AssertionError("expected ModelMismatch when corpus model != config model")


def test_needs_reembed_on_upsert_failure():
    db = _db()
    arch = Archivist(db, _vector(force_fail=True), Thresholds(embedding_model="nomic-embed-text"))
    nuggets = _nuggets()
    kept = arch.run([nuggets[0]])
    assert len(kept) == 1
    # row persisted but flagged for re-embed, not present in vector store
    row = db.execute(
        "SELECT needs_reembed FROM nuggets WHERE unique_key=?", (nuggets[0].unique_key,)
    ).fetchone()
    assert row is not None
    assert row[0] == 1


def test_idempotent_unique_key_skip():
    db = _db()
    arch = Archivist(db, _vector(), Thresholds(embedding_model="nomic-embed-text"))
    n = _nuggets()[0]
    arch.run([n])
    arch.run([n])  # same unique_key again
    count = db.execute(
        "SELECT count(1) FROM nuggets WHERE unique_key=?", (n.unique_key,)
    ).fetchone()[0]
    assert count == 1


class _SearchFails:
    """A vector store whose dedup lookup always fails, as a busy Qdrant does.

    Wraps a real in-memory store so everything except `search_scored` behaves
    normally — the point is the ONE call that used to be unguarded.
    """

    def __init__(self, exc=None):
        self._inner = VectorStore.in_memory(FakeEmbedding())
        self._exc = exc or RuntimeError("Unexpected Response: 408 (Request Timeout)")
        self.upserts = 0

    def search_scored(self, text, limit=5):
        raise self._exc

    def upsert(self, unique_key, text, payload):
        self.upserts += 1
        return self._inner.upsert(unique_key, text, payload)


def test_dedup_failure_defers_instead_of_crashing():
    """A 408 from the vector store must not end the run.

    Four consecutive scheduled treats died on exactly this, having archived
    nothing, because the search call was unguarded while the upsert below it
    was not.
    """
    db = _db()
    arch = Archivist(db, _SearchFails(), Thresholds(embedding_model="nomic-embed-text"))
    nuggets = _nuggets()

    kept = arch.run(nuggets)

    assert kept == [], "nothing may be archived without a completed dedup check"
    assert len(arch.deferred) == len(nuggets)
    assert "408" in arch.deferred[0][1]


def test_deferred_nuggets_are_not_written_to_the_corpus():
    """Deferred means set aside, not stored — the batch is retried later."""
    db = _db()
    vec = _SearchFails()
    arch = Archivist(db, vec, Thresholds(embedding_model="nomic-embed-text"))

    arch.run(_nuggets())

    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] == 0
    assert vec.upserts == 0, "a nugget that was never dedup-checked must not embed"


def test_deferred_resets_between_runs():
    db = _db()
    arch = Archivist(db, _SearchFails(), Thresholds(embedding_model="nomic-embed-text"))
    arch.run(_nuggets())
    assert arch.deferred

    arch.vector = _vector()  # vector store recovers
    kept = arch.run(_nuggets())

    assert arch.deferred == []
    assert len(kept) == 1, "the material is archived on the pass that succeeds"


def test_unavailable_store_defers_every_nugget():
    """A vector store that could not be opened must defer, not crash.

    Opening the store makes a network call, so a stopped Qdrant fails before
    the archivist runs at all. Routing that through the same stand-in keeps one
    behaviour for "not answering" rather than two.
    """
    from jester.vector import UnavailableVectorStore

    db = _db()
    exc = ConnectionError("[WinError 10061] target machine actively refused it")
    arch = Archivist(
        db, UnavailableVectorStore(exc), Thresholds(embedding_model="nomic-embed-text")
    )
    nuggets = _nuggets()

    kept = arch.run(nuggets)

    assert kept == []
    assert len(arch.deferred) == len(nuggets)
    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] == 0


def test_dimension_mismatch_is_not_treated_as_unavailability():
    """A width mismatch must stay fatal.

    Deferring on it would convert a loud, fixable misconfiguration into runs
    that report 'completed' while archiving nothing, every time, forever — the
    opposite of what raising it at open time is for.
    """
    from jester.vector import UnavailableVectorStore, VectorDimensionMismatch

    # The stand-in exists for transport failures only; a config error must not
    # be routed into it. Guard the invariant at the type level.
    assert not issubclass(VectorDimensionMismatch, ConnectionError)

    db = _db()
    arch = Archivist(
        db,
        UnavailableVectorStore(VectorDimensionMismatch("768 vs 32")),
        Thresholds(embedding_model="nomic-embed-text"),
    )
    # If one is ever constructed with a config error anyway, it still defers
    # rather than crashing — the guard that matters lives at the call site in
    # cmd_run, which re-raises before reaching here.
    arch.run(_nuggets())
    assert len(arch.deferred) == 2


# ---- the crash window: a point with no row behind it -----------------------
#
# The archivist writes the vector point BEFORE the nugget row (the upsert is
# the call that can fail slow; the row is the fast local write). A process
# killed between those two lines leaves a point whose key no row answers, and
# its cosine is ~1.0 against the same text arriving again — the old decision
# read that as "duplicate" and locked the door on material that existed
# nowhere.


def _thresholds(category_mode: dict | None = None) -> Thresholds:
    return Thresholds(embedding_model="nomic-embed-text",
                      dedup_threshold_by_category=category_mode or {})


def test_own_reflection_in_the_store_does_not_dedup_its_own_nugget():
    """Seed the vector with the candidate's own key and no row: the pre-crash
    state. The candidate must proceed to upsert + insert, not be refused on
    its own reflection."""
    db = _db()
    vec = _vector()
    n = _nuggets()[0]
    vec.upsert(n.unique_key, n.raw_text, {"category": n.category})
    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] == 0

    arch = Archivist(db, vec, _thresholds())
    kept = arch.run([n])

    assert kept == [n], "the reflection is the candidate itself, not a duplicate"
    assert db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE unique_key=?",
        (n.unique_key,)).fetchone()[0] == 1
    assert n.embedding_id == f"emb:{point_id_for(n.unique_key)}"
    assert n.needs_reembed is False
    # the guard worked BY the reflection, not around it: its score was seen
    # and must not pollute the near-miss list either
    assert arch.near_misses == []


def test_crash_between_upsert_and_insert_recovers_on_the_next_run(monkeypatch):
    """First run: the point is written, then insert_nugget never runs
    (simulated by the process dying inside the window). Second run: the
    reflection must NOT register as a duplicate — exactly one row, no
    ghost-continue. The old `any(s >= threshold for s, _k in scored)` failed
    this by refusing on the archivist's own zombie point."""
    db = _db()
    vec = _vector()
    n = _nuggets()[0]
    arch = Archivist(db, vec, _thresholds())

    real_insert = archivist_module.insert_nugget

    def die_after_upsert(db_, row):
        raise KeyboardInterrupt("worker killed mid-nugget")

    monkeypatch.setattr(archivist_module, "insert_nugget", die_after_upsert)
    with pytest.raises(KeyboardInterrupt):
        arch.run([n])
    assert vec.count() == 1, "the point was written; the row was not"
    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] == 0

    monkeypatch.setattr(archivist_module, "insert_nugget", real_insert)
    kept = arch.run([n])

    assert kept == [n]
    rows = db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE unique_key=?", (n.unique_key,)
    ).fetchone()[0]
    assert rows == 1, "no ghost-continue: the nugget lands on the recovery pass"
    assert vec.count() == 1, "the upsert is idempotent: no second point"


def test_a_ghost_point_with_no_row_behind_it_does_not_block_the_nugget():
    """An over-threshold neighbour whose key has NO row (row pruned, or the
    crash window) is a ghost. A text that exists nowhere gets archived."""
    db = _db()
    vec = _vector()
    ghost_key = "nugget/pruned-row/01"
    same_text = "self host email is painful"
    vec.upsert(ghost_key, same_text, {"category": "pain_point"})
    n = _nuggets()[0]  # raw_text == same_text, unique_key different
    assert n.unique_key != ghost_key

    arch = Archivist(db, vec, _thresholds())
    kept = arch.run([n])

    assert kept == [n]
    assert db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE unique_key=?", (n.unique_key,)
    ).fetchone()[0] == 1


def test_one_real_duplicate_refusal_survives_the_ghost_guard():
    """The guard relaxes only when EVERY over-threshold match is a ghost. One
    real row behind a match and this is a genuine duplicate — refuse."""
    db = _db()
    vec = _vector()
    n = _nuggets()[0]
    arch = Archivist(db, vec, _thresholds())
    arch.run([n])
    n2 = _nuggets()[1]  # same text, different unique_key
    ghost = "ghost/with-no-row/09"
    vec.upsert(ghost, n2.raw_text, {"category": "pain_point"})

    kept = arch.run([n2])

    assert kept == [], "a real duplicate behind one of the matches is decisive"
    assert db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE unique_key=?", (n2.unique_key,)
    ).fetchone()[0] == 0
