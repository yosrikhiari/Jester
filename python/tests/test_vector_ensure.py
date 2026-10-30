"""Opening a store that already exists must still make it fast (DD-M5).

`INDEXING_THRESHOLD` was only ever handed to `create_collection`, so a
collection made before that number existed — every store on a machine that
has been running Jester for a while — keeps Qdrant's default of 20,000 points
PER SEGMENT before it builds an HNSW index. Under that, every dedup lookup is
a brute-force scan of the whole corpus: the measured 83 ms median that grows
with the archive, and the 408s that killed scheduled runs. The exists-branch
used to look at the vector width and nothing else.
"""
import pytest

from jester.embed import FakeEmbedding
from jester.vector import INDEXING_THRESHOLD, VectorDimensionMismatch, VectorStore


class _Params:
    def __init__(self, size):
        self.vectors = type("V", (), {"size": size})()


class _Info:
    def __init__(self, size, threshold):
        self.config = type("C", (), {
            "params": _Params(size),
            "optimizer_config": type("O", (), {"indexing_threshold": threshold})(),
        })()


class _Client:
    """Answers only what `_ensure` asks, and remembers what it was told."""

    def __init__(self, size=32, threshold=20000):
        self.info = _Info(size, threshold)
        self.updates = []

    def collection_exists(self, name):
        return True

    def get_collection(self, name):
        return self.info

    def update_collection(self, name, optimizers_config=None):
        self.updates.append(optimizers_config)
        self.info = _Info(self.info.config.params.size,
                          optimizers_config.indexing_threshold)


def _open(client):
    return VectorStore(client, FakeEmbedding())


def test_an_existing_store_is_brought_to_the_indexing_threshold():
    store = _open(_Client(threshold=20000))
    assert len(store.client.updates) == 1
    assert store.client.updates[0].indexing_threshold == INDEXING_THRESHOLD


def test_a_store_that_is_already_indexed_is_left_alone():
    store = _open(_Client(threshold=INDEXING_THRESHOLD))
    assert store.client.updates == []


def test_a_deliberately_lower_threshold_is_not_raised():
    """An operator who tuned this number down is not being overruled by an
    open call; only a store still sitting above ours gets moved."""
    store = _open(_Client(threshold=500))
    assert store.client.updates == []


def test_an_unknown_threshold_is_set_rather_than_guessed():
    store = _open(_Client(threshold=None))
    assert [u.indexing_threshold for u in store.client.updates] == [INDEXING_THRESHOLD]


def test_an_unreadable_config_is_not_fatal_on_open(monkeypatch):
    """A store whose details cannot be read still opens: the width guard and
    the indexing fix are both best-effort, and lookups work anyway."""
    client = _Client()

    def boom(name):
        raise RuntimeError("collection info unavailable")

    monkeypatch.setattr(client, "get_collection", boom)
    store = VectorStore(client, FakeEmbedding())
    assert store.collection == "nuggets"
    assert client.updates == []


def test_a_width_change_is_still_fatal_on_open():
    embed = FakeEmbedding()
    embed.dim = 768
    with pytest.raises(VectorDimensionMismatch) as exc:
        VectorStore(_Client(size=32), embed)
    assert "dimension" in str(exc.value)


def test_the_real_client_accepts_the_open_of_an_existing_collection():
    """The embedded client is what `VectorStore.open` hands tests and local
    runs; the update call must not raise against it."""
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    client.create_collection("nuggets", vectors_config=_vector_params())
    store = VectorStore(client, FakeEmbedding())
    assert store.count() == 0


def _vector_params():
    from qdrant_client import models

    return models.VectorParams(size=FakeEmbedding().dim, distance=models.Distance.COSINE)
