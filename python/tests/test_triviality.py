"""R37 triviality judge tests (§7.2 / §37.5): rule verdicts + run-summary wiring."""
import json
from pathlib import Path

import pytest

from jester.config import load_config
from jester.models import Nugget
from jester.store import (
    enqueue_batch,
    get_runs,
    insert_nugget,
    open_db,
    start_run,
    update_run_summary,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: judge_trivial rules -------------------------------------------

def test_specific_pain_is_non_trivial():
    from jester.triviality import judge_trivial

    v = judge_trivial("Splitting a 40GB CSV in Excel freezes for minutes")
    assert not v.trivial
    assert v.reasons == ()


def test_tool_mention_inside_pain_stays_non_trivial():
    from jester.triviality import judge_trivial

    v = judge_trivial("My Excel export of 50k rows takes forever and crashes")
    assert not v.trivial


def test_generic_paraphrase_is_trivial():
    from jester.triviality import REASON_GENERIC, judge_trivial

    v = judge_trivial("Users want better UX")
    assert v.trivial
    assert v.reasons == (REASON_GENERIC,)


def test_someone_should_build_is_generic():
    from jester.triviality import REASON_GENERIC, judge_trivial

    v = judge_trivial("Someone should build a fix for this whole mess")
    assert v.trivial
    assert v.reasons == (REASON_GENERIC,)


def test_mainstream_tool_solves_is_trivial():
    from jester.triviality import REASON_TOOL_SOLVES, judge_trivial

    v = judge_trivial("Notion already does this with its databases")
    assert v.trivial
    assert v.reasons == (REASON_TOOL_SOLVES,)


def test_just_use_is_tool_solves():
    from jester.triviality import REASON_TOOL_SOLVES, judge_trivial

    v = judge_trivial("Just use Obsidian for that")
    assert v.trivial
    assert v.reasons == (REASON_TOOL_SOLVES,)


def test_vague_comment_has_no_specific_context():
    from jester.triviality import REASON_VAGUE, judge_trivial

    v = judge_trivial("The whole experience leaves me feeling confused")
    assert v.trivial
    assert v.reasons == (REASON_VAGUE,)


# --- Task 2: model + store plumbing ----------------------------------------

def test_insert_nugget_uses_explicit_trivial(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    n = Nugget(unique_key="k1", platform="reddit", category="pain_point",
               extracted_insight="has an insight", trivial=True)
    insert_nugget(db, n)
    row = db.execute("SELECT trivial FROM nuggets WHERE unique_key='k1'").fetchone()
    assert row[0] == 1


def test_insert_nugget_falls_back_when_unset(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    # Legacy heuristic: pain_point with no insight -> trivial=1.
    insert_nugget(db, Nugget(unique_key="k2", category="pain_point", extracted_insight=""))
    # pain_point WITH insight and no explicit verdict -> legacy says 0.
    insert_nugget(db, Nugget(unique_key="k3", category="pain_point", extracted_insight="real insight"))
    rows = dict(db.execute("SELECT unique_key, trivial FROM nuggets").fetchall())
    assert rows["k2"] == 1
    assert rows["k3"] == 0


def test_run_summary_persists_trivial_share(tmp_path):
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    rid = start_run(db, "share-run")
    update_run_summary(db, "share-run", n_discarded_trivial=2, trivial_share=0.5)
    row = db.execute("SELECT n_discarded_trivial, trivial_share FROM runs WHERE id=?", (rid,)).fetchone()
    assert row[0] == 2
    assert abs(row[1] - 0.5) < 1e-9


# --- Task 3: cmd_run wiring -------------------------------------------------

def test_cmd_run_records_trivial_share(tmp_path):
    from jester.cli import cmd_run

    db_path = tmp_path / "j.db"
    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="tj-run"))
    r = get_runs(open_db(str(db_path)))[0]
    assert r["n_nuggets_kept"] > 0
    assert r["n_discarded_trivial"] >= 1
    assert 0 < r["trivial_share"] < 1


def test_cmd_run_all_trivial_flag_fires(tmp_path):
    from jester.cli import cmd_run

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    enqueue_batch(db, "reddit", "src", "tid", [
        {"body": "Just use Notion for that", "fingerprint": "a", "upvotes": 10},
        {"body": "Why isn't there something simpler overall", "fingerprint": "b", "upvotes": 9},
    ])

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="at-run"))

    r = get_runs(open_db(str(db_path)))[0]
    flags = json.loads(r["floor_flags"] or "[]")
    # M1.3 pre-filter (§37.12) drops the 24-char "Just use Notion" body as
    # too_short, so only the second comment reaches the archive. Floor, not
    # gate: the surviving trivial nugget is still archived and flags the run.
    assert r["n_nuggets_kept"] == 1
    assert "ALL_TRIVIAL" in flags


def test_cmd_run_specific_corpus_lacks_all_trivial(tmp_path):
    from jester.cli import cmd_run

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    enqueue_batch(db, "reddit", "src", "tid", [
        {"body": "Exporting a 40GB CSV crashes my Docker container every time",
         "fingerprint": "c", "upvotes": 12},
    ])

    cmd_run(_ns(config=str(REPO_CONFIG), db=str(db_path), run="sp-run"))

    r = get_runs(open_db(str(db_path)))[0]
    flags = json.loads(r["floor_flags"] or "[]")
    assert "ALL_TRIVIAL" not in flags
