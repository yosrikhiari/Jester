"""Semantic regrouping: chunk -> embed -> index -> cluster -> label -> idea.

The rule these pin down hardest is the refusal. `FakeEmbedding` is 32
dimensions of SHA-256: clustering it yields groups that are indistinguishable
from real ones — confident labels, plausible sizes, total noise. Producing
those silently would be the most expensive kind of wrong, so the pipeline
refuses, and the refusal is tested like a feature because it is one.
"""

import json
import math
import random

import pytest

from jester.agents.labeller import FakeLabeller, keyword_label
from jester.chunking import MAX_CHARS, chunk_nugget, chunk_text
from jester.clustering import (
    DEFAULT_MIN_NUGGETS,
    FakeEmbeddingRefused,
    brute_force_neighbours,
    build_clusters,
    cluster_stats,
    cluster_vectors,
    is_fake_embedding,
)
from jester.embed import FakeEmbedding
from jester.models import Nugget
from jester.store import (
    insert_cluster_idea,
    insert_nugget,
    list_cluster_ideas,
    list_clusters,
    open_db,
    promote_cluster_idea,
    replace_clusters,
)


# ---- chunking -------------------------------------------------------------


def test_short_text_is_one_chunk():
    """The common case: nuggets average ~359 characters and must not pay for
    a splitter that has nothing to split."""
    got = chunk_text("My backup script breaks on every upgrade.", "k")
    assert len(got) == 1
    assert got[0].index == 0
    assert got[0].point_key == "k#0"


def test_long_text_splits_and_stays_within_budget():
    text = " ".join(f"Sentence number {i} about a distinct problem." for i in range(80))
    got = chunk_text(text, "k")
    assert len(got) > 1
    assert all(len(c.text) <= MAX_CHARS + 200 for c in got), \
        "overlap must not blow the budget wide open"
    assert [c.index for c in got] == list(range(len(got)))


def test_a_runt_tail_is_folded_back():
    """A 30-character tail is not a theme, but it will happily anchor a
    cluster of other 30-character tails."""
    body = "x" * (MAX_CHARS + 40)
    got = chunk_text(body, "k")
    assert all(len(c.text) >= 100 for c in got), [len(c.text) for c in got]


def test_wall_of_text_with_no_punctuation_still_splits():
    got = chunk_text("word " * 900, "k")
    assert len(got) > 1


def test_chunk_nugget_embeds_the_insight_alongside_the_raw_text():
    """Raw text alone lets shared vocabulary pull unrelated complaints
    together; the insight is the statement of the problem."""
    row = {"unique_key": "k", "raw_text": "docker ate my disk again",
           "extracted_insight": "disk fills silently"}
    got = chunk_nugget(row)
    assert "disk fills silently" in got[0].text
    assert "docker ate my disk" in got[0].text


def test_chunk_nugget_does_not_duplicate_a_repeated_insight():
    row = {"unique_key": "k", "raw_text": "disk fills silently and nobody warns you",
           "extracted_insight": "disk fills silently"}
    text = chunk_nugget(row)[0].text
    assert text.count("disk fills silently") == 1


# ---- the refusal ----------------------------------------------------------


def test_fake_embeddings_are_detected():
    assert is_fake_embedding(FakeEmbedding()) is True

    class Real:
        dim = 768

    assert is_fake_embedding(Real()) is False


def test_clustering_refuses_hash_vectors():
    """The whole point. Hash vectors carry no meaning, so two nuggets about
    the same problem are exactly as far apart as two unrelated ones."""
    rows = [{"unique_key": f"k{i}", "raw_text": f"a problem about {i}",
             "extracted_insight": ""} for i in range(10)]
    with pytest.raises(FakeEmbeddingRefused) as exc:
        build_clusters(rows, FakeEmbedding())
    # The message has to name the knob, not just the complaint.
    assert "embedding_provider" in str(exc.value)


def test_allow_fake_is_available_for_tests_only():
    rows = [{"unique_key": f"k{i}", "raw_text": f"text {i}", "extracted_insight": ""}
            for i in range(10)]
    build_clusters(rows, FakeEmbedding(), allow_fake=True)  # must not raise


