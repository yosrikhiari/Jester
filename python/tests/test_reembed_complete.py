"""DD-M6: actually completing an embedding-model change.

Swapping `embedding_provider` is three steps, and `jester reembed --all` was
only ever one of them:

- the vectors must be rewritten (it did that);
- the collection must be built for the new width - and while the old one is
  still on disk, opening it raises VectorDimensionMismatch, so the command that
  could rebuild the vectors cannot open the store it has to rebuild;
- `corpus_embedding_model` must say which model the archive now holds, because
  the archivist compares every run against that tag and raises ModelMismatch
  while it still names the old one.

These tests pin the whole loop, including the two ways it must NOT claim
completion: a failing `--all` leaves the old tag alone, and `--recreate` without
`--all` is refused before it touches a single vector.
"""
from pathlib import Path

import pytest

from jester.config import load_config
from jester.models import Nugget
from jester.roles import embedding_model_of
from jester.store import insert_nugget, meta_get, meta_set, open_db, start_run

CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")
REPO_CFG = load_config(CONFIG_DIR)


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class _FakeEmbedder:
    def __init__(self, dim):
        self.dim = dim

    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class _FakeStore:
    """Records the sequence of operations, so ordering is a fact a test reads."""

    def __init__(self, fail=False):
        self.events = []
        self.fail = fail
        self.collection = "nuggets"
        self.embed = _FakeEmbedder(32)

    def count(self):
        return 0

    def recreate(self):
        self.events.append("delete")

    def upsert(self, key, text, payload):
        if self.fail:
            raise RuntimeError("qdrant down")
        self.events.append(f"upsert:{key}")

    def upsert_many(self, items):
        if self.fail:
            raise RuntimeError("qdrant down")
        for key, _t, _p in items:
            self.events.append(f"upsert:{key}")
        return len(items)


def _db(tmp_path, *keys):
    path = tmp_path / "j.db"
    db = open_db(str(path))
    for key in keys:
        insert_nugget(db, Nugget(unique_key=key, platform="reddit",
                                 category="pain_point", extracted_insight="x",
                                 raw_text=f"body of {key}"))
    db.close()
    return str(path)


def _tag(db_path):
    db = open_db(db_path)
    value = meta_get(db, "corpus_embedding_model")
    db.close()
    return value


def _meta_tag(db_path, value):
    db = open_db(db_path)
    meta_set(db, "corpus_embedding_model", value)
    db.close()


class TestTheTagIsTheLastStep:
    def test_a_completed_all_records_the_model_it_just_embedded_with(self, tmp_path,
                                                                    monkeypatch):
        from jester import cli

        db_path = _db(tmp_path, "a1", "a2")
        store = _FakeStore()
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: store)

        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False, all=True,
                            recreate=False))

        assert _tag(db_path) == embedding_model_of(REPO_CFG.thresholds)

    def test_a_failing_all_does_not_move_the_tag(self, tmp_path, monkeypatch):
        """Half the archive is in the old space and half in the new. Recording
        the new model here would certify vectors that were never written, and
        would make the archivist accept exactly the mixed store its guard exists
        to refuse."""
        from jester import cli

        db_path = _db(tmp_path, "a1")
        _meta_tag(db_path, "old-model")
        monkeypatch.setattr(cli, "_vector_for",
                            lambda cfg, p, **kw: _FakeStore(fail=True))

        with pytest.raises(SystemExit) as exc:
            cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False,
                                all=True, recreate=False))

        assert exc.value.code == 1
        assert _tag(db_path) == "old-model"

    def test_a_pending_drain_does_not_claim_the_whole_corpus(self, tmp_path, monkeypatch):
        """`--pending` re-embeds the failure backlog, not the archive. Stamping
        the model here would speak for vectors this run never touched."""
        from jester import cli

        db_path = _db(tmp_path, "a1")
        db = open_db(db_path)
        db.execute("UPDATE nuggets SET needs_reembed=1")
        db.commit()
        db.close()
        _meta_tag(db_path, "old-model")
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: _FakeStore())

        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=True, all=False,
                            recreate=False))

        assert _tag(db_path) == "old-model"

    def test_an_empty_archive_stamps_nothing(self, tmp_path, monkeypatch):
        """No vectors, no claim about what they were made with - and the tag
        stays unset so the archivist's own first write records the model."""
        from jester import cli

        db_path = _db(tmp_path)
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: _FakeStore())

        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False, all=True,
                            recreate=False))

        assert _tag(db_path) is None


