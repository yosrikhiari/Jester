"""The deterministic source scheduler (plan section 5 S5, gates G5/DQ6).

Every number in here is a pure function of (thresholds, ledger rows,
sources.yaml, run_seq): same inputs, same plan, byte for byte. Fixtures are
built with the REAL store helpers (enqueue_batch / insert_nugget) on temp
databases - no live data, no model, no clock in a decision.

The design's own acceptance test is reproduced here by name:
`test_replay_reallocates` - on fixture history, at least one third of the
zero-yield group's allocated turns move to a yielding group (G5) - plus the
cap-clamp test that pins DQ6 (the scheduler ships NAME lists to the Go
worker and nothing else: it cannot and does not raise a Reddit/SE cap).
"""
from pathlib import Path

import pytest

from jester import scheduler
from jester.config import Thresholds, load_thresholds
from jester.models import Nugget
from jester.scheduler import (
    alloc_names,
    replay,
    score_source,
    source_plan,
)
from jester.sources import Source, save_sources
from jester.store import enqueue_batch, insert_nugget, open_db

ARCH_URL = "https://www.reddit.com/r/devops/"


class _Db:
    """sqlite3.Connection with the two things tests need alongside it.

    A connection is a C type: no attribute shadowing. The proxy passes
    everything through and adds `jester_path` for the APIs that take a path.
    """

    def __init__(self, conn, path: str):
        self.conn = conn
        self.jester_path = path

    def __getattr__(self, name):
        return getattr(self.conn, name)


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "j.db")
    conn = open_db(path, migrate=True)
    yield _Db(conn, path)
    conn.close()


