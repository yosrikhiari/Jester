"""Collection stands down when the queue is already too deep.

The two halves of the pipeline had no relationship. `ingest` filled a queue,
`treat` drained it, and neither could see the other. Little's Law says where
that ends: queue length is arrival rate times wait, so once arrivals outrun
processing the wait grows without limit. There is no rate at which it
recovers, because there is no such rate.

Measured on the live archive when this was written: 2,223 pages arriving a
day against 1,234 processed, a queue at 31,197 climbing ~1,000/day. An
unbounded queue did not absorb that, it postponed it.
"""
import types

from jester.cli import QUEUE_CEILING, _pending_batches, cmd_ingest
from jester.store import open_db


def _args(tmp_path, **kw):
    a = types.SimpleNamespace(
        db=str(tmp_path / "j.db"), config="config", run="r",
        mock=True, only="", max_comments=None, max_posts=None,
        queue_ceiling=None, exports=None,
    )
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _queue(db, n, status="pending"):
    for i in range(n):
        db.execute(
            "INSERT INTO ingest_batch (platform, source, thread_id, comments, "
            "status, run_id) VALUES ('reddit','u','t','[]',?,'r')", (status,))
    db.commit()


def test_a_deep_queue_stops_collection(tmp_path, capsys, monkeypatch):
    """The regression. Fetching more when the backlog is already losing
    ground only makes the backlog older."""
    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 12)
    db.close()
    called = []
    monkeypatch.setattr("jester.cli.worker_ingest",
                        lambda *a, **k: called.append(1) or {"ok": True})

    cmd_ingest(_args(tmp_path, queue_ceiling=10))

    assert called == [], "collected despite a full queue"
    assert "not collecting" in capsys.readouterr().out


def test_a_shallow_queue_collects_normally(tmp_path, monkeypatch):
    """Otherwise the ceiling is just an off switch."""
    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 3)
    db.close()
    called = []
    monkeypatch.setattr("jester.cli.worker_ingest",
                        lambda *a, **k: called.append(1) or {"ok": True})

    cmd_ingest(_args(tmp_path, queue_ceiling=10))

    assert called == [1]


def test_zero_disables_the_ceiling(tmp_path, monkeypatch):
    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 500)
    db.close()
    called = []
    monkeypatch.setattr("jester.cli.worker_ingest",
                        lambda *a, **k: called.append(1) or {"ok": True})

    cmd_ingest(_args(tmp_path, queue_ceiling=0))

    assert called == [1], "0 must mean no ceiling, not a ceiling of zero"


def test_listings_are_not_backlog(tmp_path):
    """The correction that matters. A listing is already structured and never
    reaches the extractor -- store.pending_batches claims kind='comment' only
    -- so a listing row sits at status='pending' for the life of the archive
    by design.

    Counting them was wrong in a way that looked convincing: the live archive
    showed 31,593 pending, of which 31,582 were real-estate listings and 11
    were comments. Measured against the raw total, a ceiling would have stood
    down Reddit and Hacker News collection over a property-portal backlog that
    nothing is waiting on.
    """
    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 4)
    for _ in range(900):
        db.execute("INSERT INTO ingest_batch (platform, source, thread_id, "
                   "comments, listings, kind, status, run_id) VALUES "
                   "('realestate','p','t','[]','[{}]','listing','pending','r')")
    db.commit()
    db.close()

    assert _pending_batches(str(tmp_path / "j.db")) == 4


def test_only_pending_batches_count(tmp_path, monkeypatch):
    """Done work is not backlog. Counting it would wedge collection shut
    permanently, since `done` only ever grows."""
    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 3)
    _queue(db, 900, status="done")
    db.close()

    assert _pending_batches(str(tmp_path / "j.db")) == 3


def test_an_unreadable_archive_reads_as_unknown_not_empty(tmp_path):
    """None, never 0. A failure to measure must not read as "the queue is
    empty, collect away"."""
    assert _pending_batches(str(tmp_path / "nope" / "missing.db")) is None


def test_the_default_ceiling_is_above_the_observed_queue():
    """31,197 was the live depth when this landed. A ceiling below it would
    have stopped collection on day one for a condition that had been building
    for a week."""
    assert QUEUE_CEILING > 31_197


# ---- the console reported the same phantom backlog -------------------------

def test_the_console_separates_comments_from_listings(tmp_path):
    """The dashboard counted every pending row and reported a 31,593 backlog.
    31,582 of those were real-estate listings that nothing is waiting on, and
    11 were comments. A number that large is not read as "mostly listings",
    it is read as "the pipeline is drowning", which is what happened."""
    from jester.console.api import ConsoleAPI

    db = open_db(str(tmp_path / "j.db"))
    _queue(db, 7)
    for _ in range(120):
        db.execute("INSERT INTO ingest_batch (platform, source, thread_id, "
                   "comments, listings, kind, status, run_id) VALUES "
                   "('realestate','p','t','[]','[{}]','listing','pending','r')")
    db.commit()
    db.close()

    counts = ConsoleAPI(db_path=str(tmp_path / "j.db"))._counts()

    assert counts["pending_batches"] == 7, "listings must not read as backlog"
    assert counts["pending_listings"] == 120, "but they must still be visible"
