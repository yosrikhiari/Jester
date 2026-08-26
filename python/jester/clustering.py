"""Group the archive into semantic themes: chunk -> embed -> index -> cluster.

WHAT THIS REPLACES. `synthesizer.run` groups nuggets with
`groupby(platform, thread_id)` — an "idea" is therefore only ever built from
comments that happened to sit in the same thread. Two people describing the
identical pain, one in r/devops and one on Hacker News, never meet. That is not
a clustering bug, it is the absence of clustering: thread id is a fact about
where text was scraped from, not about what it says.

This module groups by MEANING instead, over the vectors Qdrant already holds.

WHY NOT scikit-learn. k-means needs a k nobody can supply (how many distinct
pains are in 1,761 comments?), and HDBSCAN would add a large compiled
dependency to a runtime list this repo has kept at four packages on purpose
(env.py refused python-dotenv over four lines of parsing). The graph method
below needs no k, no new dependency, and runs off the nearest-neighbour queries
the vector store is already built to serve.

WHY MUTUAL k-NN, AND WHERE IT STOPS HELPING. Plain "link anything above the
threshold, take connected components" chains: A resembles B, B resembles C, and
C has nothing to do with A, but all three land in one blob. Requiring the link
to be mutual — B in A's top-k AND A in B's top-k — costs one dictionary and
removes most of it.

Most, not all, and the limit is precise: mutuality only bites when k is SMALLER
than the groups being joined. With k=12 and two eight-point groups, every point
can hold every other in its top-k, so a single midpoint chunk satisfies
mutuality with both sides and welds them. Measured, not theorised — the first
full pass over this archive produced two blobs of 283 and 261 nuggets, and both
had visibly lower coherence (0.73, 0.77) than the median theme (0.90).

Hence `split_incoherent`: any component that is both oversized and loose gets
re-clustered at a raised threshold. Coherence is the signal that a component is
really two subjects sharing a bridge, and it is already being computed.

Nothing here judges whether a theme is any good. `coherence`, `n_authors` and
`n_threads` are recorded so a caller can: a "theme" that is one person posting
five times is an artefact, and it must be visible as one rather than filtered
away silently.
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from jester.chunking import Chunk, chunk_nugget

#: Qdrant collection for chunk vectors. Deliberately NOT `nuggets`: that one is
#: whole-nugget vectors keyed for the dedup path, and writing chunks into it
#: would change what every dedup lookup compares against.
CHUNK_COLLECTION = "chunks"

#: Where the compose stack publishes Qdrant. Used when JESTER_QDRANT_URL is
#: unset, which is the normal state of this repo.
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"

#: Cosine similarity two chunks must reach before they can be linked at all.
#:
#: Measured, not guessed. Swept over 342 real chunks (300 nuggets, this
#: archive, nomic-embed-text), reading clusters / chunks-grouped / largest:
#:
#:     0.68   18    200/342 (58%)   biggest 35   <- blob forming
#:     0.72   16    133/342 (39%)   biggest 20
#:     0.74   14     97/342 (28%)   biggest 15
#:     0.78    7     33/342 (10%)   biggest  8   <- precise but barely covers
#:     0.80    4     15/342 ( 4%)
#:
#: 0.74 keeps the largest theme at a readable size while grouping nearly three
#: times as much of the archive as 0.78 did. Nothing here is universal: it is
#: calibrated to THIS embedding model on THIS corpus, so `--threshold` exists
#: and the summary always reports how much failed to group.
DEFAULT_THRESHOLD = 0.74

#: Neighbours considered per chunk. Larger finds more of a genuinely big theme;
#: it also raises the chance of a spurious mutual link, so it is a cap on how
#: connected any one chunk may be, not a target.
DEFAULT_NEIGHBOURS = 12

#: Clusters with fewer CHUNKS than this are dropped as noise.
DEFAULT_MIN_SIZE = 3

#: …and fewer distinct NUGGETS than this, which is the number that actually
#: matters. Chunks are an implementation detail: a long post split into three
#: chunks trivially clears min_size on its own, so a "theme" could be one
#: person talking to themselves. Observed live on the first real pass —
#: two of seven clusters were 3 chunks drawn from only 2 nuggets.
DEFAULT_MIN_NUGGETS = 3


class FakeEmbeddingRefused(RuntimeError):
    """Raised rather than clustering hash vectors.

    `FakeEmbedding` is 32 dimensions of SHA-256, L2-normalised. It is perfectly
    deterministic and carries no meaning whatsoever: two nuggets about the same
    problem are exactly as far apart as two unrelated ones. Clustering it
    produces groups that are indistinguishable, from the outside, from real
    ones — confident labels, plausible sizes, total noise.

    Refusing is the whole point. Silently producing them would be the most
    expensive kind of wrong: output that looks like insight.
    """


@dataclass
class ClusterMember:
    nugget_key: str
    chunk_index: int
    text: str
    similarity: float = 0.0


@dataclass
class Cluster:
    """One semantic theme."""

    members: List[ClusterMember] = field(default_factory=list)
    centroid: List[float] = field(default_factory=list)
    coherence: float = 0.0
    label: str = ""
    problem_statement: str = ""

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def nugget_keys(self) -> List[str]:
        seen, out = set(), []
        for m in self.members:
            if m.nugget_key not in seen:
                seen.add(m.nugget_key)
                out.append(m.nugget_key)
        return out

    def preview(self, limit: int = 12) -> List[str]:
        """The most central members' text — what a label should be written
        from, and what a reviewer should be shown to check that label."""
        ranked = sorted(self.members, key=lambda m: -m.similarity)
        return [m.text for m in ranked[:limit]]


# ---- vector maths (stdlib) -------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def centroid_of(vectors: Sequence[Sequence[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    n = float(len(vectors))
    return [x / n for x in acc]


# ---- union-find ------------------------------------------------------------


class _Union:
    def __init__(self):
        self.parent: Dict[str, str] = {}

    def add(self, k: str) -> None:
        self.parent.setdefault(k, k)

    def find(self, k: str) -> str:
        self.add(k)
        root = k
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[k] != root:  # path compression
            self.parent[k], k = root, self.parent[k]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = defaultdict(list)
        for k in self.parent:
            out[self.find(k)].append(k)
        return out


# ---- the pipeline ----------------------------------------------------------


def chunk_rows(rows: Iterable) -> List[Chunk]:
    """Every chunk of every nugget row, in archive order."""
    out: List[Chunk] = []
    for r in rows:
        out.extend(chunk_nugget(r))
    return out


def is_fake_embedding(embed) -> bool:
    """True for the hash-based stand-in.

    Checked by class name AND dimensionality rather than identity, so a
    subclass or a test double built on it is caught too. 32 dimensions is not a
    real text-embedding size; nomic-embed-text is 768.
    """
    name = type(embed).__name__.lower()
    if "fake" in name or "stub" in name:
        return True
    return int(getattr(embed, "dim", 0) or 0) <= 64


def embed_chunks(chunks: Sequence[Chunk], embed, batch: int = 64) -> List[List[float]]:
    """Vectors for every chunk, in order.

    Batched because an embedding backend is a network call per request, and
    1,761 nuggets is a few thousand chunks — one call each turns a two-minute
    job into an hour.
    """
    vectors: List[List[float]] = []
    texts = [c.text for c in chunks]
    for i in range(0, len(texts), batch):
        vectors.extend(embed.embed(texts[i : i + batch]))
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedding backend returned {len(vectors)} vectors for "
            f"{len(chunks)} chunks — refusing to align them by guesswork"
        )
    return vectors


def brute_force_neighbours(
    vectors: Sequence[Sequence[float]], threshold: float, k: int
) -> List[List[int]]:
    """Top-k neighbours per point by comparing everything to everything.

    Exact, dependency-free, and QUADRATIC. Measured on 768-dim vectors:

        n=342     8.2s
        n=800    46.4s
        n=2000  293.3s

    Fine for a test or a few hundred chunks; unusable on a real archive, which
    is why `qdrant_neighbours` exists and is what the pipeline actually uses.
    Kept as the reference implementation the indexed path is checked against.
    """
    n = len(vectors)
    tops: List[List[int]] = []
    for i in range(n):
        scored = []
        for j in range(n):
            if i == j:
                continue
            sim = cosine(vectors[i], vectors[j])
            if sim >= threshold:
                scored.append((sim, j))
        scored.sort(reverse=True)
        tops.append([j for _, j in scored[:k]])
    return tops


def qdrant_neighbours(
    client,
    collection: str,
    vectors: Sequence[Sequence[float]],
    threshold: float,
    k: int,
) -> List[List[int]]:
    """Top-k neighbours per point, from Qdrant's HNSW index.

    This is what the vector store is FOR. The brute-force version above was
    doing 4 million pure-Python cosines over 768 dimensions to answer a
    question an indexed nearest-neighbour search answers in log time — on the
    very vectors Qdrant was already holding.

    Points are keyed by their position in `vectors`, so the returned indices
    line up with the caller's chunk list without a lookup table.
    """
    from qdrant_client import models

    dim = len(vectors[0])
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(
        collection,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )
    # Batched: one upsert per point would be thousands of round trips.
    for start in range(0, len(vectors), 256):
        chunk = vectors[start : start + 256]
        client.upsert(
            collection,
            points=[
                models.PointStruct(id=start + off, vector=list(v), payload={})
                for off, v in enumerate(chunk)
            ],
        )

    tops: List[List[int]] = []
    for i, v in enumerate(vectors):
        # k + 1 because the point itself comes back as its own best match.
        resp = client.query_points(
            collection, query=list(v), limit=k + 1, score_threshold=threshold
        )
        tops.append([p.id for p in resp.points if p.id != i][:k])
    return tops


#: A component larger than this fraction of all points is a candidate for
#: splitting. A genuine theme can be large; a theme that is a quarter of the
#: entire archive is two themes holding hands.
DEFAULT_MAX_FRACTION = 0.10

#: …and only split it if it is also loose. A big, TIGHT component is a real
#: theme and must survive: splitting on size alone would shred the best result
#: the pipeline can produce.
DEFAULT_COHERENCE_FLOOR = 0.82


def component_coherence(
    idx: Sequence[int], vectors: Sequence[Sequence[float]]
) -> float:
    """Mean cosine of a component's members to their own centroid."""
    if not idx:
        return 0.0
    cent = centroid_of([vectors[i] for i in idx])
    return sum(cosine(vectors[i], cent) for i in idx) / len(idx)


