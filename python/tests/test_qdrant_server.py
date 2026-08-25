"""Shared Qdrant server tests (§37.17) — probe-gated on /healthz."""
import uuid
import urllib.request
from pathlib import Path

import pytest

from jester.config import Thresholds
from jester.vector import VectorStore


def _server_up():
    try:
        urllib.request.urlopen("http://localhost:6333/healthz", timeout=3)
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _server_up(), reason="Qdrant server not reachable"),
    pytest.mark.live_qdrant,
]


class TinyEmbed:
    """Deterministic 4-d vectors; distinct per text."""

    dim = 4

    def embed(self, texts):
        out = []
        for t in texts:
            v = [float(((hash(t) >> (8 * i)) & 0xFF) - 128) / 100.0 for i in range(self.dim)]
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


def _fresh_store():
    return VectorStore.for_url(
        "http://localhost:6333", embed=TinyEmbed(), collection=f"test_{uuid.uuid4().hex}"
    )


def test_for_url_roundtrip_search_scored():
    store = _fresh_store()
    store.upsert("k1", "alpha body", {"category": "pain_point"})
    hits = store.search_scored("alpha body")
    assert hits and hits[0][1] == "k1"


def test_persistence_across_clients():
    store = _fresh_store()
    store.upsert("keep-me", "unique persistent text xyz", {})
    # A brand-new client instance over the SAME url+collection sees the point.
    again = VectorStore.for_url(
        "http://localhost:6333", embed=TinyEmbed(), collection=store.collection
    )
    hits = again.search_scored("unique persistent text xyz")
    assert any(k == "keep-me" for _s, k in hits)


def test_count_points_at():
    store = _fresh_store()
    store.upsert("a", "one", {})
    store.upsert("b", "two", {})
    assert VectorStore.count_points_at("http://localhost:6333", store.collection) == 2


def test_count_points_missing_collection_is_zero():
    assert VectorStore.count_points_at("http://localhost:6333", f"missing_{uuid.uuid4().hex}") == 0


# --- doctor parity (§33 #4) ---------------------------------------------------

def test_doctor_parity_check_flags_mismatch(tmp_path, monkeypatch):
    from jester.cli import evaluate_doctor
    from jester.store import insert_nugget
    from jester.models import Nugget

    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(unique_key="row1", category="pain_point", extracted_insight="x"))

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr("jester.cli._qdrant_point_count", lambda url, col="nuggets": 7)

    findings = evaluate_doctor(db)
    assert any("parity" in f for f in findings)


def test_doctor_parity_ok_when_counts_match(tmp_path, monkeypatch):
    from jester.cli import evaluate_doctor
    from jester.store import insert_nugget
    from jester.models import Nugget

    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(unique_key="row1", category="pain_point", extracted_insight="x"))

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr("jester.cli._qdrant_point_count", lambda url, col="nuggets": 1)

    assert evaluate_doctor(db) == []


from jester.store import open_db  # noqa: E402


def test_cmd_run_writes_points_to_shared_server(tmp_path, monkeypatch):
    """§37.17 Task 3: with JESTER_QDRANT_URL set, archived nuggets land on the
    server and doctor's parity check passes against them."""
    from jester.cli import cmd_run

    monkeypatch.setenv("JESTER_QDRANT_URL", "http://localhost:6333")
    REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

    class NS:
        pass

    args = NS()
    args.config, args.db, args.run = str(REPO_CONFIG), str(tmp_path / "j.db"), "qdrant-e2e"
    cmd_run(args)

    db = open_db(str(tmp_path / "j.db"))
    rows = db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0]
    assert rows > 0
    assert VectorStore.count_points_at("http://localhost:6333") == rows
