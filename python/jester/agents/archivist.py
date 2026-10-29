"""M1.5 archivist agent: dedup + vector embedding + corpus-model guard (R32)."""
from typing import List

from jester.config import Thresholds
from jester.models import Nugget
from jester.store import exists_unique_key, insert_nugget, meta_get, meta_set, open_db
from jester.vector import VectorStore, point_id_for


class ModelMismatch(RuntimeError):
    """R32: corpus embedding model differs from the configured model."""


#: How far below the bar actually applied the near-miss band reaches. The
#: DEFAULT threshold is 0.87 and the band has always opened at 0.85, so the
#: width that preserves every recorded judgement is 0.87 - 0.85 = 0.02.
#:
#: The band is derived from the threshold rather than left at a bare 0.85
#: because the bar is no longer one number (config dedup_threshold_by_category).
#: At a 0.80 bar a fixed 0.85 floor is unreachable — everything at or above it
#: was already DUPLICATE — so a category with its own lower bar would lose its
#: near-miss strip entirely; at a 0.95 bar the fixed floor widens the band into
#: "anything vaguely related". A constant WIDTH under whatever bar was applied
#: keeps the strip a strip. At the global 0.87 the arithmetic lands on exactly
#: 0.85, which is why no recorded behaviour changed.
NEAR_MISS_WIDTH = 0.02


def classify_similarity(similarity: float, threshold: float) -> str:
    if similarity >= threshold:
        return "DUPLICATE"
    if similarity >= threshold - NEAR_MISS_WIDTH:
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

    def dedup_threshold_for(self, n: Nugget) -> float:
        """The cosine bar THIS nugget is judged against: its category's value
        when `dedup_threshold_by_category` declares one, else the global
        `dedup_threshold`. An absent category or an empty map keeps the
        global behaviour exactly."""
        by_category = self.config.dedup_threshold_by_category or {}
        value = by_category.get(n.category)
        return float(value) if value is not None else self.config.dedup_threshold

    def run(self, nuggets: List[Nugget]) -> List[Nugget]:
        from jester.roles import embedding_model_of

        configured = embedding_model_of(self.config)
        corpus = meta_get(self.db, "corpus_embedding_model")
        if corpus is not None and corpus != configured:
            raise ModelMismatch(
                f"corpus embedded with {corpus!r}, config requires {configured!r}"
            )
        if corpus is None:
            meta_set(self.db, "corpus_embedding_model", configured)

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
            # The bar for this nugget's category, not the global one.
            threshold = self.dedup_threshold_for(n)
            # A match whose payload key is the candidate's OWN unique_key is
            # the nugget's reflection of itself, not another author's voice.
            # It only exists when an earlier pass wrote the point but died
            # before the insert_nugget below landed — a crash between those
            # two lines is a real, silent window. Refusing on it (the old
            # behaviour) loses the nugget forever: the batch is marked
            # processed, the reflection answers every future lookup, and no
            # row exists anywhere. Its score is excluded from NEAR_MISS
            # tracking below too — a reflection is not another idea nearly
            # matching.
            neighbours = [(s, k) for s, k in scored if k != n.unique_key]
            over = [(s, k) for s, k in neighbours if s >= threshold]
            # A neighbour that clears the bar should name a row in the
            # nuggets table — that row IS the archive's evidence the text
            # was kept. When EVERY over-threshold key resolves to no row, the
            # matches are ghosts of earlier vector writes whose insert never
            # landed (the crash window above, or a row pruned after its point
            # was written). Refusing on a ghost archives nothing and loses a
            # text that exists nowhere, so the guard decides on PRESENCE: one
            # real row behind a match and the duplicate is real, refuse as
            # always; all ghosts and the nugget proceeds anyway. There the
            # fall-through (no `continue`) IS the treatment: upsert + insert
            # run as for any new nugget, upsert is idempotent (point_id_for
            # is a pure function of the key) so re-writing a point a ghost
            # occupies costs one call, and the embedding_id stamped below
            # points at that existing point.
            if over:
                if any(exists_unique_key(self.db, k) for _s, k in over):
                    continue  # semantic duplicate -> refuse/merge
                # (else: all ghosts — proceed to upsert + insert below).
            for s, _k in neighbours:
                if classify_similarity(s, threshold) == "NEAR_MISS":
                    self.near_misses.append(s)
            try:
                self.vector.upsert(n.unique_key, n.raw_text, {"category": n.category})
                n.embedding_id = f"emb:{point_id_for(n.unique_key)}"
                n.needs_reembed = False
            except Exception:
                n.embedding_id = None
                n.needs_reembed = True
            insert_nugget(self.db, n)
            kept.append(n)
        self.near_misses = sorted(self.near_misses, reverse=True)[:3]
        return kept
