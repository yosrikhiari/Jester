"""The labeled fixture set and the command that grades it.

The bar this set has to clear: twenty labeled cases spanning
relevant/irrelevant, ambiguous, edits/deletions and failures, every one
checked deterministically.

Two kinds of case, because two kinds of thing are being checked:

* **classification** — one record in, one verdict out. Graded on the audience
  the label claims (`buyer` / `practitioner` / `none`), on whether it is
  flagged ambiguous, and on whether the buyer voice was inherited from the
  thread rather than stated.
* **replay** — the same record seen two or three times, with what the table
  must say afterwards. Edits, removals and "seeing it again is not a
  duplicate" cannot be checked by classifying a string; they are facts about a
  row across runs, so they are checked against a real (temporary) database.

The cases live in `tests/fixtures/signals/*.json` as data, not code. A case is
a claim about behaviour, and a claim is easier to argue with when it is not
buried in a test function. Each one carries a `why` saying what it is for —
most of them exist because something failed against real archived posts.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List, Optional

from . import Signal, TABLE, open_signals, mark_removed, upsert
from .filters import classify, load_rules

#: Where the cases live. Under tests/ because they are the test set, and the
#: gate is graded by running them.
CASES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "signals"

#: The five categories a complete set needs. A category missing from it
#: is a failure in itself — the gate is about coverage as much as about
#: passing, and a set that quietly lost its failure cases still reads 100%.
REQUIRED_CATEGORIES = ("relevant", "irrelevant", "ambiguous", "edits", "failures")

#: How many the gate asks for.
REQUIRED_TOTAL = 20


class FixtureError(RuntimeError):
    """The fixture set itself is wrong — missing, unreadable, or short of the
    categories the gate names. Loud, because a silently empty set passes."""


def load_cases(directory: Path | str | None = None) -> List[dict]:
    """Every case from every file, each tagged with its category and source
    file so a failure names something you can open."""
    d = Path(directory or CASES_DIR)
    if not d.is_dir():
        raise FixtureError(f"no fixture directory at {d}")
    out: List[dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name}: {exc}") from exc
        category = doc.get("category")
        if not category:
            raise FixtureError(f"{path.name}: no `category`")
        for case in doc.get("cases") or []:
            if "id" not in case:
                raise FixtureError(f"{path.name}: a case has no `id`")
            if "record" not in case and "replay" not in case:
                raise FixtureError(f"{case['id']}: needs a `record` or a `replay`")
            out.append({**case, "category": category, "file": path.name})
    if not out:
        raise FixtureError(f"no cases found in {d}")
    return out


def _check_classification(case: dict, rules) -> dict:
    rec = case["record"]
    ctx = case.get("context") or {}
    v = classify(rec.get("text", ""), rules,
                 title=rec.get("title", ""),
                 context=ctx.get("text", ""), context_title=ctx.get("title", ""))
    want = case["expect"]
    misses = []
    if v.audience != want["audience"]:
        misses.append(f"audience {v.audience} != {want['audience']}")
    if "ambiguous" in want and v.ambiguous != want["ambiguous"]:
        misses.append(f"ambiguous {v.ambiguous} != {want['ambiguous']}")
    if "inherited" in want and v.inherited != want["inherited"]:
        misses.append(f"inherited {v.inherited} != {want['inherited']}")
    # A failure case must also still be a failure by the time it is stored:
    # the classifier does not get to rescue a record whose fetch went wrong.
    if want.get("run_status") and rec.get("run_status") != want["run_status"]:
        misses.append(f"run_status {rec.get('run_status')} != {want['run_status']}")
    return {
        "kind": "classify",
        "got": f"{v.audience}{' (amb)' if v.ambiguous else ''}"
               f"{' (inherited)' if v.inherited else ''}",
        "want": f"{want['audience']}{' (amb)' if want.get('ambiguous') else ''}"
                f"{' (inherited)' if want.get('inherited') else ''}",
        "confidence": v.confidence,
        "detail": v.reason,
        "misses": misses,
    }


def _check_replay(case: dict, rules, db_path: str) -> dict:
    """Run the record through several sightings against a real table."""
    db = open_signals(db_path)
    rid = case["id"].replace("-", "_")[:40]
    stats = []
    for step in case["replay"]:
        if step.get("remove_at"):
            mark_removed(db, [f"reddit:{rid}"], at=step["remove_at"])
            stats.append({"removed": 1})
            continue
        s = Signal(source_id=rid, community="r/synthetic", query="fixture",
                   text=step["text"], mode="fixture", run_id="fixture-replay")
        stats.append(upsert(db, [s], seen_at=step["seen_at"]))

    row = db.execute(
        f"SELECT * FROM {TABLE} WHERE record_id = ?", (f"reddit:{rid}",)).fetchone()
    rows = db.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    want = case["expect"]
    misses = []

    def cmp(label, actual, expected):
        if actual != expected:
            misses.append(f"{label} {actual!r} != {expected!r}")

    if "rows" in want:
        cmp("rows", rows, want["rows"])
    if row is None:
        misses.append("the row is gone — a removal must be recorded, not deleted")
    else:
        if "revisions" in want:
            cmp("revisions", row["revisions"], want["revisions"])
        if "edited" in want:
            cmp("edited", bool(row["edited_utc"]), want["edited"])
        if "removed" in want:
            cmp("removed", bool(row["removed_utc"]), want["removed"])
        for field in ("first_seen_utc", "last_seen_utc", "edited_utc", "removed_utc"):
            if field in want:
                cmp(field, row[field], want[field])
        if "excerpt_contains" in want and want["excerpt_contains"] not in (row["excerpt"] or ""):
            misses.append(f"excerpt does not contain {want['excerpt_contains']!r}")
    if "upsert" in want:
        got = [{k: s.get(k) for k in ("new", "seen_again", "edited")}
               for s in stats if "new" in s]
        cmp("upsert", got, want["upsert"])

    summary = (f"{rows} row, revisions {row['revisions']}, "
               f"edited {bool(row['edited_utc'])}, removed {bool(row['removed_utc'])}"
               if row is not None else "no row")
    # Windows will not delete a temp directory that still holds an open
    # handle, so the connection is closed before the caller cleans up.
    db.close()
    return {"kind": "replay", "got": summary, "want": json.dumps(want, sort_keys=True)[:60],
            "confidence": None, "detail": case.get("why", ""), "misses": misses}


def check(rules=None, directory: Path | str | None = None) -> dict:
    """Grade every case. Returns the table the CLI prints and the test asserts.

    Coverage is graded too: the gate names five categories and twenty cases,
    and a set that has quietly lost its failure cases still reads 100%.
    """
    rules = rules or load_rules()
    cases = load_cases(directory)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, case in enumerate(cases):
            if "replay" in case:
                # One database per replay case: they assert on total row count,
                # so they must not see each other's rows.
                out = _check_replay(case, rules, str(Path(tmp) / f"r{i}.db"))
            else:
                out = _check_classification(case, rules)
            results.append({**out, "id": case["id"], "category": case["category"],
                            "file": case["file"], "pass": not out["misses"]})

    have = {c["category"] for c in cases}
    coverage = [c for c in REQUIRED_CATEGORIES if c not in have]
    return {
        "cases": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": [r["id"] for r in results if not r["pass"]],
        "categories": sorted(have),
        "missing_categories": coverage,
        "short_by": max(0, REQUIRED_TOTAL - len(results)),
    }