@pytest.fixture()
def sources():
    return [
        Source(name="r/devops", platform="reddit", kind="subreddit",
               url=ARCH_URL),
        Source(name="r/nightly", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/nightly/"),
        Source(name="r/dormant", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/dormant/"),
    ]


def _run_batch(db, source_name, thread, run_id, platform="reddit"):
    enqueue_batch(db, platform, source_name, thread,
                  comments=[{"text": "x", "score": 1}], run_id=run_id)
    return thread


def _nuggets_for(db, platform, thread, n, run_id="r1"):
    for i in range(n):
        insert_nugget(db, Nugget(
            unique_key=f"{platform}/{thread}/{i}",
            platform=platform, thread_id=thread,
            extracted_insight=f"insight {thread} #{i}", run_id=run_id,
        ))


# ---- scoring ----------------------------------------------------------------

def test_score_counts_what_the_ledger_says(db, sources):
    for i in range(4):
        t = _run_batch(db, "r/devops", f"t{i}", f"run{i % 2}")
        _nuggets_for(db, "reddit", t, 2)
    s = score_source(db, Thresholds(), sources[0], window=30)
    assert (s.threads, s.batches, s.fresh_nuggets) == (4, 4, 8)
    assert s.rate == 2.0


def test_window_limits_how_far_back_the_score_reads(db, sources):
    for i in range(10):
        t = _run_batch(db, "r/devops", f"old{i}", f"o{i}")
        _nuggets_for(db, "reddit", t, 0)
    t = _run_batch(db, "r/devops", "newt", "new")
    _nuggets_for(db, "reddit", t, 1)
    s = score_source(db, Thresholds(), sources[0], window=3)
    # only the 3 most recent batch events count: two barren threads + the new
    # one all count as threads walked; only the new one produced nuggets
    assert s.threads == 3 and s.fresh_nuggets == 1


def test_plan_is_deterministic(db, sources):
    for i in range(3):
        t = _run_batch(db, "r/devops", f"hot{i}", f"h{i}")
        _nuggets_for(db, "reddit", t, 3)
    a = source_plan(Thresholds(), db, sources, run_seq=7)
    b = source_plan(Thresholds(), db, sources, run_seq=7)
    assert a == b


# ---- ranking and the probe rotation ----------------------------------------

def _seed_hot_and_zeros(db, hot_nuggets=30):
    for i in range(3):
        t = _run_batch(db, "r/devops", f"hot{i}", f"h{i}")
        _nuggets_for(db, "reddit", t, hot_nuggets)
    for i in range(3):
        _run_batch(db, "r/nightly", f"n{i}", f"n{i}")
    for i in range(3):
        _run_batch(db, "r/dormant", f"d{i}", f"d{i}")
    _run_batch(db, "r/nightly", "brandnew", "x")  # more zero-yield data


def test_rank_puts_yielding_sources_first(db, sources):
    _seed_hot_and_zeros(db)
    plan = source_plan(Thresholds(), db, sources, run_seq=0)
    walked_groups = [d.group for d in plan.decisions if d.walks_this_run]
    assert walked_groups == sorted(
        walked_groups, key=lambda g: scheduler.GROUP_ORDER[g]
    )
    first = plan.decisions[0]
    assert first.name == "r/devops" and first.group == "hot"


def test_zero_yield_sources_rotate_their_single_probe(db, sources):
    _seed_hot_and_zeros(db)
    probed = [source_plan(Thresholds(), db, sources,
                          run_seq=seq).walked for seq in (0, 1, 2)]
    # zero-yield probe candidates sort alphabetically: dormant, then nightly
    for w in probed:
        zeros = [n for n in w if n in ("r/nightly", "r/dormant")]
        assert len(zeros) == 1  # exactly ONE zero-yield probe per run
    assert [next(n for n in w if n in ("r/nightly", "r/dormant"))
            for w in probed] == ["r/dormant", "r/nightly", "r/dormant"]


def test_walking_list_excludes_resting_zero_sources(db, sources):
    _seed_hot_and_zeros(db)
    plan = source_plan(Thresholds(), db, sources, run_seq=11)
    assert "r/devops" in plan.walked
    # two zero sources rest: the walk list has at most one of them
    zero_in_walk = [n for n in plan.walked
                    if n in ("r/nightly", "r/dormant")]
    assert len(zero_in_walk) <= 1


# ---- DQ6: the cap clamp is structural --------------------------------------

def test_caps_never_raised_names_only(db, sources):
    for i in range(4):
        t = _run_batch(db, "r/devops", f"hot{i}", f"h{i}")
        _nuggets_for(db, "reddit", t, 5)
    for i in range(4):
        _run_batch(db, "r/nightly", f"n{i}", f"n{i}")

    cfg = Thresholds(max_threads_per_platform={"reddit": 2}).validate()
    plan = source_plan(cfg, db, sources, run_seq=0)
    # every reported cap IS the configured cap, never more
    assert all(d.cap == 2 for d in plan.decisions)
    # and the walk output is names, full stop
    assert all(isinstance(n, str) for n in plan.walked)
    from jester.worker import worker_argv

    argv = worker_argv("go", ".", "x.db", "run", live=True, only=plan.walked)
    joined = " ".join(argv)
    assert "-only" in joined and "r/devops" in joined
    # no thread-depth override exists or will ever exist in this path: the
    # scheduler's whole vocabulary on a run is the -only name list
    assert "-max-threads" not in joined


def test_explicit_only_beats_the_plan(monkeypatch, tmp_path):
    """An operator's --only always wins; the allocator is not consulted."""
    from jester import worker

    called = {"n": 0}

    def _spy(config_dir, db_path):
        called["n"] += 1
        return ["r/probed"]

    monkeypatch.setattr("jester.scheduler.alloc_names", _spy)
    argv = worker.worker_argv("go", str(tmp_path), "x.db", "run",
                              live=True, only=["r/own"])
    assert "r/own" in ",".join(argv)
    assert called["n"] == 0


# ---- the run hook ------------------------------------------------------------

def test_alloc_names_off_or_broken_returns_none(tmp_path, db):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "thresholds.yaml").write_text(
        "llm_provider: fake\n", encoding="utf-8")
    assert alloc_names(str(cfg_dir), str(db)) is None  # scheduler not enabled

    (cfg_dir / "thresholds.yaml").write_text(
        "llm_provider: fake\nscheduler_enabled: true\n", encoding="utf-8")
    cfg = load_thresholds(cfg_dir / "thresholds.yaml")
    assert cfg.scheduler_enabled is True

    # no ingest history -> empty plan -> None (walk everything, legacy)
    assert alloc_names(str(cfg_dir), db.jester_path) is None


def test_alloc_names_matches_plan_walk(db, sources, tmp_path):
    _seed_hot_and_zeros(db)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "thresholds.yaml").write_text(
        "llm_provider: fake\nscheduler_enabled: true\n", encoding="utf-8")
    save_sources(cfg_dir / "sources.yaml", sources)
    got = alloc_names(str(cfg_dir), db.jester_path)
    plan = source_plan(load_thresholds(cfg_dir / "thresholds.yaml"), db,
                       sources)
    assert got == list(plan.walked)
    assert got and "r/devops" in got


