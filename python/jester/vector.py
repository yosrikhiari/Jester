"""Qdrant vector store wrapper. Local mode (no server) for mock/Checkpoint 1."""
from qdrant_client import QdrantClient, models

from jester.embed import EmbeddingClient, FakeEmbedding

COLLECTION = "nuggets"


class VectorDimensionMismatch(RuntimeError):
    """An existing collection's vector width does not match the embedder.

    Raised at OPEN time rather than left to surface as a numpy broadcast error
    on the first upsert, because by then the traceback points at array shapes
    instead of at the configuration change that caused it."""


class VectorStore:
    def __init__(self, client: QdrantClient, embed: EmbeddingClient, force_fail: bool = False,
                 collection: str = COLLECTION):
        self.client = client
        self.embed = embed
        self.force_fail = force_fail
        self.collection = collection
        self._ensure()

    @classmethod
    def in_memory(cls, embed: EmbeddingClient = None, force_fail: bool = False,
                  collection: str = COLLECTION) -> "VectorStore":
        return cls(QdrantClient(":memory:"), embed or FakeEmbedding(), force_fail=force_fail,
                   collection=collection)

    @classmethod
    def open(cls, path: str, embed: EmbeddingClient = None, force_fail: bool = False,
             collection: str = COLLECTION) -> "VectorStore":
        return cls(QdrantClient(path=path), embed or FakeEmbedding(), force_fail=force_fail,
                   collection=collection)

    @classmethod
    def for_url(cls, url: str, embed: EmbeddingClient = None, force_fail: bool = False,
                collection: str = COLLECTION) -> "VectorStore":
        """Shared-server mode (§37.17): many processes against one Qdrant."""
        return cls(QdrantClient(url=url), embed or FakeEmbedding(), force_fail=force_fail,
                   collection=collection)

    @staticmethod
    def count_points_at(url: str, collection: str = COLLECTION) -> int:
        """§33 #4 parity helper. Missing collection counts as 0; connection
        failures raise so the caller can report them."""
        client = QdrantClient(url=url)
        try:
            if not client.collection_exists(collection):
                return 0
            return client.count(collection, exact=True).count
        finally:
            client.close()

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    def _ensure(self):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(
                    size=self.embed.dim, distance=models.Distance.COSINE
                ),
            )
            return
        # The collection already exists — and a collection remembers the width
        # it was CREATED with. Every store on this machine was created by the
        # 32-dimension FakeEmbedding, so switching embedding_provider to ollama
        # (768) leaves the old collection in place and the first upsert dies
        # deep inside numpy with "could not broadcast input array from shape
        # (768,) into shape (32,)" — an error that names neither the config
        # key that caused it nor the store that has to be rebuilt.
        try:
            have = self.client.get_collection(
                self.collection
            ).config.params.vectors.size
        except Exception:  # noqa: BLE001 - an unreadable config is not fatal here
            return
        want = int(self.embed.dim)
        if have is not None and int(have) != want:
            raise VectorDimensionMismatch(
                f"collection {self.collection!r} was built for {have}-dimension "
                f"vectors but the current embedding backend produces {want}. "
                "Vectors of two widths cannot share a collection: delete the "
                "old store and re-embed (`jester reembed`), or point "
                "JESTER_QDRANT_URL at a different server."
            )

    def upsert(self, unique_key: str, text: str, payload: dict) -> None:
        if self.force_fail:
            raise RuntimeError("simulated Qdrant upsert failure")
        vec = self.embed.embed([text])[0]
        self.client.upsert(
            self.collection,
            points=[
                models.PointStruct(
                    id=abs(hash(unique_key)) % (2**63),
                    vector=vec,
                    payload={**payload, "unique_key": unique_key},
                )
            ],
        )

    def search(self, text: str, threshold: float):
        vec = self.embed.embed([text])[0]
        resp = self.client.query_points(
            self.collection, query=vec, limit=5, score_threshold=threshold
        )
        return resp.points

    def search_scored(self, text: str, limit: int = 5):
        """Scored neighbors WITHOUT a threshold cut (§37.13): callers classify
        via archivist.classify_similarity so sub-threshold near-misses stay
        visible instead of being silently swallowed by score_threshold."""
        vec = self.embed.embed([text])[0]
        resp = self.client.query_points(self.collection, query=vec, limit=limit)
        return [(p.score, p.payload.get("unique_key")) for p in resp.points]
