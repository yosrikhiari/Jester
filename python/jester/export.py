"""CSV export of everything the pipeline scraped and extracted.

The archive is a SQLite file, which is the right store for the pipeline and the
wrong one for a human who wants to sort, pivot or hand a subset to someone else.
This writes the same data as CSV, split the two ways that actually matter when
you go looking for something — one folder per origin, one per content type —
with each folder sharded so no single sheet grows past what a spreadsheet can
open:

    exports/comments/reddit/reddit-001.csv, reddit-002.csv, …
    exports/comments/hackernews/hackernews-001.csv
    exports/nuggets/reddit/reddit-001.csv
    exports/nuggets/by-category/pain_point/pain_point-001.csv
    exports/ideas/ideas-001.csv, citations-001.csv
    exports/duplicates.csv                 what the dedup pass collapsed

**Nothing is written twice.** The archive can legitimately hold the same words
more than once — a crosspost, a quoted reply, the same question asked on two
forums — but an export that repeats them is worse than useless both for reading
and for training on. Each content type is collapsed on its own normalised text
before any file is written, globally rather than per-folder, and every drop is
recorded in `duplicates.csv` so the export holding fewer rows than the archive
is an explained number rather than a discrepancy someone has to chase.

**§14 retention applies here too.** `raw_text` copied into a CSV would outlive
the sweep that wipes it from the database, so the exporter stamps a manifest and
`retention_sweep` prunes export directories past the same TTL. An export is a
view of the archive, not a way around its retention policy.
"""
import csv
import json
import re
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

# One place that decides what a CSV of each thing looks like. Column order is
# fixed so a diff between two exports is readable.
COMMENT_COLUMNS = [
    "platform", "source_url", "thread_id", "fingerprint", "body",
    "upvotes", "run_id", "batch_status", "batch_created_at",
]
# A CSV that names only the score is a CSV that cannot answer "who said this
# and when", which is the first question anyone asks of a scraped corpus.
#
# The vote columns are EMPTY, not 0, where a platform does not publish the
# figure — Reddit per-comment downvotes, YouTube dislikes, Hacker News comment
# scores. A spreadsheet full of zeroes would read as "measured, and nobody
# voted"; a blank reads as what it is.
NUGGET_COLUMNS = [
    "unique_key", "platform", "category", "thread_id", "source_url",
    "extracted_insight", "raw_text", "engagement_score", "trivial",
    "needs_reembed", "run_id", "synthesized_at", "created_at",
    # who / when / where
    "author", "author_url", "comment_url", "comment_id", "parent_id", "depth",
    "created_utc", "created_raw",
    # how it was received
    "upvotes", "downvotes", "likes", "dislikes", "replies", "awards", "reads",
    "edited", "pinned", "author_is_op", "distinguished", "accepted_answer",
    # the thread it came from
    "community", "post_title", "post_url", "post_author", "post_created_utc",
    "post_score", "post_upvote_ratio", "post_comment_count", "post_views",
    "extra",
]
IDEA_COLUMNS = [
    "id", "title", "problem_statement", "proposed_solution",
    "demand_signal", "feasibility", "competition", "competition_checked",
    "overall", "status", "competitor_notes", "source_threads",
    "source_platforms", "supporting_nugget_count", "critic_model",
    "synthesis_model", "run_id", "last_scored_at", "created_at",
]
CITATION_COLUMNS = [
    "idea_id", "idea_title", "nugget_key", "resolved",
    "nugget_platform", "nugget_category", "nugget_insight",
]
# The audit trail for content collapsed by the dedup pass.
DUPLICATE_COLUMNS = [
    "kind", "kept_id", "kept_origin", "dropped_id", "dropped_origin",
    "reason", "content_preview",
]

MANIFEST = "manifest.json"


def _slug(value, fallback="unknown"):
    """A filename-safe origin/category key. Never empty, never a path."""
    text = str(value or "").strip().lower()
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in text)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or fallback