# ---- the clustering rule --------------------------------------------------


def _planted(groups=4, per=10, dim=64, jitter=0.02, seed=3):
    """Vectors with deliberate structure: `groups` tight blobs."""
    rnd = random.Random(seed)
    out = []
    for g in range(groups):
        for _ in range(per):
            v = [rnd.gauss(0, jitter) for _ in range(dim)]
            for i in range(g * 8, g * 8 + 6):
                v[i % dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
    return out


def test_planted_groups_are_recovered():
    vs = _planted(groups=4, per=10)
    got = cluster_vectors([str(i) for i in range(len(vs))], vs, threshold=0.74)
    assert len(got) == 4
    assert sorted(len(g) for g in got) == [10, 10, 10, 10]


def test_a_bridge_chunk_does_not_weld_two_subjects_together():
    """The failure mode the whole design is aimed at.

    One chunk sitting between two subjects links to both, and connected
    components then report them as one big important-looking theme. Mutual
    k-NN alone does NOT stop this when k is larger than the groups (here k=12
    against two 8-point blobs) — the bridge is comfortably inside both top-k
    lists. `split_incoherent` is what catches it, by noticing the merged
    component is both oversized and loose.

    Not hypothetical: the first full pass over the real archive produced blobs
    of 283 and 261 nuggets at coherence 0.73/0.77 against a median of 0.90.
    """
    a = _planted(groups=1, per=8, seed=1)
    b = _planted(groups=1, per=8, seed=2)
    b = [[x for x in v[32:]] + [x for x in v[:32]] for v in b]  # rotate away
    bridge = [(x + y) / 2 for x, y in zip(a[0], b[0])]
    n = math.sqrt(sum(x * x for x in bridge)) or 1.0
    vs = a + b + [[x / n for x in bridge]]
    got = cluster_vectors([str(i) for i in range(len(vs))], vs, threshold=0.60)
    # The bridge must not merge the two blobs into one.
    assert all(len(g) <= len(a) + 1 for g in got), [len(g) for g in got]


def test_a_large_but_tight_theme_is_not_split():
    """Splitting on size alone would shred the best result the pipeline can
    produce, so `split_incoherent` requires looseness too.

    Membership is asserted as "nearly all", not "all": with k=12 against a
    40-point group, each point's top-k is an arbitrary twelve of thirty-nine
    near-identical candidates, so mutuality fails for a couple at the edge.
    That is a known and acceptable cost of mutual k-NN — what must NOT happen
    is the group coming apart into pieces.
    """
    vs = _planted(groups=1, per=40, jitter=0.005)
    got = cluster_vectors([str(i) for i in range(len(vs))], vs, threshold=0.74)
    assert len(got) == 1, f"a tight theme was split into {len(got)} pieces"
    assert len(got[0]) >= 36, len(got[0])


def test_a_lone_vector_is_not_a_cluster():
    assert cluster_vectors(["a"], [[1.0, 0.0]], threshold=0.5) == []


def test_min_size_drops_noise():
    vs = _planted(groups=3, per=4)
    assert cluster_vectors([str(i) for i in range(len(vs))], vs,
                           threshold=0.74, min_size=9) == []


def test_min_nuggets_counts_nuggets_not_chunks():
    """Chunks are an implementation detail. A long post split three ways
    trivially clears min_size on its own — observed live, two of seven
    clusters on the first real pass were 3 chunks from only 2 nuggets."""
    # One nugget long enough to make several chunks, all near-identical.
    body = ("The upgrade wiped my backup cron and nothing warned me. " * 40)
    rows = [{"unique_key": "solo", "raw_text": body, "extracted_insight": ""}]

    class Same:
        dim = 768

        def embed(self, texts):
            return [[1.0] + [0.0] * 767 for _ in texts]

    got = build_clusters(rows, Same(), threshold=0.5, min_size=2,
                         min_nuggets=DEFAULT_MIN_NUGGETS)
    assert got == [], "one post split into chunks is not a theme"


def test_neighbour_seam_defaults_to_brute_force_and_accepts_an_index():
    vs = _planted(groups=2, per=6)
    exact = cluster_vectors([str(i) for i in range(len(vs))], vs, threshold=0.74)
    injected = cluster_vectors(
        [str(i) for i in range(len(vs))], vs, threshold=0.74,
        neighbour_fn=lambda v, t, k: brute_force_neighbours(v, t, k),
    )
    assert exact == injected


# ---- provenance -----------------------------------------------------------


def test_unknown_authors_are_not_counted_as_zero_authors():
    """'0 authors' and '3 authors we never captured' are different findings:
    most of this archive predates the author capture."""
    from jester.clustering import Cluster, ClusterMember

    c = Cluster(members=[ClusterMember("a", 0, "x"), ClusterMember("b", 0, "y")])
    stats = cluster_stats(c, {"a": {"author": ""}, "b": {"author": ""}})
    assert stats["n_authors"] == 0
    assert stats["authors_unknown"] == 2
    # The flag must not fire on missing data — it would be an accusation.
    assert stats["single_author"] is False

    stats = cluster_stats(c, {"a": {"author": "alice"}, "b": {"author": "alice"}})
    assert stats["n_authors"] == 1
    assert stats["single_author"] is True


def test_stats_report_platform_and_thread_spread():
    from jester.clustering import Cluster, ClusterMember

    c = Cluster(members=[ClusterMember("a", 0, "x"), ClusterMember("b", 0, "y")])
    stats = cluster_stats(c, {
        "a": {"platform": "reddit", "thread_id": "t1", "author": "x"},
        "b": {"platform": "hackernews", "thread_id": "t2", "author": "y"},
    })
    assert stats["platforms"] == ["hackernews", "reddit"]
    assert stats["n_threads"] == 2
    assert stats["single_thread"] is False


# ---- labelling ------------------------------------------------------------


def test_keyword_label_skips_filler():
    got = keyword_label(["I think that the backup really just breaks a lot",
                         "the backup breaks and I think it is a thing"])
    assert "backup" in got
    for filler in ("think", "that", "just", "thing"):
        assert filler not in got


def test_fake_labeller_is_obviously_mechanical():
    """A fabricated label on an unreachable model is worse than a plainly
    machine-made one: the first reads exactly like a real answer."""
    got = FakeLabeller().label(["backups break on upgrade", "upgrade broke backups"])
    assert "·" in got.label or got.label == "unnamed theme"


# ---- drafts and promotion -------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return open_db(str(tmp_path / "c.db"))


def _seed_cluster(db):
    for k in ("n1", "n2", "n3"):
        insert_nugget(db, Nugget(unique_key=k, platform="reddit", thread_id="t1",
                                 raw_text=f"body {k}", extracted_insight=f"i {k}"))
    replace_clusters(db, "r", [{
        "label": "Backups break on upgrade", "problem_statement": "ps",
        "size": 3, "n_nuggets": 3, "n_authors": 3, "authors_unknown": 0,
        "n_threads": 2, "platforms": '["reddit"]',
        "nugget_keys": json.dumps(["n1", "n2", "n3"]),
        "coherence": 0.85, "embedding_model": "nomic-embed-text",
        "single_author": False, "single_thread": False,
        "members": [{"nugget_key": "n1", "chunk_index": 0, "text": "x", "similarity": 0.9}],
    }])
    return list_clusters(db)[0]["id"]


def _draft(cid):
    from jester.models import Idea, IdeaScores

    return Idea(title="Backup Sentinel", problem_statement="p",
                proposed_solution="s", supporting_nuggets=["n1", "n2", "n3"],
                scores=IdeaScores(demand_signal=8, feasibility=6,
                                  competition=None, overall=7.0),
                synthesis_model="test-model")


def test_a_draft_does_not_reach_the_ideas_archive(db):
    cid = _seed_cluster(db)
    insert_cluster_idea(db, cid, _draft(cid))
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0, \
        "drafts are cheap and mostly discarded; the archive is not a scratchpad"
    assert len(list_cluster_ideas(db, cid)) == 1


def test_promoting_a_draft_archives_it_once(db):
    cid = _seed_cluster(db)
    draft_id = insert_cluster_idea(db, cid, _draft(cid))

    res = promote_cluster_idea(db, draft_id)
    assert res["ok"] is True
    row = db.execute("SELECT * FROM ideas WHERE id=?", (res["idea_id"],)).fetchone()
    assert row is not None
    assert json.loads(row["supporting_nuggets"]) == ["n1", "n2", "n3"]
    # Provenance: which cluster this came from, so an archived idea can be
    # traced back to the theme that produced it.
    assert row["run_id"] == f"cluster-{cid}"
    # competition stays NULL when the critic did not verify it (R29).
    assert row["competition"] is None

    # A double-click on Save is far likelier than a wish for two copies.
    again = promote_cluster_idea(db, draft_id)
    assert again["ok"] is False
    assert "already saved" in again["error"]
    assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 1


def test_replacing_clusters_keeps_drafts(db):
    """Clusters are derived and disposable; a draft is somebody's work."""
    cid = _seed_cluster(db)
    insert_cluster_idea(db, cid, _draft(cid))
    _seed_cluster(db)  # a fresh pass wipes and rewrites the clusters
    assert len(list_cluster_ideas(db)) == 1


# ---- qdrant wiring --------------------------------------------------------


def test_clustering_reaches_qdrant_without_an_env_var(monkeypatch):
    """JESTER_QDRANT_URL is unset on this machine and in the scheduled
    launcher. Keying the index off it alone meant the vector database ran in
    the next container over, unused, while the pass went quadratic."""
    from jester import clustering

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    seen = {}

    class Client:
        def __init__(self, url, timeout=None):
            seen["url"] = url

        def get_collections(self):
            return None

    monkeypatch.setattr("qdrant_client.QdrantClient", Client)
    # A real pass needs an embedder; this only checks which URL is dialled.
    assert clustering.DEFAULT_QDRANT_URL == "http://127.0.0.1:6333"
    Client(clustering.DEFAULT_QDRANT_URL)
    assert seen["url"] == "http://127.0.0.1:6333"


def test_a_dimension_mismatch_is_refused_at_open_time(tmp_path):
    """Every local store on this machine was created 32-wide by FakeEmbedding.
    Switching embedding_provider to ollama (768) used to die inside numpy with
    'could not broadcast input array from shape (768,) into shape (32,)' — an
    error naming neither the config key nor the store to rebuild."""
    from jester.embed import FakeEmbedding
    from jester.vector import VectorDimensionMismatch, VectorStore

    path = str(tmp_path / "v.qdrant")
    store = VectorStore.open(path, FakeEmbedding())
    store.upsert("k1", "hello", {})
    store.client.close()

    class Real768:
        dim = 768

        def embed(self, texts):
            return [[0.1] * 768 for _ in texts]

    with pytest.raises(VectorDimensionMismatch) as exc:
        VectorStore.open(path, Real768())
    msg = str(exc.value)
    assert "32" in msg and "768" in msg
    assert "reembed" in msg, "the error must name the way out"


def test_collections_are_namespaced_per_database():
    """A shared Qdrant server has no notion of which database a vector belongs
    to. Before this, every database wrote into one `nuggets` collection: a
    cycle run against a throwaway --db put 240 vectors into the production
    dedup space, where they stayed and influenced real runs."""
    from jester.cli import collection_for

    main = collection_for("data/jester.db")
    probe = collection_for("/tmp/probe.db")
    assert main != probe, "two databases must not share a collection"
    assert collection_for("data/jester.db") == main, "must be stable"
    # Absolute and relative paths to the same file are the same database.
    import os

    assert collection_for(os.path.abspath("data/jester.db")) == main
    # Ideas get their own space, so idea vectors never pollute the nugget
    # space the archivist dedups against (R44).
    assert collection_for("data/jester.db", "ideas") != main
    # In-memory databases are their own namespace, not a shared bucket.
    assert collection_for(":memory:") == "nuggets__memory"
    # Names must be safe for a collection identifier.
    weird = collection_for("/tmp/My Test DB (2).db")
    assert weird.replace("_", "").isalnum(), weird
