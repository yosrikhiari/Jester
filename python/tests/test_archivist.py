"""TDD tests for the M1.5 archivist agent: dedup, R32 model-refuse, needs_reembed."""
import tempfile

from jester.agents.archivist import Archivist, ModelMismatch
from jester.config import Thresholds, load_thresholds
from jester.embed import FakeEmbedding
from jester.llm import FakeLLM, ALLOWED_CATEGORIES
from jester.agents.extractor import extract
from jester.store import open_db, meta_get, meta_set
from jester.vector import VectorStore


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