def _write(path: Path, columns, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel on Windows misreads plain UTF-8 CSVs as cp1252, which
    # mangles every non-ASCII character in scraped text.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        n = 0
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
            n += 1
    return n


# Rows per CSV before rolling to the next shard. Excel stops at ~1,048,576
# rows and gets unusable long before that; 100k opens quickly and still keeps
# the file count small.
DEFAULT_ROWS_PER_FILE = 100_000

_WS = re.compile(r"\s+")


def _norm(text) -> str:
    """Normalise text for duplicate detection.

    Case and whitespace are formatting, not content: the same complaint pasted
    twice with a different line wrap is still the same complaint.
    """
    return _WS.sub(" ", str(text or "")).strip().lower()


def dedup(rows, key_fn, kind, origin_fn=None, id_fn=None):
    """Drop rows whose *content* already appeared. Returns (kept, dropped).

    Deduplication is global rather than per-file: the same text crossposted to
    two subreddits is repeated content wherever it lands, and a per-origin pass
    would happily write it twice. Every drop is recorded so the gap between the
    archive and the export is auditable instead of silent.
    """
    seen = {}
    kept, dropped = [], []
    for row in rows:
        key = key_fn(row)
        if not key:
            # Nothing to compare on: keep it rather than collapse unrelated
            # blank-content rows into one.
            kept.append(row)
            continue
        first = seen.get(key)
        if first is None:
            seen[key] = row
            kept.append(row)
            continue
        dropped.append({
            "kind": kind,
            "kept_id": id_fn(first) if id_fn else "",
            "kept_origin": origin_fn(first) if origin_fn else "",
            "dropped_id": id_fn(row) if id_fn else "",
            "dropped_origin": origin_fn(row) if origin_fn else "",
            "reason": "identical content",
            "content_preview": key[:160],
        })
    return kept, dropped


def _write_shards(directory: Path, prefix: str, columns, rows, rows_per_file: int):
    """Write rows as `<prefix>-001.csv`, `-002.csv`, … inside `directory`.

    Always at least one file, so an origin with no rows still shows up as an
    empty sheet with headers rather than a missing folder that reads as "this
    source was never fetched".
    """
    rows_per_file = max(1, int(rows_per_file or DEFAULT_ROWS_PER_FILE))
    written = []
    chunks = [rows[i:i + rows_per_file] for i in range(0, len(rows), rows_per_file)] or [[]]
    for index, chunk in enumerate(chunks, start=1):
        path = directory / f"{prefix}-{index:03d}.csv"
        written.append((path, _write(path, columns, chunk)))
    return written


def _rowdict(row):
    return {k: row[k] for k in row.keys()}


def _group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


# ---- readers ---------------------------------------------------------------


def read_comments(db):
    """Raw scraped comments, unpacked from the ingest_batch JSON payloads.

    Batches are read whatever their status: a `done` batch is still the record
    of what was scraped, and dropping it would make the export a view of the
    queue rather than of the haul.
    """
    import sqlite3

    db.row_factory = sqlite3.Row
    out = []
    for b in db.execute(
        "SELECT platform, source, thread_id, comments, status, created_at, run_id "
        "FROM ingest_batch ORDER BY id"
    ):
        try:
            payload = json.loads(b["comments"] or "null")
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, list):
            continue  # malformed batch: counted nowhere, invented nowhere
        for c in payload:
            if not isinstance(c, dict):
                continue
            out.append({
                "platform": b["platform"] or "",
                "source_url": b["source"] or "",
                "thread_id": b["thread_id"] or "",
                "fingerprint": c.get("fingerprint", ""),
                "body": c.get("body", ""),
                "upvotes": c.get("upvotes", c.get("score", "")),
                "run_id": b["run_id"] or "",
                "batch_status": b["status"] or "",
                "batch_created_at": b["created_at"] or "",
            })
    return out


def read_nuggets(db):
    from jester.store import list_nuggets

    return [_rowdict(r) for r in list_nuggets(db)]


def read_ideas(db):
    from jester.store import list_ideas

    rows = []
    for r in list_ideas(db):
        d = _rowdict(r)
        keys = json.loads(d.get("supporting_nuggets") or "[]")
        d["supporting_nugget_count"] = len(keys)
        d["source_platforms"] = ", ".join(json.loads(d.get("source_platforms") or "[]"))
        d["competition_checked"] = "yes" if d.get("competition_checked") else "no"
        # R29: an unchecked competition is NULL, and must export as empty —
        # writing the imputed 5 here would launder a guess into a spreadsheet.
        if not r["competition_checked"]:
            d["competition"] = ""
        rows.append(d)
    return rows


def read_citations(db):
    import sqlite3

    db.row_factory = sqlite3.Row
    out = []
    for idea in db.execute("SELECT id, title, supporting_nuggets FROM ideas ORDER BY id"):
        for key in json.loads(idea["supporting_nuggets"] or "[]"):
            n = db.execute(
                "SELECT platform, category, extracted_insight FROM nuggets WHERE unique_key=?",
                (key,),
            ).fetchone()
            out.append({
                "idea_id": idea["id"],
                "idea_title": idea["title"] or "",
                "nugget_key": key,
                # A dangling citation is a real archive defect (doctor alarms on
                # it); the export shows it rather than dropping the row.
                "resolved": "yes" if n else "no",
                "nugget_platform": n["platform"] if n else "",
                "nugget_category": n["category"] if n else "",
                "nugget_insight": n["extracted_insight"] if n else "",
            })
    return out


# ---- the export ------------------------------------------------------------


