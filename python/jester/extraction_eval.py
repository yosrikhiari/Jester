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
import hashlib
import json
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


def _chars(row) -> int:
    """Raw length, unstripped.

    The archive draw measures `length(raw_text)` in SQL, which counts every
    character. `.strip()` here would move a padded row across a 280/1000
    boundary and make the two paths disagree about its stratum — and the
    whole point of streaming lengths first is that the ids come out the same.
    """
    return len(row.get("text") or row.get("raw_text") or "")


def stratum_of(row) -> str:
    """Platform × category × length bucket — the strata the sample is drawn on.

    A row may carry `chars` instead of its text: the archive draw reads keys
    and lengths first, because loading every raw_text costs most of a gigabyte
    to choose 150 comments.
    """
    chars = row.get("chars")
    if chars is None:
        chars = _chars(row)
    return (f"{row.get('platform') or 'unknown'}|"
            f"{row.get('category') or 'uncategorised'}|"
            f"{length_bucket(int(chars))}")


def _rank(seed: int, unique_key: str) -> bytes:
    """A row's position in the draw, fixed by the seed and its key alone.

    `hashlib`, not `random`: the draw has to be reproducible from a seed, not
    unpredictable, and a hash rank depends on nothing else — no PRNG
    implementation, no pool length, no consumption order. The seed is public
    and recorded in the design file, which is the whole point.
    """
    return hashlib.sha256(f"{seed}\x00{unique_key}".encode("utf-8")).digest()


def stratified_sample(rows, sizes, seed: int = DEFAULT_SEED):
    """Draw `sizes[stratum]` rows per stratum, deterministically.

    Each row is ranked by `_rank(seed, unique_key)` and the lowest ranks are
    taken, so the order rows arrive in cannot change the draw and adding one
    row can displace at most one already-drawn row. A stratum with fewer
    candidates yields all of them; the report shows the shortfall rather than
    silently topping it up from another stratum.
    """
    groups = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        row["chars"] = (int(row["chars"]) if row.get("chars") is not None
                        else _chars(row))
        row["stratum"] = stratum_of(row)
        groups[row["stratum"]].append(row)

    out = []
    for stratum in sorted(sizes):
        want = max(0, int(sizes[stratum] or 0))
        # Sorted first so a hash collision, however unlikely, resolves the
        # same way on every run.
        pool = sorted(groups.get(stratum, []), key=lambda r: r["unique_key"])
        drawn = sorted(pool, key=lambda r: _rank(seed, r["unique_key"]))[:want]
        out.extend(sorted(drawn, key=lambda r: r["unique_key"]))
    return out


def sample_from_db(db, sizes, seed: int = DEFAULT_SEED):
    """Read-only sample over the archive. The caller chooses the strata sizes.

    Keys and lengths are read first and text only for the rows drawn: the
    archive's raw text is most of a gigabyte, and the draw needs 150 comments,
    not all 115k. The keys still go through `stratified_sample`, so the ids
    are exactly those of a draw over the full rows.
    """
    rows = db.execute(
        "SELECT unique_key, platform, category, length(raw_text) "
        "FROM nuggets WHERE raw_text IS NOT NULL AND raw_text != '' "
        "ORDER BY unique_key").fetchall()
    drawn = stratified_sample(
        ({"unique_key": r[0], "platform": r[1], "category": r[2],
          "chars": r[3] or 0} for r in rows), sizes, seed=seed)
    texts = {}
    keys = [row["unique_key"] for row in drawn]
    for start in range(0, len(keys), 500):
        batch = keys[start:start + 500]
        marks = ",".join("?" * len(batch))
        texts.update(db.execute(
            f"SELECT unique_key, raw_text FROM nuggets "
            f"WHERE unique_key IN ({marks})", batch).fetchall())
    for row in drawn:
        row["text"] = texts.get(row["unique_key"], "")
    return drawn


def strata_counts(db) -> dict:
    """Rows per stratum, read without loading any raw_text."""
    rows = db.execute(
        "SELECT platform, category, length(raw_text) FROM nuggets "
        "WHERE raw_text IS NOT NULL AND raw_text != ''").fetchall()
    return dict(Counter(stratum_of(
        {"platform": r[0], "category": r[1], "chars": r[2] or 0})
        for r in rows))


def proportional_sizes(counts, total: int) -> dict:
    """Allocate `total` rows across strata in proportion to their pools.

    Deterministic: largest remainder for the fractions, ties broken by stratum
    name, one row guaranteed to every non-empty stratum. No stratum is asked
    for more rows than it has, and a shortfall is offered to strata with spare
    capacity rather than silently promised.
    """
    pool = {str(k): int(v) for k, v in (counts or {}).items() if int(v) > 0}
    if not pool or total <= 0:
        return {}
    want = min(int(total), sum(pool.values()))
    by_size = sorted(pool, key=lambda k: (-pool[k], k))
    if want < len(pool):
        # Fewer rows than strata: the largest get one each; the rest honestly
        # get none rather than a fraction.
        return {k: 1 for k in by_size[:want]}

    pool_total = sum(pool.values())
    ideal = {k: want * pool[k] / pool_total for k in pool}
    sizes = {k: max(1, int(ideal[k])) for k in pool}

    # The one-per-stratum floor can overshoot `want` when many strata have a
    # tiny share; take the excess back from the largest allocations first.
    excess = sum(sizes.values()) - want
    while excess > 0:
        donor = min((k for k in pool if sizes[k] > 1),
                    key=lambda k: (-sizes[k], k), default=None)
        if donor is None:
            break
        sizes[donor] -= 1
        excess -= 1

    short = want - sum(sizes.values())
    order = sorted(pool, key=lambda k: (-(ideal[k] - int(ideal[k])), k))
    while short > 0:
        spare = [k for k in order if sizes[k] < pool[k]]
        if not spare:
            break
        for k in spare:
            if short <= 0:
                break
            sizes[k] += 1
            short -= 1
    return sizes


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
                  max_nuggets_per_comment=1.2, min_kappa=0.6):
    """The published floors. An absent metric is a failure, not a pass.

    κ is the exception the rule bends for: it is None when no comment carries
    two ratings, or when every rating is one class (chance agreement is 1, so
    κ is undefined). Neither is evidence of disagreement, so the floor applies
    only when κ was measurable.
    """
    out = []
    kappa = report.get("kappa")
    if kappa is not None and kappa < min_kappa:
        out.append(f"kappa {kappa:.3f} is below the {min_kappa:.2f} floor "
                   f"({report.get('double_labelled', 0)} double-labelled)")
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
    "gate_failures", "length_bucket", "load_labels", "proportional_sizes",
    "sample_counts", "sample_from_db", "strata_counts", "stratified_sample",
    "stratum_of", "write_sample",
]
