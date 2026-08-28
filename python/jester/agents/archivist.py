"""M1.5 archivist agent: dedup + vector embedding + corpus-model guard (R32)."""
from typing import List

from jester.config import Thresholds
from jester.models import Nugget
from jester.store import exists_unique_key, insert_nugget, meta_get, meta_set, open_db
from jester.vector import VectorStore


class ModelMismatch(RuntimeError):
    """R32: corpus embedding model differs from the configured model."""


def classify_similarity(similarity: float, threshold: float) -> str:
    if similarity >= threshold:
        return "DUPLICATE"
    if similarity >= 0.85:
        return "NEAR_MISS"
    return "DISTINCT"


class Archivist:
    def __init__(self, db, vector: VectorStore, config: Thresholds):
        self.db = db
        self.vector = vector
        self.config = config
        self.near_misses = []  # §37.13: top dedup near-miss scores, reset per run
        #: (nugget, reason) for nuggets whose dedup lookup could not be
        #: completed. See `run` — these are NOT archived, and the caller is
        #: expected to leave their batches queued for a later pass.
        self.deferred = []

    def run(self, nuggets: List[Nugget]) -> List[Nugget]:
        corpus = meta_get(self.db, "corpus_embedding_model")
        if corpus is not None and corpus != self.config.embedding_model:
            raise ModelMismatch(
                f"corpus embedded with {corpus!r}, config requires {self.config.embedding_model!r}"
            )
        if corpus is None:
            meta_set(self.db, "corpus_embedding_model", self.config.embedding_model)

        self.near_misses = []
        self.deferred = []
        kept: List[Nugget] = []
        for n in nuggets:
            if exists_unique_key(self.db, n.unique_key):
                continue
            try:
                scored = self.vector.search_scored(n.raw_text, limit=5)
            except Exception as exc:  # noqa: BLE001 - any transport failure
                # The dedup lookup is the ONE question that cannot be deferred
                # by guessing: without neighbours there is no way to know
                # whether this nugget is already in the corpus. Archiving it
                # anyway risks a duplicate that no later pass will ever find;
                # dropping it loses the material outright.
                #
                # So it is neither — the nugget is set aside and its batch is
                # left queued, exactly as a rate-limited extraction is. The
                # cost is re-extracting one batch later. The cost of the old
                # behaviour was the entire run: this line was unguarded while
                # the upsert twelve lines below was not, so a single 408 from
                # a busy Qdrant unwound a run mid-flight and four consecutive
                # scheduled treats died here having archived nothing.
                self.deferred.append((n, f"{type(exc).__name__}: {exc}"))
                continue
            if any(s >= self.config.dedup_threshold for s, _k in scored):
                continue  # semantic duplicate -> refuse/merge
            for s, _k in scored:
                if classify_similarity(s, self.config.dedup_threshold) == "NEAR_MISS":
                    self.near_misses.append(s)
            try:
                self.vector.upsert(n.unique_key, n.raw_text, {"category": n.category})
                n.embedding_id = f"emb:{abs(hash(n.unique_key)) % (2 ** 63)}"
                n.needs_reembed = False
            except Exception:
                n.embedding_id = None
                n.needs_reembed = True
            insert_nugget(self.db, n)
            kept.append(n)
        self.near_misses = sorted(self.near_misses, reverse=True)[:3]
        return kept
