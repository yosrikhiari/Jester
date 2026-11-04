"""The console's read side of the run-outcome ledger.

``run_outcome`` is Go-owned (the worker's CREATE TABLE IF NOT EXISTS, no
schema_version bump): the console opens the archive with migrate=False and
only reads rows. The tests below mirror the worker's DDL to seed a temp
archive, and pin the drift rules' contract INCLUDING their honest limits:
anchor silence alone never alarms in an anchored profile (the anchor is the
only way that mode sees tiles at all), and a single quiet run is weather.
"""
import json

import pytest

from jester import runout
from jester.console.api import ConsoleAPI
from jester.sources import Source, save_sources
from jester.store import open_db

# Mirrors go/internal/store/store.go's run_outcome DDL. The console never
# creates it — only the worker does — so tests do the worker's part.
RUN_OUTCOME_DDL = """
CREATE TABLE IF NOT EXISTS run_outcome (
  run_id      TEXT NOT NULL,
  source      TEXT NOT NULL,
  platform    TEXT NOT NULL DEFAULT '',
  kind        TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL DEFAULT (datetime('now')),
  items       INTEGER NOT NULL DEFAULT 0,
  fresh       INTEGER NOT NULL DEFAULT 0,
  dup         INTEGER NOT NULL DEFAULT 0,
  pages_ok    INTEGER NOT NULL DEFAULT 0,
  pages_err   INTEGER NOT NULL DEFAULT 0,
  bytes       INTEGER NOT NULL DEFAULT 0,
  latency_ms  INTEGER NOT NULL DEFAULT 0,
  fetch_mode  TEXT NOT NULL DEFAULT '',
  first_err   TEXT NOT NULL DEFAULT '',
  stats_json  TEXT,
  PRIMARY KEY (run_id, source)
);
CREATE INDEX IF NOT EXISTS idx_run_outcome_source ON run_outcome(source, observed_at);
"""


def seed(conn, source, run_id, *, observed_at, items=0, fresh=0, dup=0,
         pages_ok=0, pages_err=0, bytes_=0, latency=0, fetch_mode="",
         first_err="", stats=None):
    """One ledger row, in the shape the Go worker writes."""
    conn.execute(
        "INSERT INTO run_outcome (run_id, source, observed_at, items, fresh, "
        "dup, pages_ok, pages_err, bytes, latency_ms, fetch_mode, first_err, "
        "stats_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, source, observed_at, items, fresh, dup, pages_ok, pages_err,
         bytes_, latency, fetch_mode, first_err,
         None if stats is None else json.dumps(stats)),
    )


def walk(kept, fields, *, pages_ok=1, pages_err=0, first_err=""):
    """A realestate row's payload: the walk stats plus its ledger columns."""
    return {
        "items": kept, "fresh": kept, "dup": 0,
        "pages_ok": pages_ok, "pages_err": pages_err, "first_err": first_err,
        "stats": {"pages": pages_ok + pages_err, "pages_ok": pages_ok,
                  "pages_err": pages_err, "bytes": 1000, "latency_ms": 10,
                  "fetch_mode": "http", "parse": {"anchors": kept, "kept": kept,
                                                  "fields": fields}},
    }


