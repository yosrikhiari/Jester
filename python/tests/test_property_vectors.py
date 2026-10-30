"""DD-M4: putting property vectors into the store, and the CLI that does it.

`semantic_similarity` reads a Qdrant collection called `properties__jester`.
Before this module, nothing in the codebase ever WROTE to it - the archivist
writes nuggets, `reembed` writes nuggets, the clustering pass writes chunks -
so the property index was empty in every deployment, the reader could only ever
say "not found", and the semantic layer of the 4-layer dedup rule was dead while
looking alive.

The writer keys its points with the same `listing_key` the reader looks up, so
the two halves cannot drift apart unnoticed; and it batches, because a real
archive is thousands of listings and one round trip each is not a sync.
"""
import json
import sqlite3

import pytest

from jester.agents.property_dedup import (
    listing_key,
    load_listing_from_obs,
    sync_property_vectors,
)


def _archive(tmp_path, named="a.db"):
    db = sqlite3.connect(tmp_path / named)
    db.executescript(
        """
        CREATE TABLE listing (portal TEXT, listing_id TEXT, url TEXT,
                              first_seen_at TEXT, last_seen_at TEXT);
        CREATE TABLE listing_observation (id INTEGER PRIMARY KEY, portal TEXT,
                              listing_id TEXT, observed_at TEXT, run_id TEXT,
                              content_hash TEXT, gallery_hash TEXT, price INTEGER,
                              currency TEXT, status TEXT, payload TEXT);
        CREATE TABLE listing_media (observation_id INTEGER, position INTEGER,
                              url TEXT, phash TEXT);
        """)
    return db


def add(db, portal, lid, price, payload, currency="TND"):
    db.execute("INSERT INTO listing VALUES (?,?,?,?,?)",
               (portal, lid, f"https://{portal}.test/{lid}", "", ""))
    db.execute(
        "INSERT INTO listing_observation (portal, listing_id, price, currency, payload)"
        " VALUES (?,?,?,?,?)", (portal, lid, price, currency, json.dumps(payload)))
    db.commit()


class _Store:
    """Records what was written; the batch path and the per-item path are both
    visible, so a test can tell which one ran."""

    def __init__(self, fail_batches=False, fail_items=False):
        self.batches = []
        self.singles = []
        self.fail_batches = fail_batches
        self.fail_items = fail_items

    def upsert_many(self, items):
        if self.fail_batches:
            raise RuntimeError("batch write refused")
        self.batches.append(list(items))
        return len(items)

    def upsert(self, key, text, payload):
        if self.fail_items:
            raise RuntimeError("write refused")
        self.singles.append((key, text, payload))


class TestTheWrite:
    def test_every_listing_with_text_becomes_one_point(self, tmp_path):
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa", "city": "Ariana"})
        add(db, "mubawab", "mu1", 995000, {"title": "Villa", "city": "Ariana"})
        store = _Store()

        out = sync_property_vectors(db, store)

        assert (out["scanned"], out["synced"], out["failed"]) == (2, 2, 0)
        written = [item for batch in store.batches for item in batch]
        assert {k for k, _t, _p in written} == {"tayara/ta1", "mubawab/mu1"}

    def test_one_portal_can_be_synced_without_the_others(self, tmp_path):
        """A portal that changed its description template is re-embedded alone;
        the archive does not have to be rewritten to fix one corner of it."""
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa"})
        add(db, "mubawab", "mu1", 995000, {"title": "Villa"})
        store = _Store()

        out = sync_property_vectors(db, store, portal="tayara")

        assert out["synced"] == 1
        assert store.batches[0][0][0] == "tayara/ta1"

    def test_the_key_is_the_one_the_reader_looks_up(self, tmp_path):
        """The writer and the reader are separate functions; if the key shapes
        drift, every pair silently becomes unknown again."""
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa"})
        store = _Store()

        sync_property_vectors(db, store)

        key = store.batches[0][0][0]
        assert key == listing_key("tayara", "ta1")

    def test_the_payload_carries_what_a_human_needs_to_trace_it(self, tmp_path):
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa", "city": "Ariana"})
        store = _Store()

        sync_property_vectors(db, store)

        _key, _text, payload = store.batches[0][0]
        assert payload["portal"] == "tayara"
        assert payload["listing_id"] == "ta1"
        assert payload["city"] == "Ariana"

    def test_a_listing_with_nothing_to_embed_is_skipped_not_written(self, tmp_path):
        """An empty text embeds to a vector of nothing, and a point made of
        nothing is closer to every other point of nothing than to anything
        real. Skipping keeps it in the honest "unknown" answer instead."""
        db = _archive(tmp_path)
        add(db, "tayara", "quiet", 995000, {})
        add(db, "mubawab", "loud", 995000, {"title": "Villa", "city": "Ariana"})
        store = _Store()

        out = sync_property_vectors(db, store)

        assert (out["synced"], out["skipped"]) == (1, 1)
        assert [k for k, _t, _p in store.batches[0]] == ["mubawab/loud"]

    def test_only_the_latest_observation_of_a_listing_is_embedded(self, tmp_path):
        """A listing is re-observed every pass; 1000 listings re-seen 30 times
        would write 30000 points for the same properties and the search would
        answer with the same listing over and over."""
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Old description"})
        add(db, "tayara", "ta1", 995000, {"title": "New description"})
        store = _Store()

        out = sync_property_vectors(db, store)

        assert out["synced"] == 1
        assert store.batches[0][0][1].startswith("New description")

    def test_one_batch_write_per_chunk_not_one_per_listing(self, tmp_path):
        db = _archive(tmp_path)
        for i in range(150):
            add(db, "tayara", f"t{i}", 995000, {"title": f"Villa {i}"})
        store = _Store()

        out = sync_property_vectors(db, store)

        assert out["synced"] == 150
        assert len(store.batches) > 1, "one round trip per listing is not a sync"
        assert all(len(b) <= 64 for b in store.batches)

    def test_a_failing_batch_falls_back_and_a_failing_item_is_counted(self, tmp_path):
        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa"})
        store = _Store(fail_batches=True, fail_items=True)

        out = sync_property_vectors(db, store)

        assert out["failed"] == 1 and out["synced"] == 0
        assert "write refused" in out["errors"][0]

    def test_a_store_that_never_answered_is_reported_not_raised(self, tmp_path):
        """The console and the treat both call this; a dead Qdrant must not
        turn into a traceback in the middle of a run."""
        class _Dead:
            def upsert_many(self, items):
                raise RuntimeError("qdrant is down")

            def upsert(self, *a):
                raise RuntimeError("qdrant is down")

        db = _archive(tmp_path)
        add(db, "tayara", "ta1", 995000, {"title": "Villa"})

        out = sync_property_vectors(db, _Dead())

        assert out["failed"] == 1
        assert any("qdrant is down" in e for e in out["errors"])

    def test_an_archive_with_no_listing_tables_is_empty_not_an_error(self, tmp_path):
        """The listing tables belong to the Go collector. A Python-only
        database that never saw one has nothing to sync, which is a reportable
        fact and not a crash."""
        db = sqlite3.connect(tmp_path / "bare.db")
        db.execute("CREATE TABLE other (id INTEGER)")
        db.commit()

        out = sync_property_vectors(db, _Store())

        assert out["scanned"] == 0 and out["synced"] == 0


