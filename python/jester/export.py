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
    exports/listings/property24/property24-001.csv
    exports/listings/tayara/tayara-001.csv
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
import html
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

# Real-estate listings. Flat, because a spreadsheet cannot follow a JSON
# payload, and the portals disagree on vocabulary: Property24 publishes
# "location", Private Property "locality"/"region"/"street", Tayara
# "city"/"governorate". All of them get a column, each portal fills the ones it
# uses, and payload_json carries whatever is left so nothing published is lost
# on the way to CSV.
LISTING_COLUMNS = [
    "portal", "market", "listing_id", "url", "observed_at", "is_latest", "run_id",
    "price", "currency", "deal_type", "status",
    "title", "description", "property_type",
    "location", "locality", "region", "street", "city", "governorate",
    "bedrooms", "bathrooms", "rooms", "surface",
    "seller", "seller_type", "published_at", "latitude", "longitude",
    "media_count", "hashed_media_count", "media_urls",
    "content_hash", "gallery_hash", "first_seen_at", "last_seen_at",
    "payload_json",
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


# Payload keys promoted to their own column. Everything else stays in
# payload_json rather than being silently discarded.
_LISTING_PAYLOAD_KEYS = (
    "title", "description", "property_type", "location", "locality", "region",
    "street", "city", "governorate", "bedrooms", "bathrooms", "rooms",
    "surface", "seller", "seller_type", "published_at", "latitude",
    "longitude", "deal_type",
)


# Portals do not share a vocabulary for the same fact. Property24, Mubawab and
# Behya publish `location`; Private Property publishes `locality` and `region`;
# Tayara publishes `city` and `governorate`; Redfin publishes `street`. Read
# straight through, the combined sheet has a town name in four different
# columns depending on which portal the row came from, and cannot be filtered
# by city at all.
#
# Each canonical column takes the first alias the row actually carries. The
# raw key stays in payload_json either way, so nothing published is lost and
# the mapping is auditable.
_LISTING_ALIASES = {
    "city": ("city", "locality", "location"),
    "region": ("region", "governorate", "province", "state"),
    "street": ("street", "address", "neighborhood", "suburb"),
    "title": ("title", "name", "description"),
}

_WS_RUN = re.compile(r"[ 	  ]+")
_TAGS = re.compile(r"<[^>]*>")
_NULLISH = frozenset({"null", "none", "nan", "undefined", "n/a", "-"})


def _clean(value):
    """Normalise one scraped value for a spreadsheet.

    Scraped text arrives with the page's formatting still attached: entities
    that never got unescaped, tags around a description, non-breaking and
    narrow no-break spaces inside numbers, and the string "null" where a
    portal meant nothing at all. A CSV is read by people and by pandas, and
    both of them treat "null" as a value.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        # A repeated field arrives as a list; the Python repr of one
        # ("['Belhar, Cape Flats', 'Belhar']") is not a cell value.
        value = value[0] if value else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, str):
        return value
    v = html.unescape(value)
    v = _TAGS.sub(" ", v)
    v = v.replace(" ", " ").replace(" ", " ")
    v = _WS_RUN.sub(" ", v).strip()
    # Collapse the newlines a description carries so one listing stays one row
    # to read, without destroying the paragraph breaks entirely.
    v = re.sub(r"\s*\n\s*", " / ", v).strip()
    if v.lower() in _NULLISH:
        return ""
    return v


def _canonical(payload, key):
    """First alias the row actually carries, cleaned."""
    for alias in _LISTING_ALIASES[key]:
        v = _clean(payload.get(alias))
        if v:
            return v
    return ""


def read_listings(db):
    """Every real-estate observation, flattened for a spreadsheet.

    ONE ROW PER OBSERVATION, not per listing, and deliberately so. The archive
    appends an observation every time a listing is seen again, and that append
    IS the price history - collapsing them would throw away the series the
    schema exists to keep. is_latest marks the current state, so a sheet can be
    filtered to "what is on the market now" without losing what it used to cost.

    It is also why listings do not go through dedup() like the other content
    types. Two observations of one listing are not the same row written twice;
    they are the same property at two moments.
    """
    import sqlite3

    db.row_factory = sqlite3.Row
    # A row written before the fetcher transcoded Windows-1252 holds bytes that
    # are not UTF-8, and the driver's default text_factory raises on the whole
    # QUERY rather than the offending value: one Tunisie Annonce URL reading
    # "meublé" took the entire listings export to zero rows, silently, and took
    # five other portals' data with it. Replacing the undecodable bytes keeps
    # the rest of the archive readable; the fetcher stops new ones arriving.
    db.text_factory = lambda b: b.decode("utf-8", "replace")
    try:
        rows = db.execute(
            """SELECT o.id, o.portal, o.listing_id, o.observed_at, o.run_id,
                      o.content_hash, o.gallery_hash, o.price, o.currency,
                      o.status, o.payload, l.url, l.first_seen_at, l.last_seen_at
                 FROM listing_observation o
                 LEFT JOIN listing l
                   ON l.portal = o.portal AND l.listing_id = o.listing_id
                ORDER BY o.portal, o.listing_id, o.id"""
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # ONLY a missing table means "nothing to export". Every other
        # OperationalError - a lock, an undecodable column, a corrupt page - is
        # a real failure, and swallowing it wrote a manifest reporting zero
        # listings over an archive holding 920 of them.
        if "no such table" not in str(exc).lower():
            raise
        return []

    media = {}
    try:
        for m in db.execute(
            "SELECT observation_id, url, phash FROM listing_media"
            " ORDER BY observation_id, position"
        ):
            media.setdefault(m["observation_id"], []).append((m["url"], m["phash"]))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        media = {}

    # Newest observation id per listing, so is_latest is a fact rather than an
    # assumption about row order downstream.
    latest = {}
    for r in rows:
        key = (r["portal"], r["listing_id"])
        if r["id"] >= latest.get(key, -1):
            latest[key] = r["id"]

    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        shots = media.get(r["id"], [])
        row = {
            "portal": r["portal"],
            "market": payload.get("market", ""),
            "listing_id": r["listing_id"],
            "url": _clean(r["url"] or payload.get("url", "")),
            "observed_at": r["observed_at"] or "",
            "is_latest": "true" if latest.get((r["portal"], r["listing_id"])) == r["id"] else "false",
            "run_id": r["run_id"] or "",
            # Blank, not 0, where the portal published no figure. A sheet full
            # of zeroes reads as "free"; a blank reads as "not stated".
            "price": "" if r["price"] is None else r["price"],
            "currency": r["currency"] or "",
            "status": _clean(r["status"]),
            "media_count": len(shots),
            "hashed_media_count": sum(1 for _, ph in shots if ph),
            "media_urls": " ".join(u for u, _ in shots if u),
            "content_hash": r["content_hash"] or "",
            "gallery_hash": r["gallery_hash"] or "",
            "first_seen_at": r["first_seen_at"] or "",
            "last_seen_at": r["last_seen_at"] or "",
        }
        for key in _LISTING_PAYLOAD_KEYS:
            row[key] = _clean(payload.get(key))
        # ...then fill the canonical columns from whichever alias this portal
        # happens to use, so `city` means the same thing on every row.
        for key in _LISTING_ALIASES:
            if not row.get(key):
                row[key] = _canonical(payload, key)
        leftover = {k: v for k, v in payload.items()
                    if k not in _LISTING_PAYLOAD_KEYS and k not in ("url", "market")}
        row["payload_json"] = (
            json.dumps(leftover, ensure_ascii=False, sort_keys=True) if leftover else ""
        )
        out.append(row)
    return out


def export_csv(db, out_dir, *, now=None, rows_per_file: int = DEFAULT_ROWS_PER_FILE) -> dict:
    """Write the whole archive as CSV. Returns a manifest of what was written."""
    out_dir = Path(out_dir)
    now = now or datetime.now(timezone.utc)

    comments = read_comments(db)
    nuggets = read_nuggets(db)
    ideas = read_ideas(db)
    citations = read_citations(db)
    listings = read_listings(db)

    # ---- deduplicate before anything is written ---------------------------
    # The archive can legitimately hold the same words twice (a crosspost, a
    # quoted reply, the same question asked on two forums). An export that
    # repeats them is worse than useless for reading or for training on, so
    # each content type is collapsed on its own text.
    raw_counts = {
        "comments": len(comments), "nuggets": len(nuggets),
        "ideas": len(ideas), "citations": len(citations),
        "listings": len(listings),
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

    # ONE ROW PER LISTING in the sheets people open, and the full series in
    # exactly one place.
    #
    # The archive appends an observation every time a listing is seen again,
    # unchanged or not, and it is right to: `RecordObservation` documents that
    # "we looked on the 3rd and it had not moved" is what makes days-on-market
    # a fact rather than an inference from gaps. But that is an archival
    # decision, and re-exporting it row for row made the spreadsheet unusable.
    # A real archive held 7979 observations of 3828 listings, of which 2876
    # rows - 36% - carried no change from an earlier one: the same listing at
    # the same price with a byte-identical content_hash, six times over. Only
    # 74 listings had ever changed price at all.
    #
    # So the split is by question. `current` answers "what is on the market",
    # which is what `all-001.csv` and the per-portal folders are opened for.
    # `history` answers "how did it get there" and keeps every observation, so
    # nothing the archive knows is lost on the way to CSV.
    current = sorted((r for r in listings if r.get("is_latest") == "true"),
                     key=lambda r: (r.get("portal", ""), r.get("listing_id", "")))

    # One folder per portal, which is the split that matters for listings:
    # nobody asks "show me every property", they ask what Property24 had.
    for portal, rows in sorted(_group(current, lambda r: _slug(r.get("portal"))).items()):
        record(_write_shards(out_dir / "listings" / portal, portal,
                             LISTING_COLUMNS, rows, rows_per_file))

    # ...and one sheet with all of them, sitting beside those folders. The
    # per-portal split answers "what did this portal have"; it cannot answer
    # "what is on the market", which needs every source in one place - sorting
    # by price across portals, or filtering to a market, means one file rather
    # than ten opened side by side. `portal` and `market` are the first two
    # columns, so the split is a filter away.
    record(_write_shards(out_dir / "listings", "all",
                         LISTING_COLUMNS, current, rows_per_file))

    # Every observation, including the unchanged ones. Kept separate rather
    # than dropped: the price cuts are in here, and so is the evidence for how
    # long something has been on the market.
    record(_write_shards(out_dir / "listings", "history",
                         LISTING_COLUMNS,
                         sorted(listings, key=lambda r: (r.get("portal", ""),
                                                         r.get("listing_id", ""),
                                                         r.get("observed_at", ""))),
                         rows_per_file))

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
        # Stated because it bites every naive reader exactly once. The BOM is
        # deliberate — Excel on Windows reads a plain UTF-8 CSV as cp1252 and
        # mangles every non-ASCII character in scraped text — but it means the
        # FIRST column header carries an invisible ﻿ prefix. A consumer
        # opening these with plain "utf-8" silently loses that column, which
        # is unique_key for nuggets and id for ideas. Read them with
        # encoding="utf-8-sig" (pandas: encoding="utf-8-sig").
        "encoding": "utf-8-sig",
        "reader_note": (
            "open with encoding='utf-8-sig'; plain utf-8 leaves a BOM on the "
            "first column name"
        ),
        "counts": {
            "comments": len(comments),
            "nuggets": len(nuggets),
            "ideas": len(ideas),
            "citations": len(citations),
            # "listings" counts LISTINGS, which is what all-NNN.csv and the
            # portal folders hold. The observation total is its own number
            # rather than folded in here, because a log line saying "exported
            # 7979 listing(s)" over an archive of 3828 properties is a count
            # nobody can act on.
            "listings": sum(1 for r in listings if r.get("is_latest") == "true"),
            "listing_observations": len(listings),
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