def export_csv(db, out_dir, *, now=None, rows_per_file: int = DEFAULT_ROWS_PER_FILE) -> dict:
    """Write the whole archive as CSV. Returns a manifest of what was written."""
    out_dir = Path(out_dir)
    now = now or datetime.now(timezone.utc)

    comments = read_comments(db)
    nuggets = read_nuggets(db)
    ideas = read_ideas(db)
    citations = read_citations(db)

    # ---- deduplicate before anything is written ---------------------------
    # The archive can legitimately hold the same words twice (a crosspost, a
    # quoted reply, the same question asked on two forums). An export that
    # repeats them is worse than useless for reading or for training on, so
    # each content type is collapsed on its own text.
    raw_counts = {
        "comments": len(comments), "nuggets": len(nuggets),
        "ideas": len(ideas), "citations": len(citations),
    }
    duplicates = []
    comments, dropped = dedup(
        comments, lambda r: _norm(r.get("body")), "comment",
        origin_fn=lambda r: r.get("platform", ""),
        id_fn=lambda r: r.get("fingerprint", ""))
    duplicates += dropped
    nuggets, dropped = dedup(
        nuggets, lambda r: _norm(r.get("extracted_insight")) or _norm(r.get("raw_text")),
        "nugget",
        origin_fn=lambda r: r.get("platform", ""),
        id_fn=lambda r: r.get("unique_key", ""))
    duplicates += dropped
    ideas, dropped = dedup(
        ideas, lambda r: _norm(r.get("title")) + "|" + _norm(r.get("problem_statement")),
        "idea",
        origin_fn=lambda r: r.get("source_platforms", ""),
        id_fn=lambda r: str(r.get("id", "")))
    duplicates += dropped
    # Citations pointing at a collapsed nugget would dangle; keep the join
    # honest by re-pointing nothing and simply dropping exact repeats.
    citations, dropped = dedup(
        citations, lambda r: f"{r.get('idea_id')}|{r.get('nugget_key')}", "citation",
        origin_fn=lambda r: r.get("nugget_platform", ""),
        id_fn=lambda r: str(r.get("idea_id", "")))
    duplicates += dropped

    files = {}
    rows_per_file = int(rows_per_file or DEFAULT_ROWS_PER_FILE)

    def record(written):
        for path, n in written:
            files[str(path.relative_to(out_dir)).replace("\\", "/")] = n

    # ---- by origin: one folder per source, sharded ------------------------
    for platform, rows in sorted(_group(comments, lambda r: _slug(r["platform"])).items()):
        record(_write_shards(out_dir / "comments" / platform, platform,
                             COMMENT_COLUMNS, rows, rows_per_file))
    for platform, rows in sorted(_group(nuggets, lambda r: _slug(r.get("platform"))).items()):
        record(_write_shards(out_dir / "nuggets" / platform, platform,
                             NUGGET_COLUMNS, rows, rows_per_file))

    # ---- by content type: "every data-loss complaint, whatever the source"
    for category, rows in sorted(
        _group(nuggets, lambda r: _slug(r.get("category"), "uncategorised")).items()
    ):
        record(_write_shards(out_dir / "nuggets" / "by-category" / category, category,
                             NUGGET_COLUMNS, rows, rows_per_file))

    # ideas span platforms by construction (that is the point of synthesis), so
    # they are one set plus the citation join that makes the export relational.
    record(_write_shards(out_dir / "ideas", "ideas", IDEA_COLUMNS, ideas, rows_per_file))
    record(_write_shards(out_dir / "ideas", "citations", CITATION_COLUMNS,
                         citations, rows_per_file))

    # The audit trail for every collapsed row: which id was kept, which was
    # dropped, and a preview of the shared text. Without it the export would
    # silently hold fewer rows than the archive.
    if duplicates:
        _write(out_dir / "duplicates.csv", DUPLICATE_COLUMNS, duplicates)
        files["duplicates.csv"] = len(duplicates)

    manifest = {
        "exported_at": now.isoformat(),
        "rows_per_file": rows_per_file,
        "counts": {
            "comments": len(comments),
            "nuggets": len(nuggets),
            "ideas": len(ideas),
            "citations": len(citations),
        },
        # Reported separately so "we exported fewer rows than the archive holds"
        # is an explained number rather than a discrepancy someone has to chase.
        "counts_before_dedup": raw_counts,
        "duplicates_dropped": len(duplicates),
        "files": files,
        # Named so a reader knows the retention clock applies to this directory
        # too — §14 does not stop at the database boundary.
        "retention_note": (
            "Contains raw scraped text. jester retention prunes export "
            "directories past the same TTL as the archive (§14)."
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MANIFEST).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def default_export_dir(db_path: str) -> str:
    """Exports live beside the database they came from."""
    if db_path == ":memory:":
        return os.path.abspath("exports")
    base = os.path.abspath(db_path)
    return os.path.join(os.path.dirname(base), "exports")


def prune_exports(out_dir, ttl_days: int = 30, now=None) -> int:
    """§14: drop export directories whose manifest is past the TTL.

    Without this, `jester retention` would wipe raw_text from the archive while
    a CSV copy of the same text sat next to it — a retention policy with a hole
    in it is not a retention policy.
    """
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return 0
    now = now or datetime.now(timezone.utc)
    manifest = out_dir / MANIFEST
    if not manifest.exists():
        return 0
    try:
        stamp = json.loads(manifest.read_text(encoding="utf-8")).get("exported_at")
        when = datetime.fromisoformat(str(stamp))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - an unreadable stamp is treated as stale
        when = None
    if when is not None and (now - when).days < ttl_days:
        return 0
    shutil.rmtree(out_dir, ignore_errors=True)
    return 1
