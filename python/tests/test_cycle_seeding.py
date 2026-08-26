"""A live cycle must never invent its input.

`cmd_run` falls back to the canned `FixtureFetcher` corpus when the queue is
empty — that is the M1.1 mock demo, and it is correct there. It used to happen
unconditionally, which meant a SCHEDULED LIVE tick that fetched nothing (every
browser source skipped because cloakserve was down, say) archived the mock
sample as if it had been scraped. On this machine that only looked harmless
because the skip-list already held those fingerprints; against a fresh database
the night's archive is fabricated (R29).

So: the caller that just fetched decides. These pin both halves of that.
"""

import pytest

from jester import cli
from jester.fetchers import MOCK_SAMPLE
from jester.store import open_db


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _cycle_args(tmp_path, config_dir, **kw):
    base = dict(
        db=str(tmp_path / "cycle.db"),
        config=str(config_dir),
        run="cycle-test",
        exports=str(tmp_path / "exports"),
        max_ideas=None,
        only="",
        max_comments=None,
        max_posts=None,
        mock=False,
        no_timestamps=True,
        stale_after=60,
    )
    base.update(kw)
    return _ns(**base)


@pytest.fixture
def config_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "config"


def _fake_ingest(queued):
    """Stand in for the Go worker: it ran, it succeeded, it queued `queued`."""

    def ingest(*a, **kw):
        return {"ok": True, "code": 0, "output": "", "queued_new_comments": queued}

    return ingest


def _mock_fingerprints():
    return {c["fingerprint"] for b in MOCK_SAMPLE for c in b["comments"]}


def test_live_cycle_that_fetched_nothing_archives_nothing(
    tmp_path, config_dir, monkeypatch
):
    monkeypatch.setattr(cli, "worker_ingest", _fake_ingest(0))
    args = _cycle_args(tmp_path, config_dir, mock=False)
    cli.cmd_cycle(args)

    db = open_db(args.db)
    keys = [r[0] for r in db.execute("SELECT unique_key FROM nuggets").fetchall()]
    for k in keys:
        for fp in _mock_fingerprints():
            assert not k.endswith(fp), f"live cycle archived the mock sample: {k}"
    assert keys == [], "a live cycle that fetched nothing must archive nothing"


def test_mock_cycle_still_seeds_the_fixture_corpus(tmp_path, config_dir, monkeypatch):
    """`--mock` is the offline demo; taking the fallback away from it would
    leave `jester cycle --mock` with nothing to demonstrate."""
    monkeypatch.setattr(cli, "worker_ingest", _fake_ingest(0))
    args = _cycle_args(tmp_path, config_dir, mock=True)
    cli.cmd_cycle(args)

    db = open_db(args.db)
    keys = [r[0] for r in db.execute("SELECT unique_key FROM nuggets").fetchall()]
    assert keys, "a mock cycle should seed and archive the fixture corpus"


def test_plain_run_keeps_the_demo_fallback(tmp_path, config_dir):
    """`jester run` on its own never fetched, so it has nothing to be honest
    about — the M1.1 behaviour stands."""
    args = _ns(
        db=str(tmp_path / "run.db"),
        config=str(config_dir),
        run="run-test",
        exports=str(tmp_path / "exports"),
        max_ideas=None,
    )
    cli.cmd_run(args)
    db = open_db(args.db)
    n = db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0]
    assert n > 0