def split_incoherent(
    groups: List[List[int]],
    vectors: Sequence[Sequence[float]],
    *,
    threshold: float,
    neighbours: int,
    min_size: int,
    total: int,
    max_fraction: float = DEFAULT_MAX_FRACTION,
    coherence_floor: float = DEFAULT_COHERENCE_FLOOR,
    neighbour_fn=None,
    depth: int = 0,
) -> List[List[int]]:
    """Re-cluster oversized, loose components at a tighter threshold.

    Both conditions, never one: size alone would break up a genuinely large
    theme, and looseness alone would chase small noisy groups that min_size
    already handles.

    Depth-capped at 3. A component that will not separate after three
    tightenings is reported as it is rather than ground down — the summary
    carries its coherence, so a reader can see it is a weak grouping instead of
    being handed a confident-looking shard.
    """
    if depth >= 3:
        return groups
    out: List[List[int]] = []
    for g in groups:
        oversized = total > 0 and len(g) / total > max_fraction
        loose = component_coherence(g, vectors) < coherence_floor
        if not (oversized and loose) or len(g) < 2 * min_size:
            out.append(g)
            continue
        sub_vectors = [vectors[i] for i in g]
        tighter = min(0.95, threshold + 0.04)
        sub = cluster_vectors(
            [str(i) for i in range(len(sub_vectors))],
            sub_vectors,
            threshold=tighter,
            neighbours=neighbours,
            min_size=min_size,
            neighbour_fn=neighbour_fn,
            _split=False,
        )
        if not sub:
            # Nothing separated at the tighter threshold; keep the original
            # rather than silently dropping a whole component.
            out.append(g)
            continue
        mapped = [[g[i] for i in part] for part in sub]
        out.extend(split_incoherent(
            mapped, vectors, threshold=tighter, neighbours=neighbours,
            min_size=min_size, total=total, max_fraction=max_fraction,
            coherence_floor=coherence_floor, neighbour_fn=neighbour_fn,
            depth=depth + 1,
        ))
    return out


