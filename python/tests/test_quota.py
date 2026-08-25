"""R30-pattern budget ledger + dedup near-miss tests (§37.13)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from jester.config import Thresholds
from jester.store import get_runs, open_db, quota_add, quota_used

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

PT = ZoneInfo("America/Los_Angeles")


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: PT day-key math ----------------------------------------------------

def test_day_key_rolls_at_midnight_pt():
    from jester.quota import day_key

    # 2026-01-01 02:00 UTC is still 2025-12-31 in Los Angeles (PST, UTC-8).
    now = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    assert day_key(now) == "2025-12-31"
    # And a PT-local afternoon maps to itself.
    local = datetime(2026, 3, 15, 14, 0, tzinfo=PT)
    assert day_key(local) == "2026-03-15"


# --- Task 2: ledger semantics -----------------------------------------------------

def test_spend_accumulates_and_persists_across_reopen(tmp_path):
    p = tmp_path / "j.db"
    db = open_db(str(p))
    from jester.quota import day_key, spend

    spend(db, "youtube", 3000, daily_budget=8000)
    db.close()
    db = open_db(str(p))  # R30: budget survives process restarts
    remaining = spend(db, "youtube", 2000, daily_budget=8000)
    assert remaining == 3000
    assert quota_used(db, "youtube", day_key()) == 5000


def test_platforms_are_isolated():
    db = open_db(":memory:")
    from jester.quota import spend

    spend(db, "youtube", 100, daily_budget=8000)
    assert quota_used(db, "reddit", "2026-08-23") == 0


def test_midnight_roll_resets_budget():
    db = open_db(":memory:")
    from jester.quota import spend

    aug23 = datetime(2026, 8, 23, 12, 0, tzinfo=PT)
    aug24 = datetime(2026, 8, 24, 12, 0, tzinfo=PT)
    spend(db, "youtube", 7900, daily_budget=8000, now=aug23)
    remaining = spend(db, "youtube", 100, daily_budget=8000, now=aug24)
    assert remaining == 7900


def test_over_budget_raises_and_spends_nothing():
    from jester.quota import QuotaExceeded, spend

    db = open_db(":memory:")
    with pytest.raises(QuotaExceeded) as exc:
        spend(db, "youtube", 100, daily_budget=50)
    assert exc.value.remaining == 50
    assert quota_used(db, "youtube", "2026-08-23") == 0


# --- Task 3: near-miss capture ------------------------------------------------------

class FixedEmbedding:
    """Deterministic unit vectors keyed by exact text (3-D so near vectors
    aren't near *each other*): cos(base, v) = v[0]."""

    dim = 3
    VECTORS = {
        "base text": [1.0, 0.0, 0.0],
        # cos(base) = 0.86 -> NEAR_MISS at default threshold 0.87
        "near miss text": [0.86, 0.5102920730894866, 0.0],
        "duplicate text": [1.0, 0.0, 0.0],   # cos(base) = 1.0 -> DUPLICATE
        "distinct text": [0.0, 1.0, 0.0],    # cos(base) = 0.0 -> DISTINCT
    }

    def embed(self, texts):
        return [list(self.VECTORS[t]) for t in texts]


def _archivist_with(vector):
    from jester.agents.archivist import Archivist
    from jester.store import open_db as _open_db

    return Archivist(_open_db(":memory:"), vector, Thresholds())


def test_near_miss_recorded_duplicate_skipped_silently():
    from jester.vector import VectorStore

    vs = VectorStore.in_memory(embed=FixedEmbedding())
    arch = _archivist_with(vs)
    nuggets = [
        Nugget(unique_key="dup", raw_text="duplicate text"),
        Nugget(unique_key="near", raw_text="near miss text"),
        Nugget(unique_key="distinct", raw_text="distinct text"),
    ]
    for n in nuggets:
        n.category = "pain_point"
    kept = arch.run(nuggets)

    assert [n.unique_key for n in kept] == ["dup", "near", "distinct"]  # none pre-exist
    assert arch.near_misses == [pytest.approx(0.86, rel=1e-5)]


def test_true_duplicate_of_stored_point_is_skipped():
    from jester.vector import VectorStore

    vs = VectorStore.in_memory(embed=FixedEmbedding())
    arch = _archivist_with(vs)
    first = [Nugget(unique_key="a", raw_text="base text", category="pain_point")]
    arch.run(first)
    second = [Nugget(unique_key="b", raw_text="duplicate text", category="pain_point")]
    kept = arch.run(second)
    assert kept == []
    assert arch.near_misses == []  # duplicates are not near-misses


def test_near_misses_capped_at_top_three_desc():
    from math import sqrt
    from jester.vector import VectorStore

    def _vec(x, theta_deg):
        """Unit vector with cos(base) = x and residual direction rotated by theta,
        so near-miss vectors stay mutually DISTINCT (~0.62-0.75 cosine)."""
        r = sqrt(1.0 - x * x)
        t = __import__("math").radians(theta_deg)
        return [x, r * __import__("math").cos(t), r * __import__("math").sin(t)]

    class SpreadEmbedding(FixedEmbedding):
        VECTORS = {
            **FixedEmbedding.VECTORS,
            "nm high": _vec(0.865, 0),
            "nm mid": _vec(0.858, 120),
            "nm low": _vec(0.851, 240),
        }

    vs = VectorStore.in_memory(embed=SpreadEmbedding())
    arch = _archivist_with(vs)
    # Seed the corpus with the base vector first: the near-miss candidates must
    # each land in [0.85, 0.87) against it while staying DISTINCT from each other.
    arch.run([Nugget(unique_key="base", raw_text="base text", category="pain_point")])
    nuggets = [
        Nugget(unique_key=k, raw_text=k, category="pain_point")
        for k in ("nm low", "nm mid", "nm high")
    ]
    arch.run(nuggets)
    assert arch.near_misses == pytest.approx([0.865, 0.858, 0.851], rel=1e-5)


# --- Task 4: runs wiring --------------------------------------------------------------

def test_top_near_misses_round_trip_and_render(tmp_path, capsys):
    from jester.cli import cmd_run, cmd_runs
    from jester.store import update_run_summary, start_run, finish_run

    db_path = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="nm-run"))

    db = open_db(str(db_path))
    start_run(db, "nm-manual")
    update_run_summary(db, "nm-manual", top_near_misses=[0.91, 0.86])
    finish_run(db, "nm-manual", status="completed")

    cmd_runs(_ns(db=str(db_path)))
    out = capsys.readouterr().out
    assert "near=[0.91, 0.86]" in out


from jester.models import Nugget  # noqa: E402  (used across task-3 tests)
