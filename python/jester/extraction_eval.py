"""Extraction quality (S-06): sample, label bookkeeping, κ, P/R/F1, fabrication.

The archive is frozen, so the evaluation substrate is free: draw a
deterministic stratified sample once, label it by hand, and report every
aggregate with its denominator. Nothing here calls a model.

Labels are JSONL. One row is one rater's verdict on one comment::

    {"unique_key": "reddit:sub:comment",
     "platform": "reddit",
     "rater": "owner",
     "keep": true,
     "gold": ["the backup job fails silently"],
     "predicted": [{"insight": "backups fail silently", "verdict": "supported"},
                   {"insight": "restores were never tested", "verdict": "fabricated"}]}

`keep` is the per-comment decision two raters are compared on (Cohen's κ).
`gold` is what the human found; `predicted` is what the extractor produced,
each with the labeller's verdict. `unchecked` is a verdict, not a guess: it is
counted and reported beside the aggregate, never imputed.
"""
import json
import random
import sys
from collections import defaultdict, Counter

DEFAULT_SEED = 20261031

#: Where a length bucket starts. Forum comments are short; HN/Discourse prose
#: runs long enough that one bucket would hide the difference.
LENGTH_BUCKETS = (("short", 280), ("medium", 1000), ("long", None))

SUPPORTED = "supported"
FABRICATED = "fabricated"
UNCHECKED = "unchecked"

_VERDICT_ALIASES = {
    "supported": SUPPORTED, "ok": SUPPORTED, "correct": SUPPORTED,
    "fabricated": FABRICATED, "unsupported": FABRICATED,
    "hallucinated": FABRICATED,
    "unchecked": UNCHECKED, "": UNCHECKED, "unknown": UNCHECKED,
}


def length_bucket(chars: int) -> str:
    for name, limit in LENGTH_BUCKETS:
        if limit is None or chars <= limit:
            return name
    return "long"


def _text(row) -> str:
    return (row.get("text") or row.get("raw_text") or "").strip()


def stratum_of(row) -> str:
    """Platform × category × length bucket — the strata the sample is drawn on."""
    return (f"{row.get('platform') or 'unknown'}|"
            f"{row.get('category') or 'uncategorised'}|"
            f"{length_bucket(len(_text(row)))}")


def stratified_sample(rows, sizes, seed: int = DEFAULT_SEED):
    """Draw `sizes[stratum]` rows per stratum, deterministically.

    The same seed and the same patch always produce the same ids. Rows are
    ordered by `unique_key` before sampling and the RNG is consumed in sorted
    stratum order, so a new row added to the pool cannot reshuffle the others.
    A stratum with fewer candidates yields all of them; the report shows the
    shortfall rather than silently topping it up from another stratum.
    """
    groups = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        row["stratum"] = stratum_of(row)
        row["chars"] = len(_text(row))
        groups[row["stratum"]].append(row)

    rng = random.Random(seed)
    out = []
    for stratum in sorted(sizes):
        want = max(0, int(sizes[stratum] or 0))
        pool = sorted(groups.get(stratum, []), key=lambda r: r["unique_key"])
        drawn = rng.sample(pool, min(want, len(pool))) if pool and want else []
        out.extend(sorted(drawn, key=lambda r: r["unique_key"]))
    return out


def sample_from_db(db, sizes, seed: int = DEFAULT_SEED):
    """Read-only sample over the archive. The caller chooses the strata sizes."""
    rows = db.execute(
        "SELECT unique_key, platform, category, raw_text "
        "FROM nuggets WHERE raw_text IS NOT NULL AND raw_text != '' "
        "ORDER BY unique_key").fetchall()
    return stratified_sample(
        ({"unique_key": r[0], "platform": r[1], "category": r[2],
          "text": r[3]} for r in rows), sizes, seed=seed)


def cohens_kappa(pairs):
    """Cohen's κ for paired binary decisions. None when only one class occurs.

    Percent agreement is not agreement with chance; this is the number the
    literature asks for (Gilardi 2023).
    """
    pairs = [(int(a), int(b)) for a, b in pairs]
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    p_a = sum(a for a, _ in pairs) / n
    p_b = sum(b for _, b in pairs) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


def _verdict(value) -> str:
    key = str(value or "").strip().lower()
    return _VERDICT_ALIASES.get(key, UNCHECKED)


def _gold_count(row) -> int:
    gold = row.get("gold")
    if isinstance(gold, int):
        return max(0, gold)
    return len(gold or [])


