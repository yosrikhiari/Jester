"""Scoring agreement (V3-13): the owner's scores against the model's, per axis.

The scorer's numbers are only evidence once a human has scored the same ideas.
This module measures the gap; it never fills one in. Scores are JSONL, one row
per idea::

    {"idea_id": 12,
     "human": {"overall": 7.0, "demand": 6.0, "feasibility": 8.0,
               "competition": 5.0},
     "model": {"overall": 6.5, "demand": 6.0, "feasibility": 7.0,
               "competition": 5.5}}

The axes are whatever keys appear in BOTH mappings — the four above are the
documented ones. A null, missing or non-numeric score is skipped for that axis,
never imputed: a human who did not score competition did not score it as 0.
Each axis is reported as mean absolute error with its own denominator, and one
row counts as compared when at least one axis is comparable.

A malformed row (not an object, or no `human`/`model` mapping) is skipped and
counted, so a report can say how much of the file it did not use. A row that
has both mappings but no comparable axis (no shared key, or every shared value
null or non-numeric) is counted as uncompared, so every read row lands in
exactly one of the three buckets and the printed totals reconcile. A shared
axis whose value on either side is not a number is counted as an unscored pair:
a file of `"7"` strings reads as 30 rows, 0 compared, 0 skipped, 120 unscored
pairs, not as silence.
"""
import json
from collections import defaultdict

#: The documented axes, in report order. Any other shared key follows, sorted.
AXES = ("overall", "demand", "feasibility", "competition")

#: How many compared ideas the evaluation plan asks for before the number is
#: treated as measured rather than pending.
DEFAULT_MIN = 30

#: The one-line row the instructions show when no file exists yet. Built by
#: `json.dumps` in `example_row()` so it is always valid JSON.
_EXAMPLE = {
    "idea_id": 12,
    "human": {"overall": 7.0, "demand": 6.0, "feasibility": 8.0,
              "competition": 5.0},
    "model": {"overall": 6.5, "demand": 6.0, "feasibility": 7.0,
              "competition": 5.5},
}


def example_row() -> str:
    return json.dumps(_EXAMPLE, sort_keys=False)


def _number(value):
    """A score, or None. A bool is not a score and neither is "n/a"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def load_rows(path):
    """Parsed JSONL rows. A line that is not JSON comes back as None.

    The report counts that as a skipped row: a broken line is one unusable
    row, not a reason to lose the whole file or to guess what it meant.
    """
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append(None)
    return rows


def _axis_order(seen):
    documented = [axis for axis in AXES if axis in seen]
    return documented + sorted(axis for axis in seen if axis not in AXES)


def evaluate_agreement(rows, *, min_rows=DEFAULT_MIN) -> dict:
    """MAE per shared axis, each with the number of pairs it was computed on."""
    sums = defaultdict(float)
    counts = defaultdict(int)
    seen = set()
    n_compared = n_skipped = n_uncompared = n_unscored = 0

    for row in rows:
        if not isinstance(row, dict):
            n_skipped += 1
            continue
        human, model = row.get("human"), row.get("model")
        if not isinstance(human, dict) or not isinstance(model, dict):
            n_skipped += 1
            continue
        compared = False
        for axis, human_value in human.items():
            if axis not in model:
                continue
            seen.add(axis)
            h, m = _number(human_value), _number(model[axis])
            if h is None or m is None:
                n_unscored += 1
                continue
            sums[axis] += abs(h - m)
            counts[axis] += 1
            compared = True
        if compared:
            n_compared += 1
        else:
            n_uncompared += 1

    axes = {
        axis: {"mae": sums[axis] / counts[axis] if counts[axis] else None,
               "n": counts[axis]}
        for axis in _axis_order(seen)
    }
    required = max(0, int(min_rows))
    return {
        "axes": axes,
        "n_rows": len(rows),
        "n_compared": n_compared,
        "n_skipped": n_skipped,
        "n_uncompared": n_uncompared,
        "n_unscored": n_unscored,
        "min_rows": required,
        "measurable": n_compared >= required,
    }


def _mae(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_report(report, *, labels_path) -> str:
    lines = [
        f"scoring agreement ({labels_path})",
        f"  rows = {report['n_rows']} read, {report['n_compared']} compared, "
        f"{report['n_skipped']} skipped, {report['n_uncompared']} uncompared",
    ]
    if report["n_unscored"]:
        lines.append(f"  unscored = {report['n_unscored']} shared axis pair(s) "
                     f"with a null or non-numeric score")
    if not report["axes"]:
        lines.append("  no axis appears in both the human and the model scores")
    for axis, stats in report["axes"].items():
        lines.append(f"  {axis}: MAE = {_mae(stats['mae'])} "
                     f"({stats['n']} pairs)")
    lines.append(f"  require {report['min_rows']} compared idea(s)")
    return "\n".join(lines)


__all__ = [
    "AXES", "DEFAULT_MIN", "evaluate_agreement", "example_row",
    "format_report", "load_rows",
]