class TestAgainstARealStore:
    def test_a_synced_index_turns_the_unknown_answer_into_a_score(self, tmp_path):
        """End to end, with the real VectorStore: sync, then the reader finds
        the candidate's vector and produces a number instead of a shrug."""
        from jester.agents.property_dedup import check_duplicate
        from jester.embed import FakeEmbedding
        from jester.vector import VectorStore

        db = _archive(tmp_path, "real.db")
        add(db, "tayara", "ta1", 995000,
            {"title": "1 bed apartment", "city": "Zonnebloem", "bedrooms": "1"})
        add(db, "mubawab", "mu1", 995000,
            {"title": "One bedroom flat", "city": "Zonnebloem", "bedrooms": "1"})
        store = VectorStore.in_memory(FakeEmbedding(), collection="properties__jester")

        out = sync_property_vectors(db, store)
        assert out["synced"] == 2 and store.count() == 2

        result = check_duplicate(db, store, "tayara", "ta1")

        assert result.semantic_score is not None, \
            "the index answered, so the pair was actually compared"
        assert not any("structured-only" in r for r in result.reasons)
        assert any("semantic score" in r for r in result.reasons)

    def test_the_stored_vector_is_retrievable_by_key(self, tmp_path):
        from jester.embed import FakeEmbedding
        from jester.vector import VectorStore

        db = _archive(tmp_path, "v.db")
        add(db, "tayara", "ta1", 995000, {"title": "Villa", "city": "Ariana"})
        store = VectorStore.in_memory(FakeEmbedding(), collection="properties__jester")

        sync_property_vectors(db, store)

        vec = store.vector_for(listing_key("tayara", "ta1"))
        assert vec is not None and len(vec) == FakeEmbedding.dim

    def test_an_unkeyed_lookup_is_none_not_an_error(self, tmp_path):
        from jester.embed import FakeEmbedding
        from jester.vector import VectorStore

        store = VectorStore.in_memory(FakeEmbedding(), collection="properties__jester")
        assert store.vector_for("tayara/nope") is None


class TestTheCommand:
    def _ns(self, **kw):
        class NS:
            pass
        ns = NS()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_syncing_needs_no_portal_or_listing(self, tmp_path, monkeypatch, capsys):
        from jester import cli

        db_path = tmp_path / "j.db"
        db = _archive(tmp_path, "j.db")
        add(db, "tayara", "ta1", 995000, {"title": "Villa", "city": "Ariana"})
        db.close()

        store = _Store()
        monkeypatch.setattr(cli, "_property_vector_for", lambda cfg, p: store)

        cli.cmd_property_dedup(self._ns(
            db=str(db_path), config="config", portal=None, listing_id=None,
            sync_vectors=True))

        out = capsys.readouterr().out
        assert "synced 1" in out
        assert store.batches and store.batches[0][0][0] == "tayara/ta1"

    def test_a_check_with_no_listing_says_so_instead_of_crashing(self, tmp_path, capsys):
        from jester import cli

        with pytest.raises(SystemExit) as exc:
            cli.cmd_property_dedup(self._ns(
                db=str(tmp_path / "j.db"), config="config", portal=None,
                listing_id=None, sync_vectors=False))

        assert exc.value.code == 1
        assert "portal" in capsys.readouterr().out
