"""F-06: dedup threshold calibration over labelled pairs.

Offline and deterministic: embed every distinct text once with the configured
backend, score each pair by cosine, and report precision/recall/F1 across the
sweep range. The point is replacing the single global 0.87 with a number that
has a table behind it — per category, once pair files exist for them.

Pair file: JSONL, one object per line:
  {"a": "first text", "b": "second text", "same": true}

A DIRECTORY of pair files (`sweep_over_pairs_dir`, wired to
`jester dedup-sweep --pairs-dir`) reads one file per category, each named
`<category>.jsonl`, and reports one annotated table per file so a category's
proposed bar is judged on ITS labelled material, not on the archive average.

Malformed lines are skipped and counted, never fatal: a hand-built label file
with one bad row should still calibrate on the rest.

A file below the pair floor does not get a table printed; it gets the refusal
printed. Precision and recall over 8 labelled pairs describe the labeler's
luck, not the threshold.
"""
import json
import math
from pathlib import Path

SWEEP_START = 0.80
SWEEP_STOP = 0.92
SWEEP_STEP = 0.01

#: The pair floor the CLI has always defaulted `--min-pairs` to. One constant
#: so the sweep driver refuses on the same floor the argparse documents.
DEFAULT_MIN_PAIRS = 20


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def default_thresholds(configured=None):
    """Sweep grid 0.80..0.92 plus the configured threshold when it falls
    outside the grid, so the current setting always has a row to defend."""
    grid = []
    t = SWEEP_START
    while t <= SWEEP_STOP + 1e-9:
        grid.append(round(t, 2))
        t = round(t + SWEEP_STEP, 10)
    if configured is not None and round(float(configured), 2) not in grid:
        grid.append(round(float(configured), 2))
        grid.sort()
    return grid


def load_pairs(path):
    """Returns (pairs, skipped). pairs is [(a, b, same), ...]."""
    pairs, skipped = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                a, b, same = obj["a"], obj["b"], obj["same"]
                if not isinstance(a, str) or not isinstance(b, str):
                    raise ValueError("a/b must be strings")
                pairs.append((a, b, bool(same)))
            except (ValueError, KeyError, TypeError, AttributeError):
                skipped += 1
    return pairs, skipped


def sweep(pairs, embed, thresholds=None):
    """Score pairs once, evaluate every threshold. Pure: no I/O, no network
    beyond what `embed` itself does."""
    thresholds = list(thresholds) if thresholds else default_thresholds()
    if not pairs:
        return {"rows": [], "best": None, "n_pairs": 0, "note": "no pairs"}
    # One embed call for the whole file: distinct texts, stable order.
    order, seen = [], set()
    for a, b, _s in pairs:
        for t in (a, b):
            if t not in seen:
                seen.add(t)
                order.append(t)
    vecs = embed.embed(order)
    if len(vecs) != len(order):
        raise ValueError(
            f"embedder returned {len(vecs)} vectors for {len(order)} texts")
    by_text = dict(zip(order, vecs))
    scored = [(cosine(by_text[a], by_text[b]), same) for a, b, same in pairs]
    rows = []
    for t in thresholds:
        tp = sum(1 for s, same in scored if same and s >= t)
        fp = sum(1 for s, same in scored if not same and s >= t)
        fn = sum(1 for s, same in scored if same and s < t)
        tn = sum(1 for s, same in scored if not same and s < t)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        rows.append({"threshold": t, "precision": round(precision, 3),
                     "recall": round(recall, 3), "f1": round(f1, 3),
                     "tp": tp, "fp": fp, "fn": fn, "tn": tn})
    best = max(rows, key=lambda r: (r["f1"], -r["threshold"]))
    return {"rows": rows, "best": best, "n_pairs": len(pairs)}


def sweep_over_pairs_dir(path, embed, *, thresholds=None,
                         min_pairs: int = DEFAULT_MIN_PAIRS,
                         categories=None) -> dict:
    """Per-category driver: one labelled pair file per category.

    Reads `<path>/<category>.jsonl` for every file present (or just the ones
    named in `categories`) and returns {category: result} sorted by category.
    Each result carries `n_pairs`, `skipped`, `rows`, `best` and `refused`. A
    file at or above the floor gets the real sweep table; a thin one gets
    `refused: True` with empty rows and the command prints the refusal
    instead of a table of noise. The flag is always present on every result.
    """
    d = Path(path)
    if not d.is_dir():
        raise FileNotFoundError(f"no pairs directory at {d}")
    wanted = sorted(categories) if categories else None
    out: dict = {}
    for f in d.glob("*.jsonl"):
        category = f.stem
        if wanted is not None and category not in wanted:
            continue
        pairs, skipped = load_pairs(f)
        result = {
            "category": category,
            # An at-or-above-the-floor file gets the real sweep table; a thin
            # one gets rows=[] and the refusal flag, and the command prints
            # the honest message instead of a table of noise.
            "rows": [],
            "best": None,
            "n_pairs": len(pairs),
            "skipped": skipped,
            "refused": len(pairs) < min_pairs,
        }
        if not result["refused"]:
            live = sweep(pairs, embed, thresholds)
            result["rows"], result["best"] = live["rows"], live["best"]
        out[category] = result
    return out
