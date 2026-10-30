"""Qdrant vector store wrapper. Local mode (no server) for mock/Checkpoint 1."""
import hashlib
import time

from qdrant_client import QdrantClient, models

from jester.embed import EmbeddingClient, FakeEmbedding

COLLECTION = "nuggets"

#: Seconds before a Qdrant HTTP call is abandoned. QdrantClient defaults its
#: `timeout` to None, which passes nothing to httpx and leaves httpx's own
#: 5-second default in force — short for a server that is busy indexing, and a
#: timeout here used to end the whole run rather than one lookup.
TIMEOUT_S = 30

#: A 408 from Qdrant is usually the server being busy, not the query being
#: wrong, and the next attempt a moment later normally lands. One retry, not
#: three: this sits inside a per-nugget loop that runs tens of thousands of
#: times, so every extra attempt is paid for on every failure.
RETRIES = 1
RETRY_BACKOFF_S = 0.5

#: Vectors a SEGMENT must hold before Qdrant builds an HNSW index over it.
#:
#: MEASURED. Qdrant's default is 20,000 and it is a per-segment number, not a
#: per-collection one. This archive sat at 16,958 points spread over eight
#: segments of roughly 2,100 each, so no segment ever reached the threshold,
#: `indexed_vectors_count` stayed at 0, and every dedup lookup was a
#: brute-force scan of the whole corpus: 83 ms median, and rising linearly
#: with the archive because that is what O(n) means. Under sustained load
#: those scans are what tipped into the 408s that killed four consecutive
#: treats.
#:
#: At 1,000 every segment indexes. Same collection, same 16,958 points,
#: measured immediately after: 23 ms median. The gain grows with the corpus —
#: brute force degrades linearly where HNSW degrades logarithmically.
#:
#: The cost of a low threshold is that small collections pay to build an index
#: they could have scanned. That cost is bounded and paid once; the scan cost
#: is paid on every one of tens of thousands of dedup lookups per run.
#:
#: Applied when a collection is opened, not only when it is created: a store
#: made before this number existed would otherwise keep scanning forever.
INDEXING_THRESHOLD = 1000


