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