# ---- replay ------------------------------------------------------------------

def _fixture_plan(db):
    """One hot source, four barren ones; flat legacy = 80% zero turns."""
    seeded = [
        Source(name="r/devops", platform="reddit", kind="subreddit",
               url=ARCH_URL),
        Source(name="r/nightly", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/nightly/"),
        Source(name="r/dormant", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/dormant/"),
        Source(name="r/barren-a", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/barren-a/"),
        Source(name="r/barren-b", platform="reddit", kind="subreddit",
               url="https://www.reddit.com/r/barren-b/"),
    ]
    for i in range(3):
        t = _run_batch(db, "r/devops", f"hot{i}", f"h{i}")
        _nuggets_for(db, "reddit", t, 9)
    for name in ("r/nightly", "r/dormant", "r/barren-a", "r/barren-b"):
        for j in range(3):
            _run_batch(db, name, f"{name}-{j}", f"{name}-{j}")
    return seeded, source_plan(Thresholds(), db, seeded, run_seq=0)


def test_replay_reallocates(db, sources):
    """The design's named gate (G5): at least one third of zero-yield turns
    move to a yielding group across the replay horizon."""
    seeded, plan = _fixture_plan(db)
    zero_sources = sum(1 for d in plan.decisions if d.group == "zero")
    assert zero_sources == 4

    ticks = 6
    walked_per_tick = []
    zero_turns = 0
    for tick in range(ticks):
        p = source_plan(Thresholds(), db, seeded,
                        run_seq=plan.run_seq + tick)
        walked_per_tick.append(p.walked)
        zero_turns += sum(1 for d in p.decisions
                          if d.walks_this_run and d.group == "zero")
    legacy_zero_turns = ticks * zero_sources
    share = (legacy_zero_turns - zero_turns) / legacy_zero_turns
    assert share >= 1 / 3, (
        f"the plan must move a third of zero-yield turns (moved {share:.0%})"
    )
    # and every still-yielding source keeps its turn every time
    assert all("r/devops" in w for w in walked_per_tick)


def test_replay_prints_and_returns_the_count(db, sources, capsys):
    _seed_hot_and_zeros(db)
    plan = source_plan(Thresholds(), db, sources, run_seq=0)
    moved = replay(plan, Thresholds(), db, sources, ticks=3)
    out = capsys.readouterr().out
    assert "zero" in out and "reallocation" in out
    assert moved >= 0


# ---- the plan command ---------------------------------------------------------

def test_plan_action_prints(db, sources, tmp_path, capsys):
    _seed_hot_and_zeros(db)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    save_sources(cfg_dir / "sources.yaml", sources)
    (cfg_dir / "thresholds.yaml").write_text(
        "llm_provider: fake\n", encoding="utf-8")

    import argparse

    args = argparse.Namespace(db=db.jester_path, config=str(cfg_dir), replay=0)
    assert scheduler.plan_action(args) == 0
    out = capsys.readouterr().out
    assert "r/devops" in out and "scheduler:" in out


def test_registry_rows_for_the_scheduler_keys_exist():
    t = Thresholds()
    assert t.scheduler_enabled is False
    assert t.scheduler_window_batches == 30
    assert Thresholds._EDITABLE["scheduler_enabled"] is not None
    assert Thresholds._RANGES["scheduler_window_batches"] == (1, 500)
    # the chat keys from S3/S4 stay pinned at the same time
    assert Thresholds._EDITABLE["chat_enabled"] is not None