def evaluate(labels):
    """Every aggregate over one label set, with its denominator.

    A comment may be labelled twice (κ). Predictions and gold are counted once
    per `unique_key`; the repeat is only a second opinion on `keep`.
    """
    by_key = {}
    for row in labels:
        by_key.setdefault(row["unique_key"], row)

    predicted = supported = fabricated = unchecked = gold = 0
    for row in by_key.values():
        gold += _gold_count(row)
        for item in row.get("predicted") or []:
            predicted += 1
            verdict = _verdict(item.get("verdict") if isinstance(item, dict) else item)
            if verdict == SUPPORTED:
                supported += 1
            elif verdict == FABRICATED:
                fabricated += 1
            else:
                unchecked += 1

    checked = supported + fabricated
    precision = supported / checked if checked else None
    recall = supported / gold if gold else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and (precision + recall)
          else None)

    ratings = defaultdict(dict)
    for row in labels:
        if row.get("rater") and "keep" in row:
            ratings[row["unique_key"]][row["rater"]] = bool(row["keep"])
    pairs = [tuple(int(v) for v in list(raters.values())[:2])
             for raters in ratings.values() if len(raters) >= 2]

    return {
        "n_comments": len(by_key),
        "n_label_rows": len(labels),
        "predicted": predicted,
        "supported": supported,
        "fabricated": fabricated,
        "unchecked": unchecked,
        "checked": checked,
        "gold": gold,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fabrication_rate": fabricated / predicted if predicted else None,
        "unchecked_rate": unchecked / predicted if predicted else None,
        "nuggets_per_comment": predicted / len(by_key) if by_key else None,
        "kappa": cohens_kappa(pairs),
        "double_labelled": len(pairs),
    }


def _pct(value) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _num(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_report(report, *, seed: int, labels_path) -> str:
    def ratio(n, d):
        return f"{n}/{d}" if d else f"{n}/0"

    lines = [
        f"extraction quality ({labels_path})",
        f"  n = {report['n_comments']} comment(s), "
        f"{report['predicted']} predicted nugget(s)",
        f"  kappa = {_num(report['kappa'])} "
        f"({report['double_labelled']} double-labelled)",
        f"  precision = {_pct(report['precision'])} "
        f"({ratio(report['supported'], report['checked'])} checked)",
        f"  recall = {_pct(report['recall'])} "
        f"({ratio(report['supported'], report['gold'])} gold)",
        f"  F1 = {_num(report['f1'])}",
        f"  fabrication = {_pct(report['fabrication_rate'])} "
        f"({ratio(report['fabricated'], report['predicted'])})",
        f"  unchecked = {_pct(report['unchecked_rate'])} "
        f"({ratio(report['unchecked'], report['predicted'])})",
        f"  nuggets/comment = {_num(report['nuggets_per_comment'])}",
        f"  seed={seed}",
    ]
    return "\n".join(lines)


def gate_failures(report, *, min_f1=0.70, max_fabrication=0.05,
                  max_nuggets_per_comment=1.2):
    """The published floors. An absent metric is a failure, not a pass."""
    out = []
    f1 = report.get("f1")
    if f1 is None:
        out.append("F1 is unavailable: the label set has no supported predictions "
                   "or no gold assertions")
    elif f1 < min_f1:
        out.append(f"F1 {f1:.3f} is below the {min_f1:.2f} floor")
    fabrication = report.get("fabrication_rate")
    if fabrication is not None and fabrication > max_fabrication:
        out.append(f"fabrication {fabrication:.1%} is above the "
                   f"{max_fabrication:.0%} ceiling")
    ratio = report.get("nuggets_per_comment")
    if ratio is not None and ratio > max_nuggets_per_comment:
        out.append(f"{ratio:.2f} nuggets/comment is above the "
                   f"{max_nuggets_per_comment:.2f} ceiling")
    return out


def load_labels(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


def write_sample(rows, path) -> None:
    """Write the drawn sample as JSONL, sorted, so the file is reproducible."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in sorted(rows, key=lambda r: r["unique_key"]):
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sample_counts(rows) -> dict:
    return dict(Counter(r["stratum"] for r in rows))


def cmd_sample(db, sizes, out, seed: int = DEFAULT_SEED) -> dict:
    rows = sample_from_db(db, sizes, seed=seed)
    write_sample(rows, out)
    return {"drawn": len(rows), "strata": sample_counts(rows), "seed": seed}


__all__ = [
    "DEFAULT_SEED", "cmd_sample", "cohens_kappa", "evaluate", "format_report",
    "gate_failures", "length_bucket", "load_labels", "sample_counts",
    "sample_from_db", "stratified_sample", "stratum_of", "write_sample",
]
