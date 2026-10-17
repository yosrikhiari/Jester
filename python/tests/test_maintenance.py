"""Maintenance passes (design section 5 S7, AD7).

Fixtures are built straight into temp archives with the real store
helpers, and dates are seeded RELATIVE TO NOW so the stale-days math is
provable at any wall clock. The pass must be deterministic in its
decisions and append-only in its effects: every test asserts nothing was
deleted, re-pointed or rewritten, only that events landed.
"""
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    """Naive UTC (created_at's clock), free of the utcnow deprecation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

import pytest

from jester import maintenance
from jester.config import Thresholds
from jester.maintenance import (
    _append_event,
    check_drift,
    check_queues,
    check_stale_nuggets,
    event_rows,
    maintenance_pass,
    queue_counts,
)
from jester.models import Nugget
from jester.store import enqueue_batch, insert_nugget, open_db


@pytest.fixture()
def db(tmp_path):
    conn = open_db(str(tmp_path / "j.db"), migrate=True)
    yield conn
    conn.close()


def _sql_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed_batches(db, source, pattern):
    """pattern: a iterable of comment counts per batch row, oldest first."""
    for i, n in enumerate(pattern):
        comments = [{"body": f"c{j}", "fingerprint": f"{source}-{i}-{j}"}
                    for j in range(n)]
        enqueue_batch(db, "reddit", source, f"{source}-{i}",
                      comments=comments, run_id=f"r{i}")


# ---- drift ---------------------------------------------------------------------

def test_drift_spike_flags_a_source_whose_window_got_barren(db):
    # an old healthy history, then an all-empty convergence in the window
    _seed_batches(db, "r/healthy", [3, 3, 3, 3, 3, 3, 3, 3])
    _seed_batches(db, "r/dying", [3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0])
    cfg = Thresholds(scheduler_window_batches=6,
                     maintenance_null_rate_spike=0.20).validate()
    events = check_drift(cfg, db)
    flagged = [e["inputs"]["source"] for e in events]
    assert flagged == ["r/dying"]
    first = events[0]
    # baseline = the history BEFORE the window (6 healthy batches), window =
    # the last 6 (5 empty of 6)
    assert first["inputs"]["baseline_null_rate"] == 0.0
    assert first["inputs"]["window_null_rate"] == pytest.approx(
        0.8333, abs=0.01)
    assert "drift_suspect" == first["kind"]
    # the event row really is in the ledger, with reason/inputs/outcome
    rows = event_rows(db)
    assert rows[0]["kind"] == "drift_suspect" and rows[0]["inputs"]["source"]


def test_drift_stays_quiet_without_history_or_change(db):
    _seed_batches(db, "r/young", [3, 0, 0])  # too new to have a baseline
    # noisy but LEVEL: window rate equals the baseline rate, delta zero
    _seed_batches(db, "r/steady", [3, 0, 3, 0, 3, 0, 3, 0])
    cfg = Thresholds(scheduler_window_batches=4,
                     maintenance_null_rate_spike=0.25).validate()
    assert check_drift(cfg, db) == []


def test_a_quiet_source_never_drifts(db):
    _seed_batches(db, "r/quite-ok", [3] * 12)
    cfg = Thresholds().validate()
    assert check_drift(cfg, db) == []


# ---- stale nuggets -------------------------------------------------------------

def test_stale_clusterless_nuggets_are_flagged_signed_by_age(db):
    now = _now()
    old = now - timedelta(days=120)
    fresh = now - timedelta(days=10)
    old_key = "reddit/r/test/old"
    fresh_key = "reddit/r/test/fresh"
    insert_nugget(db, Nugget(unique_key=old_key, platform="reddit",
                             extracted_insight="old one", run_id="r1",
                             category="pain_point"))
    insert_nugget(db, Nugget(unique_key=fresh_key, platform="reddit",
                             extracted_insight="fresh one", run_id="r2",
                             category="noise"))
    # created_at is stamped by the store; overwrite the two to seed age
    db.execute("UPDATE nuggets SET created_at=? WHERE unique_key=?",
               (_sql_date(old), old_key))
    db.execute("UPDATE nuggets SET created_at=? WHERE unique_key=?",
               (_sql_date(fresh), fresh_key))
    # cluster the fresh one so only the old, clusterless one is flagged
    db.execute(
        "INSERT INTO clusters(id, run_id, nugget_keys) VALUES (1, 'r2', ?)",
        (f'["{fresh_key}"]',))
    db.commit()

    events = check_stale_nuggets(Thresholds(maintenance_stale_days=90)
                                 .validate(), db, now=now)
    assert len(events) == 1
    flagged = events[0]["outcome"]["flagged"]
    assert [f["unique_key"] for f in flagged] == [old_key]
    assert flagged[0]["age_days"] >= 120
    # nothing deleted: both rows remain
    got = [r[0] for r in db.execute(
        "SELECT unique_key FROM nuggets ORDER BY id")]
    assert got == [old_key, fresh_key]


def test_young_or_clustered_nuggets_stay_unflagged(db):
    now = _now()
    key = "reddit/r/test/mid"
    insert_nugget(db, Nugget(unique_key=key, platform="reddit",
                             extracted_insight="not old enough", run_id="r1"))
    db.execute("UPDATE nuggets SET created_at=? WHERE unique_key=?",
               (_sql_date(now - timedelta(days=40)), key))
    db.commit()
    assert check_stale_nuggets(Thresholds(maintenance_stale_days=90)
                               .validate(), db, now=now) == []


# ---- queues ---------------------------------------------------------------------

def test_queue_counts_surface_both_queues_and_only_then_append(db):
    assert queue_counts(db) == {"needs_reembed": 0, "needs_score": 0}
    assert check_queues(db) == []
    insert_nugget(db, Nugget(unique_key="reddit/r/test/k1", platform="reddit",
                             extracted_insight="old", run_id="r1",
                             needs_reembed=True))
    db.execute("INSERT INTO ideas(title, needs_score) VALUES ('x', 1)")
    db.commit()
    counts = queue_counts(db)
    assert counts == {"needs_reembed": 1, "needs_score": 1}
    events = check_queues(db)
    assert len(events) == 1 and events[0]["kind"] == "queues"
    assert "1 nugget(s) need re-embedding" in events[0]["reason"]
    assert "1 idea(s) need re-scoring" in events[0]["reason"]


# ---- the pass -------------------------------------------------------------------

def test_maintenance_pass_appends_everything_and_stays_read_only(db):
    _seed_batches(db, "r/dying", [3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0])
    insert_nugget(db, Nugget(unique_key="reddit/r/test/ancient",
                             platform="reddit", extracted_insight="dust",
                             run_id="r1"))
    db.execute("UPDATE nuggets SET created_at=? WHERE unique_key=?",
               (_sql_date(_now() - timedelta(days=400)),
                "reddit/r/test/ancient"))
    insert_nugget(db, Nugget(unique_key="reddit/r/test/re",
                             platform="reddit", extracted_insight="old",
                             run_id="r2", needs_reembed=True))
    db.commit()
    before = {
        "nuggets": db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0],
        "batches": db.execute("SELECT COUNT(*) FROM ingest_batch"
                              ).fetchone()[0],
    }
    cfg = Thresholds(scheduler_window_batches=6,
                     maintenance_null_rate_spike=0.20).validate()
    events = maintenance_pass(cfg, db)
    kinds = sorted(e["kind"] for e in events)
    assert kinds == ["drift_suspect", "queues", "stale_nuggets"]
    # the pass touched nothing but its own ledger
    assert db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0] \
        == before["nuggets"]
    assert db.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] \
        == before["batches"]
    # the events ledger is append-only-shaped: rerun appends, never rewrites
    n_before = db.execute("SELECT COUNT(*) FROM maintenance_events"
                          ).fetchone()[0]
    maintenance_pass(Thresholds().validate(), db)
    n_after = db.execute("SELECT COUNT(*) FROM maintenance_events"
                         ).fetchone()[0]
    assert n_after > n_before


def test_events_carry_actor_and_round_trip(db):
    _append_event(db, kind="queues", actor_kind="scheduler",
                  actor="maintenance", reason="test",
                  inputs={"needs_reembed": 0, "needs_score": 1},
                  outcome={"note": "test"})
    rows = event_rows(db)
    row = rows[0]
    assert row["actor_kind"] == "scheduler" and row["actor"] == "maintenance"
    assert row["inputs"]["needs_score"] == 1
    assert row.get("event_id", "").startswith("ME-")


def test_registry_rows_for_maintenance_keys():
    t = Thresholds()
    assert t.maintenance_null_rate_spike == 0.25
    assert t.maintenance_stale_days == 90
    assert Thresholds._RANGES["maintenance_null_rate_spike"] == (0.01, 1.0)
    assert Thresholds._RANGES["maintenance_stale_days"] == (7, 3650)
    assert Thresholds._EDITABLE["maintenance_null_rate_spike"] is not None
    assert Thresholds._EDITABLE["maintenance_stale_days"] is not None