def realcfg(tmp_path):
    """A hermetic config dir with two real-estate sources."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    save_sources(cfg / "sources.yaml", [
        Source(name="zoopla", platform="realestate", kind="listing",
               url="https://x.test/z"),
        Source(name="ghost", platform="realestate", kind="listing",
               url="https://x.test/g"),
    ])
    return cfg


@pytest.fixture
def db(tmp_path):
    conn = open_db(str(tmp_path / "j.db"))
    conn.executescript(RUN_OUTCOME_DDL)
    conn.commit()
    yield conn
    conn.close()


# ---- the reader -------------------------------------------------------------

def test_absent_table_is_an_empty_dict(tmp_path):
    """A console that opens an archive the worker has not touched yet reads
    an empty ledger — the same normal state source_state's reader accepts —
    instead of raising 'no such table' at every page load."""
    conn = open_db(str(tmp_path / "fresh.db"))
    try:
        assert runout.load_outcomes(conn) == {}
    finally:
        conn.close()


def test_series_groups_newest_first_and_caps_at_twelve(db):
    for i in range(14):
        seed(db, "zoopla", f"r-{i:02d}",
             observed_at=f"2026-10-{i + 1:02d}T00:00:00Z", items=i)
    db.commit()
    rows = runout.load_outcomes(db)["zoopla"]
    assert len(rows) == runout.SERIES_PER_SOURCE
    assert [r["items"] for r in rows] == list(range(13, 1, -1))


def test_community_row_reads_null_stats_as_absent(db):
    seed(db, "hn-ask", "r-1", observed_at="2026-11-01T00:00:00Z",
         items=5, first_err="500")
    db.commit()
    row = runout.load_outcomes(db)["hn-ask"][0]
    assert row["stats"] == {} and row["first_err"] == "500"


def test_corrupt_stats_json_degrades_to_empty(db):
    db.execute(
        "INSERT INTO run_outcome (run_id, source, observed_at, stats_json)"
        " VALUES ('r-1','s','2026-11-01T00:00:00Z','{nope')")
    db.commit()
    row = runout.load_outcomes(db)["s"][0]
    assert row["stats"] == {}


# ---- the drift rules --------------------------------------------------------

def test_all_pages_failed_alarms_the_walk(db):
    for i, err in enumerate([None, None, "local 403"]):
        seed(db, "tayara", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             **walk(0, {}, pages_ok=0, pages_err=2, first_err=err or ""))
    db.commit()
    kinds = [a["kind"] for a in
             runout.drift_alarms(runout.load_outcomes(db)["tayara"])]
    assert "all-pages-failed" in kinds
    assert "repeat-failure" not in kinds  # one failure is weather


def test_shape_gone_needs_clean_pages_and_a_prior_yield(db):
    # History: the portal kept rows. Latest: pages parsed clean, kept zero.
    for i, kept in enumerate([30, 20]):
        seed(db, "mubawab", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             **walk(kept, {"price": kept}))
    seed(db, "mubawab", "r-2", observed_at="2026-11-03T00:00:00Z",
         **walk(0, {}))
    db.commit()
    kinds = [a["kind"] for a in
             runout.drift_alarms(runout.load_outcomes(db)["mubawab"])]
    assert "shape-gone" in kinds


def test_anchor_silence_alone_is_not_shape_gone(db):
    """The honest limit, pinned: a failing walk (every fetch errored) with no
    prior parse signal does NOT claim the shape vanished — the ledger cannot
    see tiles through a refused request at all, so it refuses to judge."""
    seed(db, "bare", "r-1", observed_at="2026-11-02T00:00:00Z",
         **walk(8, {"price": 8}))
    seed(db, "bare", "r-2", observed_at="2026-11-03T00:00:00Z",
         **walk(0, {}, pages_ok=0, pages_err=2, first_err="connection refused"))
    db.commit()
    kinds = [a["kind"] for a in
             runout.drift_alarms(runout.load_outcomes(db)["bare"])]
    assert "shape-gone" not in kinds
    assert "all-pages-failed" in kinds


def test_never_yielded_source_is_not_shape_gone(db):
    """A source that has never kept anything is unmeasured, not drifted —
    the onboarding story must stay distinct from the breakdown story."""
    for i in range(3):
        seed(db, "newborn", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             **walk(0, {}))
    db.commit()
    assert runout.drift_alarms(runout.load_outcomes(db)["newborn"]) == []


def test_coverage_drop_alarms_against_the_pooled_baseline(db):
    # Price filled on every kept row for two runs, then only on a fifth: the
    # subtle half-drift (prices on a minority of tiles) a zero never shows.
    for i, (kept, covered) in enumerate([(10, 10), (10, 10), (10, 2)]):
        seed(db, "zoopla", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             **walk(kept, {"price": covered}))
    db.commit()
    drops = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
             if a["kind"] == "coverage-drop"]
    assert len(drops) == 1
    assert drops[0]["field"] == "price"


def test_coverage_drop_needs_a_good_baseline(db):
    """A field born thin (20% forever) has no clear lost state to fall from —
    the COVERAGE_FLOOR guard keeps a permanently thin field quiet."""
    for i, covered in enumerate([2, 2, 0]):
        seed(db, "thin", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             **walk(10, {"currency": covered}))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["thin"])
           if a["kind"] == "coverage-drop"]
    assert got == []


def test_repeat_failure_counts_a_streak_not_a_lifetime(db):
    """F,F,ok,F must NOT read '3 consecutive' — one success in between means
    the portal answers sometimes, which reads differently to an operator."""
    for i, err in enumerate(["e1", "e2", "", "e3"]):
        seed(db, "flappy", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             items=0, first_err=err)
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["flappy"])
           if a["kind"] == "repeat-failure"]
    assert got == []


def test_three_consecutive_failures_alarm(db):
    for i, err in enumerate(["e1", "e2", "e3"]):
        seed(db, "dead", f"r-{i}", observed_at=f"2026-11-0{i + 1}T00:00:00Z",
             items=0, first_err=err)
    db.commit()
    kinds = [a["kind"] for a in
             runout.drift_alarms(runout.load_outcomes(db)["dead"])]
    assert "repeat-failure" in kinds


# ---- the grade verdict (stats_json "grade") ---------------------------------

def seeded_grade(*, failed=(), findings=(), thin=False):
    """A grade dict exactly as the worker embeds it: one portal, three gates,
    verdicts from `failed`, structural findings off the portal, and `thin`
    marking every gate below the run quorum (shares that make no verdict)."""

    def gate(name):
        if thin:
            return {"name": name, "verdict": "thin", "got": 0.8, "n": 8, "total": 10}
        return {"name": name, "verdict": "fail" if name in failed else "ok",
                "got": 0.67, "n": 8, "total": 12}

    portal = {
        "portal": "zoopla", "records": 12,
        "gates": [gate("url present"), gate("price parsed"), gate("media present")],
    }
    if findings:
        portal["failures"] = list(findings)
    return {"total": 12, "unique": 12, "dupes": 0, "portals": [portal]}


def graded_row(run_id, **kw):
    """A realestate walk row that carries a grade in its stats."""
    body = walk(12, {"price": 12})
    body["stats"]["grade"] = seeded_grade(**kw)
    return body


def test_grader_fail_names_the_gates(db):
    seed(db, "zoopla", "run-bad", observed_at="2026-11-03T00:00:00Z",
         **graded_row("run-bad", failed=("price parsed", "media present")))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
           if a["kind"] == "grade-failed"]
    assert len(got) == 1
    assert got[0]["label"] == "grade: 2 gate(s)"
    assert (got[0]["note"] ==
            "run run-bad: 2 gate(s) failed (price parsed, media present)")


def test_passing_grade_clears_an_old_failure(db):
    seed(db, "zoopla", "run-bad", observed_at="2026-11-02T00:00:00Z",
         **graded_row("run-bad", failed=("price parsed",)))
    seed(db, "zoopla", "run-ok", observed_at="2026-11-03T00:00:00Z",
         **graded_row("run-ok"))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
           if a["kind"] == "grade-failed"]
    assert got == []  # the ledger's newest verdict is clean


def test_ungraded_newest_row_does_not_clear_the_alarm(db):
    """A walk that parsed nothing carries no grade; it must not make the
    portal look graded-clean — the alarm stays, and still says which run."""
    seed(db, "zoopla", "run-bad", observed_at="2026-11-02T00:00:00Z",
         **graded_row("run-bad", failed=("price parsed",)))
    seed(db, "zoopla", "run-dark", observed_at="2026-11-03T00:00:00Z",
         **walk(0, {}, pages_ok=0, pages_err=2, first_err="connection refused"))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
           if a["kind"] == "grade-failed"]
    assert len(got) == 1
    assert got[0]["note"].startswith("run run-bad: 1 gate(s) failed")


def test_findings_only_grade_alarms(db):
    """A grade whose gates passed but whose records carry a structural
    finding is still a failing grade — the finding is the alarm."""
    seed(db, "zoopla", "run-find", observed_at="2026-11-03T00:00:00Z",
         **graded_row("run-find",
                      findings=("zoopla: 2 records carry no deal_type",)))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
           if a["kind"] == "grade-failed"]
    assert len(got) == 1
    assert got[0]["label"] == "grade: 1 finding(s)"
    assert "no deal_type" in got[0]["note"]


def test_thin_grade_makes_no_alarm(db):
    """Below the run quorum the sample carries shares without verdicts —
    neither fail nor alarm: a 3-tile walk cannot paint a red trend card."""
    seed(db, "zoopla", "run-thin", observed_at="2026-11-03T00:00:00Z",
         **graded_row("run-thin", thin=True))
    db.commit()
    got = [a for a in runout.drift_alarms(runout.load_outcomes(db)["zoopla"])
           if a["kind"] == "grade-failed"]
    assert got == []


def test_grade_failing_source_is_drifting(db, tmp_path):
    seed(db, "zoopla", "run-bad", observed_at="2026-11-03T00:00:00Z",
         **graded_row("run-bad", failed=("price parsed",)))
    db.commit()
    db.close()
    cfg = realcfg(tmp_path)
    api = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(cfg))
    res = api.sources()
    by_name = {s["name"]: s for s in res["sources"]}
    kinds = [a["kind"] for a in by_name["zoopla"]["trend"]["drift"]]
    assert "grade-failed" in kinds
    assert res["drifting_sources"] == ["zoopla"]  # ghost carries no rows


def test_outcomes_drill_down_carries_the_grade_rows(db, tmp_path):
    seed(db, "zn", "r-1", observed_at="2026-11-01T00:00:00Z",
         **graded_row("r-1", failed=("price parsed",)))
    db.commit()
    db.close()
    cfg = tmp_path / "config3"
    cfg.mkdir()
    api = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(cfg))
    res = api.outcomes()
    row = res["series"]["zn"][0]
    assert row["stats"]["grade"]["portals"][0]["gates"][1]["verdict"] == "fail"
    assert any(a["kind"] == "grade-failed" for a in res["drift"]["zn"])


# ---- the console surfaces ---------------------------------------------------

def test_sources_carries_trend_cards(db, tmp_path):
    dates = ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z",
             "2026-11-03T00:00:00Z"]
    for run_id, at, (kept, covered) in zip(["a", "b", "c"], dates,
                                           [(30, 30), (20, 20), (10, 2)]):
        seed(db, "zoopla", run_id, observed_at=at,
             **walk(kept, {"price": covered}))
    db.commit()
    db.close()
    cfg = realcfg(tmp_path)
    api = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(cfg))
    res = api.sources()
    by_name = {s["name"]: s for s in res["sources"]}
    # The trend card rides each visited source; a never-visited one has none.
    assert by_name["zoopla"]["trend"]["items"] == 10
    assert by_name["zoopla"]["trend"]["drift"][0]["kind"] == "coverage-drop"
    assert "trend" not in by_name["ghost"]
    assert res["drifting_sources"] == ["zoopla"]


def test_outcomes_endpoint_is_the_drill_down(db, tmp_path):
    seed(db, "zn", "r-1", observed_at="2026-11-01T00:00:00Z",
         **walk(5, {"price": 5}))
    db.commit()
    db.close()
    cfg = tmp_path / "config2"
    cfg.mkdir()
    api = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(cfg))
    res = api.outcomes()
    assert res["ok"] is True
    assert res["runs_per_source"] == runout.SERIES_PER_SOURCE
    assert res["series"]["zn"][0]["stats"]["pages_ok"] == 1
    assert res["drift"]["zn"] == []  # one healthy run alarms nothing