def point_id_for(unique_key: str) -> int:
    """The Qdrant point id a keyed document is written under.

    First 8 bytes of sha256, masked to 63 bits (Qdrant ids are unsigned
    64-bit; the mask keeps the value on the safe side of that while staying
    positive and collision-unfriendly). Deterministic on PURPOSE: the point
    id is how a nugget's vector is found again, and every id below this line
    used to be `abs(hash(key)) % 2**63` — Python salts str hashing per
    process, so that value changed on every interpreter start. A `reembed
    --all` run in a fresh process re-pointed every nugget while leaving the
    old points behind, silently doubling the collection; the `emb:...`
    embedding_id stamped into the archive described coordinates that no
    longer existed; and the doctor's rows-vs-points parity check filled with
    noise nobody could attribute to a cause.

    sha256 of the key is the cheapest primitive that makes the id a pure
    function of the key's bytes: same key, same point, across processes,
    machines, and runs.
    """
    digest = hashlib.sha256(unique_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def _with_retry(call, *, attempts: int = RETRIES + 1):
    """Run `call`, retrying transient transport failures a bounded number of
    times. The LAST exception propagates — callers decide what a hard failure
    means, and for the archivist it means deferring a nugget, not dying."""
    last = None
    for i in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - transport-agnostic on purpose
            last = exc
            if i + 1 < attempts:
                time.sleep(RETRY_BACKOFF_S * (i + 1))
    raise last


class VectorDimensionMismatch(RuntimeError):
    """An existing collection's vector width does not match the embedder.

    Raised at OPEN time rather than left to surface as a numpy broadcast error
    on the first upsert, because by then the traceback points at array shapes
    instead of at the configuration change that caused it."""


class UnavailableVectorStore:
    """Stands in for a VectorStore that could not be opened at all.

    Every method raises the original connection error. That is the point: the
    archivist already knows how to handle a dedup lookup it cannot complete —
    it sets the nugget aside and its batch is left queued — and routing a
    dead-at-startup Qdrant through that same path means one behaviour for
    "the vector store is not answering" instead of two.

    The alternative, letting the constructor's exception escape, ends the run
    before a single batch is read. That is how it behaved when a scheduled
    treat met a stopped Qdrant: nothing archived, nothing explained, and the
    queue no shorter.

    NOT a general fallback. `reembed` and the console's search want the hard
    failure, because for them an unreachable store makes the operation
    meaningless rather than merely deferred.
    """

    def __init__(self, exc: Exception):
        self._exc = exc

    def search_scored(self, text, limit: int = 5):
        raise self._exc

    def search(self, text, threshold):
        raise self._exc

    def upsert(self, unique_key: str, text: str, payload: dict) -> None:
        raise self._exc

    def upsert_many(self, items) -> int:
        raise self._exc

    def search_scored_many(self, texts, limit: int = 5):
        raise self._exc

    def count(self) -> int:
        raise self._exc


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
        return cls(QdrantClient(url=url, timeout=TIMEOUT_S), embed or FakeEmbedding(),
                   force_fail=force_fail, collection=collection)

    @staticmethod
    def count_points_at(url: str, collection: str = COLLECTION) -> int:
        """§33 #4 parity helper. Missing collection counts as 0; connection
        failures raise so the caller can report them."""
        client = QdrantClient(url=url, timeout=TIMEOUT_S)
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
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=INDEXING_THRESHOLD
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
            info = self.client.get_collection(self.collection)
        except Exception:  # noqa: BLE001 - an unreadable config is not fatal here
            return
        have = info.config.params.vectors.size
        want = int(self.embed.dim)
        if have is not None and int(have) != want:
            raise VectorDimensionMismatch(
                f"collection {self.collection!r} was built for {have}-dimension "
                f"vectors but the current embedding backend produces {want}. "
                "Vectors of two widths cannot share a collection: delete the "
                "old store and re-embed (`jester reembed`), or point "
                "JESTER_QDRANT_URL at a different server."
            )
        self._fix_indexing_threshold(info)

    def _fix_indexing_threshold(self, info) -> None:
        """Bring an existing collection to INDEXING_THRESHOLD (DD-M5).

        The number below was only ever passed to `create_collection`, so every
        collection made before it shipped still holds Qdrant's own default --
        20,000 points per segment before an HNSW index is built. A corpus
        under that is scanned brute force on every dedup lookup, which is the
        83 ms median that grows with the archive.

        Only a store sitting ABOVE the threshold is moved: whoever tuned it
        lower is not being overruled by an open call.
        """
        try:
            have = info.config.optimizer_config.indexing_threshold
            if have is not None and int(have) <= INDEXING_THRESHOLD:
                return
            self.client.update_collection(
                self.collection,
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=INDEXING_THRESHOLD),
            )
        except Exception:  # noqa: BLE001 - a store that will not tell or take still answers queries
            return

    def upsert(self, unique_key: str, text: str, payload: dict) -> None:
        if self.force_fail:
            raise RuntimeError("simulated Qdrant upsert failure")
        vec = self.embed.embed([text])[0]
        _with_retry(
            lambda: self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=point_id_for(unique_key),
                        vector=vec,
                        payload={**payload, "unique_key": unique_key},
                    )
                ],
            )
        )

    def upsert_many(self, items) -> int:
        """One embed call + one upsert call for many points (F-06).

        `items` is [(unique_key, text, payload), ...]. Returns the point
        count. Raises on ANY failure — the caller (reembed) falls back to
        per-item upserts so one bad row cannot fail a whole chunk. Deliberately
        NOT used by the archivist: its per-nugget loop is a dedup decision
        stream where each search must see the previous upserts, and batching
        that would change what counts as a duplicate.
        """
        items = list(items)
        if not items:
            return 0
        if self.force_fail:
            raise RuntimeError("simulated Qdrant upsert failure")
        vecs = self.embed.embed([text for _, text, _ in items])
        if len(vecs) != len(items):
            raise RuntimeError(
                f"embedder returned {len(vecs)} vectors for {len(items)} texts")
        _with_retry(
            lambda: self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=point_id_for(key),
                        vector=vec,
                        payload={**payload, "unique_key": key},
                    )
                    for (key, _text, payload), vec in zip(items, vecs)
                ],
            )
        )
        return len(items)

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
        resp = _with_retry(
            lambda: self.client.query_points(self.collection, query=vec, limit=limit)
        )
        return [(p.score, p.payload.get("unique_key")) for p in resp.points]

    def search_scored_many(self, texts, limit: int = 5):
        """One embed call + one batch query for many lookups (F-06).

        Returns [[(score, unique_key), ...], ...] aligned with `texts`.
        Falls back to per-text queries when the client predates
        query_batch_points; raises like search_scored on transport failure.
        """
        texts = list(texts)
        if not texts:
            return []
        vecs = self.embed.embed(texts)
        batch_points = getattr(self.client, "query_batch_points", None)

        def _do():
            if callable(batch_points):
                from qdrant_client import models as _models
                resp = self.client.query_batch_points(
                    self.collection,
                    requests=[_models.QueryRequest(query=v, limit=limit,
                                                   with_payload=True)
                              for v in vecs],
                )
                out = []
                for r in resp:
                    pts = r.points if hasattr(r, "points") else r
                    out.append([(p.score, p.payload.get("unique_key"))
                                for p in pts])
                return out
            return [[(p.score, p.payload.get("unique_key")) for p in
                     self.client.query_points(
                         self.collection, query=v, limit=limit).points]
                    for v in vecs]

        return _with_retry(_do)