class TestRecreate:
    def test_refused_without_all_before_touching_anything(self, tmp_path, monkeypatch,
                                                          capsys):
        from jester import cli

        db_path = _db(tmp_path, "a1")
        store = _FakeStore()
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: store)

        with pytest.raises(SystemExit) as exc:
            cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=True,
                                all=False, recreate=True))

        assert exc.value.code == 1
        assert "--all" in capsys.readouterr().out
        assert store.events == [], "a refusal must not delete or write vectors"

    def test_refused_while_a_run_is_active(self, tmp_path, monkeypatch, capsys):
        """Deleting the collection mid-run would leave the running archivist
        querying an empty space and calling every nugget distinct."""
        from jester import cli

        path = tmp_path / "j.db"
        db = open_db(str(path))
        insert_nugget(db, Nugget(unique_key="a1", platform="reddit",
                                 category="pain_point", extracted_insight="x",
                                 raw_text="body"))
        start_run(db, "active-run")
        db.close()
        store = _FakeStore()
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: store)

        with pytest.raises(SystemExit) as exc:
            cli.cmd_reembed(_ns(db=str(path), config=CONFIG_DIR, pending=False,
                                all=True, recreate=True))

        assert exc.value.code == 1
        assert "still active" in capsys.readouterr().out
        assert store.events == []

    def test_the_store_is_dropped_before_the_replacement_is_written(self, tmp_path,
                                                                   monkeypatch):
        from jester import cli

        db_path = _db(tmp_path, "a1", "a2")
        store = _FakeStore()
        monkeypatch.setattr(cli, "_vector_for", lambda cfg, p, **kw: store)

        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False, all=True,
                            recreate=True))

        assert store.events[0] == "delete", "the old collection has to go first"
        assert store.events.count("upsert:a1") == 1

    def test_the_recreate_open_is_unchecked_and_the_ordinary_one_is_not(
            self, tmp_path, monkeypatch):
        """The drop has to act on a collection whose width it already knows is
        wrong; every other reembed - and every other reader of that store - must
        still be refused, or a mixed-width archive reads as healthy."""
        from jester import cli

        db_path = _db(tmp_path, "a1")
        seen = []

        def spy(cfg, p, check_dimension=True):
            seen.append(check_dimension)
            return _FakeStore()

        monkeypatch.setattr(cli, "_vector_for", spy)

        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False, all=True,
                            recreate=True))
        assert seen == [False], \
            "one unchecked open, and the store it returns is the one that writes"

        seen.clear()
        cli.cmd_reembed(_ns(db=db_path, config=CONFIG_DIR, pending=False, all=True,
                            recreate=False))
        assert seen == [True], "the ordinary path keeps the width guard"

    def test_the_guarded_open_of_a_wrong_width_store_still_raises(self):
        """--recreate must not soften the width check for anyone else."""
        from qdrant_client import QdrantClient

        from jester.embed import FakeEmbedding
        from jester.vector import VectorDimensionMismatch, VectorStore

        client = QdrantClient(":memory:")
        VectorStore(client, FakeEmbedding(), collection="widths")

        with pytest.raises(VectorDimensionMismatch):
            VectorStore(client, _FakeEmbedder(48), collection="widths")


class TestTheRebuildIsReal:
    def test_a_recreated_collection_opens_under_the_new_width(self):
        """End to end against a real Qdrant client, no stub in it: build at 32,
        switch the embedder to 48, recreate, and the GUARDED open that the very
        first test in this file says must refuse now succeeds. That is the whole
        of DD-M6 - an embedding-model change that can be finished."""
        from qdrant_client import QdrantClient

        from jester.embed import FakeEmbedding
        from jester.vector import VectorStore

        client = QdrantClient(":memory:")
        old = VectorStore(client, FakeEmbedding(), collection="widths")
        old.upsert("k", "text", {})
        assert old.count() == 1

        wide = _FakeEmbedder(48)
        unchecked = VectorStore(client, wide, collection="widths",
                                check_dimension=False)
        unchecked.recreate()
        assert unchecked.count() == 0

        unchecked.upsert("k", "text", {})
        assert len(unchecked.vector_for("k")) == 48

        VectorStore(client, wide, collection="widths")  # guarded, and it passes