def cluster_vectors(
    keys: Sequence[str],
    vectors: Sequence[Sequence[float]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    neighbours: int = DEFAULT_NEIGHBOURS,
    min_size: int = DEFAULT_MIN_SIZE,
    neighbour_fn=None,
    _split: bool = True,
) -> List[List[int]]:
    """Group vector indices into themes. Returns lists of indices.

    Pure function over vectors so it is testable without Qdrant, an embedding
    backend, or a database — the clustering RULE is the part most worth pinning
    down, and it should not need three services to check. `neighbour_fn` is the
    seam: brute force by default, Qdrant's index in production.
    """
    n = len(vectors)
    if n < 2:
        return []

    finder = neighbour_fn or (lambda vs, th, k: brute_force_neighbours(vs, th, k))
    tops = finder(vectors, threshold, neighbours)

    # MUTUAL links only — see the module docstring on chaining.
    top_sets = [set(t) for t in tops]
    uf = _Union()
    for i in range(n):
        uf.add(str(i))
    for i in range(n):
        for j in tops[i]:
            if i in top_sets[j]:
                uf.union(str(i), str(j))

    groups = [
        sorted(int(x) for x in members)
        for members in uf.groups().values()
        if len(members) >= min_size
    ]
    if _split:
        groups = split_incoherent(
            groups, vectors, threshold=threshold, neighbours=neighbours,
            min_size=min_size, total=n, neighbour_fn=neighbour_fn,
        )
    # Biggest first: the largest coherent theme is the most useful thing to
    # show, and a stable order makes two runs comparable.
    groups.sort(key=lambda g: (-len(g), g[0]))
    return groups


def build_clusters(
    rows: Sequence,
    embed,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    neighbours: int = DEFAULT_NEIGHBOURS,
    min_size: int = DEFAULT_MIN_SIZE,
    min_nuggets: int = DEFAULT_MIN_NUGGETS,
    allow_fake: bool = False,
    neighbour_fn=None,
) -> List[Cluster]:
    """Chunk, embed and cluster a set of nugget rows.

    `allow_fake` exists ONLY so the test suite can exercise the mechanics
    offline. It must never be set by the console or the CLI: see
    FakeEmbeddingRefused.
    """
    if is_fake_embedding(embed) and not allow_fake:
        raise FakeEmbeddingRefused(
            "refusing to cluster hash-based embeddings: FakeEmbedding is "
            "32 dimensions of SHA-256 and carries no meaning, so the groups "
            "would be noise wearing labels. Set embedding_provider: ollama "
            "in config/thresholds.yaml and run `jester reembed`."
        )

    chunks = chunk_rows(rows)
    if len(chunks) < min_size:
        return []
    vectors = embed_chunks(chunks, embed)
    index_groups = cluster_vectors(
        [c.point_key for c in chunks],
        vectors,
        threshold=threshold,
        neighbours=neighbours,
        min_size=min_size,
        neighbour_fn=neighbour_fn,
    )

    clusters: List[Cluster] = []
    for group in index_groups:
        cent = centroid_of([vectors[i] for i in group])
        members = [
            ClusterMember(
                nugget_key=chunks[i].nugget_key,
                chunk_index=chunks[i].index,
                text=chunks[i].text,
                similarity=round(cosine(vectors[i], cent), 4),
            )
            for i in group
        ]
        members.sort(key=lambda m: -m.similarity)
        coherence = (
            round(sum(m.similarity for m in members) / len(members), 4)
            if members
            else 0.0
        )
        cluster = Cluster(members=members, centroid=cent, coherence=coherence)
        # Measured in nuggets, not chunks: one long post split three ways is
        # not three people agreeing.
        if len(cluster.nugget_keys) < min_nuggets:
            continue
        clusters.append(cluster)
    return clusters


def cluster_stats(cluster: Cluster, rows_by_key: Dict[str, dict]) -> dict:
    """Provenance for one theme.

    `n_authors` is the number that decides whether a theme means anything. Five
    chunks from one prolific commenter cluster beautifully and signify nothing;
    five chunks from five strangers on three platforms is the actual signal the
    whole pipeline exists to find. Reported, never used to filter — that call
    belongs to whoever is reading.
    """
    authors, threads, platforms = set(), set(), set()
    unknown_authors = 0
    for key in cluster.nugget_keys:
        row = rows_by_key.get(key) or {}
        a = (row.get("author") or "").strip()
        if a:
            authors.add(a.lower())
        else:
            # NOT the same as "no author": nuggets archived before the capture
            # was widened have none recorded. Counting those as zero distinct
            # authors would make an unmeasured theme look like a worthless one.
            unknown_authors += 1
        t = (row.get("thread_id") or "").strip()
        if t:
            threads.add(t)
        p = (row.get("platform") or "").strip()
        if p:
            platforms.add(p)
    return {
        "n_nuggets": len(cluster.nugget_keys),
        "n_authors": len(authors),
        "authors_unknown": unknown_authors,
        "n_threads": len(threads),
        "platforms": sorted(platforms),
        # A theme nobody but one person raised, or one that never left a single
        # thread, is exactly what thread-grouping already found. Flagged so the
        # UI can say so — but only when authorship was actually measured;
        # otherwise the flag would be an accusation based on missing data.
        "single_author": bool(authors) and len(authors) <= 1,
        "single_thread": len(threads) <= 1,
    }


def rows_by_key(rows: Iterable) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        try:
            out[r["unique_key"]] = {k: r[k] for k in r.keys()}
        except (AttributeError, TypeError):
            if isinstance(r, dict) and r.get("unique_key"):
                out[r["unique_key"]] = r
    return out


def to_payload(cluster: Cluster, stats: dict, embedding_model: str) -> dict:
    """The row shape the store and the console both read."""
    return {
        "label": cluster.label,
        "problem_statement": cluster.problem_statement,
        "size": cluster.size,
        "coherence": cluster.coherence,
        "embedding_model": embedding_model,
        "nugget_keys": json.dumps(cluster.nugget_keys),
        **stats,
        "platforms": json.dumps(stats.get("platforms") or []),
    }


# ---- orchestration ---------------------------------------------------------


def cluster_archive(
    db,
    cfg,
    *,
    run_id: str = "cluster",
    threshold: float = DEFAULT_THRESHOLD,
    neighbours: int = DEFAULT_NEIGHBOURS,
    min_size: int = DEFAULT_MIN_SIZE,
    min_nuggets: int = DEFAULT_MIN_NUGGETS,
    embed=None,
    labeller=None,
    allow_fake: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """One clustering pass over the archive. Returns a summary dict.

    Kept here rather than in the CLI so the console's button and the command
    line run the SAME code — the lesson from `jester schedule`, which for
    months registered a job whose behaviour existed nowhere else and therefore
    could not be checked.
    """
    from jester.agents.labeller import label_clusters, select_labeller
    from jester.embed import select_embedding
    from jester.store import list_nuggets, replace_clusters

    rows = list_nuggets(db, limit=limit)
    if not rows:
        return {"ok": True, "clusters": 0, "nuggets": 0,
                "detail": "the archive is empty — nothing to cluster"}

    embed = embed or select_embedding(cfg)
    fake = is_fake_embedding(embed)
    if fake and not allow_fake:
        raise FakeEmbeddingRefused(
            "refusing to cluster hash-based embeddings: FakeEmbedding is "
            "32 dimensions of SHA-256 and carries no meaning, so the groups "
            "would be noise wearing labels. Set embedding_provider: ollama "
            "in config/thresholds.yaml and make sure Ollama is reachable."
        )

    # Index the chunk vectors and let Qdrant answer the neighbour queries.
    # Brute force is exact but quadratic (293s at 2,000 vectors, measured), and
    # the archive only grows. Falling back to brute force when no Qdrant is
    # reachable keeps a laptop without the container working, slowly and
    # loudly, rather than failing.
    neighbour_fn = None
    # DEFAULTED, not required. JESTER_QDRANT_URL is unset on this machine and
    # in the scheduled launcher, so keying the index off it alone meant the
    # feature quietly fell back to the quadratic path — the vector database
    # running in the next container over, unused, while a 2,000-chunk pass
    # spent 293s doing by hand what the index does in 22s.
    qdrant_url = os.environ.get("JESTER_QDRANT_URL") or DEFAULT_QDRANT_URL
    backend = "brute force (quadratic)"
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url, timeout=10)
        client.get_collections()  # prove it answers before relying on it
        neighbour_fn = lambda vs, th, k: qdrant_neighbours(  # noqa: E731
            client, CHUNK_COLLECTION, vs, th, k
        )
        backend = f"qdrant {qdrant_url}"
    except Exception as exc:  # noqa: BLE001
        # Loud, not silent: the run still works, just slowly, and the operator
        # should know which of the two it got.
        print(f"[cluster] qdrant at {qdrant_url} unreachable ({exc}); "
              "falling back to brute force — this is quadratic and will crawl "
              "on a large archive")

    clusters = build_clusters(
        rows, embed,
        threshold=threshold, neighbours=neighbours, min_size=min_size,
        min_nuggets=min_nuggets, allow_fake=allow_fake,
        neighbour_fn=neighbour_fn,
    )
    label_clusters(clusters, labeller or select_labeller(cfg))

    by_key = rows_by_key(rows)
    model = getattr(cfg, "embedding_model", "") if not fake else "fake (hash)"
    payloads = []
    for c in clusters:
        stats = cluster_stats(c, by_key)
        payload = to_payload(c, stats, model)
        payload["members"] = [
            {"nugget_key": m.nugget_key, "chunk_index": m.chunk_index,
             "text": m.text, "similarity": m.similarity}
            for m in c.members
        ]
        payloads.append(payload)
    replace_clusters(db, run_id, payloads)

    grouped = sum(len(c.nugget_keys) for c in clusters)
    return {
        "ok": True,
        "clusters": len(clusters),
        "nuggets": len(rows),
        "grouped_nuggets": grouped,
        # What did NOT cluster is as informative as what did: a pass that
        # groups 8% of the archive has told you the threshold is too tight,
        # and a summary that reports only the clusters hides that.
        "ungrouped_nuggets": len(rows) - grouped,
        "embedding_model": model,
        # Which path answered the neighbour queries. A summary that does not
        # say cannot distinguish "the index did this in 20s" from "we brute
        # forced it and got lucky the archive was small".
        "neighbour_backend": backend,
        "threshold": threshold,
        "min_size": min_size,
        "min_nuggets": min_nuggets,
        # Authorship is the strongest signal a theme is real. Say how much of
        # it is actually known, so a reviewer does not read "1 author" off an
        # archive that never recorded any.
        "authors_known": sum(
            1 for r in rows if (r["author"] if "author" in r.keys() else "")
        ),
    }
