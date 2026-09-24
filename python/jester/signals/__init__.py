"""Problem-signal records — the collector's storage, field map and export.

The collector asks for one thing the nugget pipeline does not have: a
**record per post**, not per comment. Jester's archive stores comments (`ingest_batch`) and
the insights extracted from them (`nuggets`); a Reddit post survives only as
`thread_meta` JSON hanging off a comment batch. A problem signal has to be a
row of its own, because every check is about that row: it must be
deduplicated by its source id, keep first/last seen, survive an edit, record a
deletion, carry the query that matched it and the reason it was kept, and
reconcile between the database and the CSV.

Three rules are structural here rather than left to discipline:

* **Mode is a column.** Every record says `live` or `fixture`. A fixture row
  can never be counted as a live one, because the count is grouped by the
  column (`counts()`), not asserted in a report.
* **Unknown stays unknown.** `company` and `buyer_intent` exist and are always
  `"unknown"` unless a permitted source states them. A post is a signal, never
  evidence of a budget or a buyer, and there is no code path that infers one.
* **One field map.** `FIELDS` below is the only definition of the schema; the
  SQLite DDL, the ClickHouse DDL and the CSV header are all generated from
  it, so the three cannot drift.

Live Reddit collection is NOT here. It needs approved commercial API access,
which is somebody else's decision and is unresolved. Everything in this module
runs offline against fixtures, so the pipeline is finished and tested before
access exists.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass, asdict, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

#: The field map. (name, sqlite type, clickhouse type, what it is).
#:
#: Ordering is the CSV column order and the reading order of the table: what
#: the record is, where it came from, when it happened, why it was kept, and
#: which run saw it.
FIELDS = [
    ("record_id", "TEXT", "String", "our key: <platform>:<source_id>"),
    ("mode", "TEXT", "LowCardinality(String)", "live | fixture — never inferred, always stored"),
    ("platform", "TEXT", "LowCardinality(String)", "hackernews — the source the collectors actually read"),
    ("source_id", "TEXT", "String", "the platform's own id (Hacker News item number)"),
    ("source_url", "TEXT", "String", "permalink; the evidence link that travels with the signal"),
    ("community", "TEXT", "LowCardinality(String)", "hn/comment | hn/ask | hn/show | hn/story | hn/hiring"),
    ("kind", "TEXT", "LowCardinality(String)", "post | comment"),
    ("author", "TEXT", "String", "handle as published; never enriched, never contacted"),
    ("title", "TEXT", "String", "post title, empty for a comment"),
    ("excerpt", "TEXT", "String", "permitted text, truncated to EXCERPT_CHARS"),
    ("text_hash", "TEXT", "String", "sha1 of the full permitted text — how an edit is detected"),
    ("query", "TEXT", "String", "the community/search term that matched this record"),
    ("match_reason", "TEXT", "String", "why it was kept, in words a human can check"),
    ("match_confidence", "REAL", "Float32", "0–1; the classifier's own confidence, not a score"),
    ("audience", "TEXT", "LowCardinality(String)",
     "buyer (outreach can act on it) | practitioner (content material) | none"),
    ("relevant", "INTEGER", "UInt8", "1 kept as a problem signal, 0 collected and rejected"),
    ("company", "TEXT", "LowCardinality(String)", "always 'unknown' unless the source states it"),
    ("buyer_intent", "TEXT", "LowCardinality(String)", "'unknown' for a post; a hiring advert's own engagement words"),
    # Nullable: a record can exist without one. A fetch that failed has no post
    # date, and that row still has to be stored and counted.
    ("created_utc", "TEXT", "Nullable(DateTime64(3))", "when the author posted it"),
    ("edited_utc", "TEXT", "Nullable(DateTime64(3))", "when we first saw the text change"),
    ("removed_utc", "TEXT", "Nullable(DateTime64(3))", "when the source stopped returning it"),
    ("first_seen_utc", "TEXT", "DateTime64(3)", "our first sighting — never overwritten"),
    ("last_seen_utc", "TEXT", "DateTime64(3)", "our latest sighting"),
    ("revisions", "INTEGER", "UInt16", "how many times the text changed under us"),
    ("run_id", "TEXT", "String", "the run that last touched this row"),
    ("run_status", "TEXT", "LowCardinality(String)", "ok | error"),
    ("error", "TEXT", "String", "what went wrong on this record, empty when ok"),
    # -- what happened NEXT ---------------------------------------------------
    #
    # The only fields here that do not come from the source or from the rules.
    # Everything above describes what was collected and how it was scored; a
    # collector can be perfectly right about all of it and still be useless,
    # because "matches our criteria" is not the same claim as "this company
    # replied". Validating the rules against the rules is circular, and until
    # these columns have values in them that is all anyone has been doing.
    #
    # Set BY HAND, never inferred. There is no code path that writes an
    # outcome from a heuristic, because a guessed outcome would poison the one
    # measurement in the archive that comes from reality.
    ("outcome", "TEXT", "LowCardinality(String)",
     "what came of it: '' untouched | contacted | replied | meeting | won | no | unfit"),
    ("outcome_at", "TEXT", "Nullable(DateTime64(3))", "when the outcome was recorded"),
    ("outcome_note", "TEXT", "String", "one line from whoever worked it; empty is fine"),
]

#: The outcomes a record may carry, in the order a lead moves through them.
#: `unfit` is deliberately separate from `no`: a company that never should
#: have reached outreach is a fault in the rules, and one that said no is not.
#: Collapsing them would hide the only signal that can improve the classifier.
OUTCOMES = ("", "contacted", "replied", "meeting", "won", "no", "unfit")
FIELD_NAMES = [f[0] for f in FIELDS]

#: How much permitted text a record carries. An excerpt, not a copy: retention
#: (§14) applies to the archive and to every CSV written from it.
EXCERPT_CHARS = 600

TABLE = "problem_signal"


def sqlite_ddl() -> str:
    cols = ",\n  ".join(f"{n} {t}" for n, t, _c, _d in FIELDS)
    return (
        f"CREATE TABLE IF NOT EXISTS {TABLE} (\n  {cols},\n"
        "  PRIMARY KEY (record_id)\n);\n"
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_seen ON {TABLE} (last_seen_utc);\n"
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_mode ON {TABLE} (mode, relevant);\n"
    )


def clickhouse_ddl() -> str:
    """The analytical table, generated from the same field map so the two
    schemas cannot drift. ReplacingMergeTree on last_seen_utc gives the
    "repeat import creates no duplicates" check the same shape it has in
    SQLite: one row per record_id, the newest sighting winning."""
    cols = ",\n  ".join(f"{n} {c}" for n, _t, c, _d in FIELDS)
    return (
        f"CREATE TABLE IF NOT EXISTS {TABLE} (\n  {cols}\n)\n"
        "ENGINE = ReplacingMergeTree(last_seen_utc)\n"
        # Partition on when WE saw it, not on when it was written. created_utc
        # is nullable — a fetch that failed has no post date — and a null
        # partition key rejects the row, which would silently drop exactly the
        # failure records the run is supposed to count. first_seen_utc is
        # always set, and "what did we collect that month" is the question the
        # archive gets asked anyway.
        "PARTITION BY toYYYYMM(first_seen_utc)\n"
        # Keyed on the record id alone. Anything else in the sort key and the
        # same record lands twice the day a post is crossposted or a community
        # is renamed, which is the duplicate the M2 check exists to forbid.
        "ORDER BY record_id;\n"
    )


def field_map_markdown() -> str:
    """The field map as a table, printed from the code rather than typed into
    a document that can go stale."""
    head = "| field | sqlite | clickhouse | meaning |\n|---|---|---|---|\n"
    # A meaning like "live | fixture" would break the table. The pipe is part
    # of what the field says, so escape it rather than reword the field map.
    cell = lambda s: s.replace("|", "\\|")  # noqa: E731
    return head + "".join(
        f"| `{n}` | {t} | {cell(c)} | {cell(d)} |\n" for n, t, c, d in FIELDS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class Signal:
    """One problem signal. Defaults encode the rules: fixture until a live
    collector says otherwise, unknown company and intent until a source states
    them, no error until one happens."""
    source_id: str
    source_url: str = ""
    community: str = ""
    platform: str = "reddit"
    kind: str = "post"
    author: str = ""
    title: str = ""
    excerpt: str = ""
    query: str = ""
    match_reason: str = ""
    match_confidence: float = 0.0
    relevant: int = 1
    #: Who the signal is for. The collector feeds two consumers — a buyer
    #: test and a content feed — and they are not the same posts.
    audience: str = "none"
    created_utc: str = ""
    mode: str = "fixture"
    run_id: str = ""
    run_status: str = "ok"
    error: str = ""
    #: Full permitted text. Hashed for edit detection, truncated for storage.
    text: str = ""
    company: str = "unknown"
    buyer_intent: str = "unknown"
    #: Filled in later by a person, never by the collector.
    outcome: str = ""
    outcome_at: str = ""
    outcome_note: str = ""

    def row(self, seen_at: Optional[str] = None) -> dict:
        seen = seen_at or _now()
        return {
            "record_id": f"{self.platform}:{self.source_id}",
            "mode": self.mode,
            "platform": self.platform,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "community": self.community,
            "kind": self.kind,
            "author": self.author,
            "title": self.title,
            "excerpt": (self.excerpt or self.text or "")[:EXCERPT_CHARS],
            "text_hash": text_hash(self.text or self.excerpt),
            "query": self.query,
            "match_reason": self.match_reason,
            "match_confidence": float(self.match_confidence or 0.0),
            "relevant": int(self.relevant),
            "audience": self.audience or "none",
            "company": self.company or "unknown",
            "buyer_intent": self.buyer_intent or "unknown",
            "created_utc": self.created_utc,
            "edited_utc": "",
            "removed_utc": "",
            "first_seen_utc": seen,
            "last_seen_utc": seen,
            "revisions": 0,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "error": self.error,
            # A newly collected record has no outcome. Nothing infers one.
            "outcome": self.outcome,
            "outcome_at": self.outcome_at,
            "outcome_note": self.outcome_note,
        }


def open_signals(db_path: str) -> sqlite3.Connection:
    """Open (and create) the signal database, making its directory if needed.

    The directory matters more than it looks. `data/` is gitignored, so it
    does not exist in a fresh clone, and sqlite does not create a missing
    parent — it raises `unable to open database file`, which reads like a
    permissions problem and sends the reader looking in the wrong place. The
    first person to run the documented command on a clean checkout hit exactly
    that. The export already creates its own output directory; this is the
    same courtesy for the database beside it.
    """
    parent = Path(db_path).parent
    # `:memory:` has no parent worth creating, and a bare filename resolves to
    # `.`, which always exists — so only a real directory path gets made.
    if db_path != ":memory:" and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.executescript(sqlite_ddl())
    _catch_up_columns(db)
    db.commit()
    return db


def _catch_up_columns(db: sqlite3.Connection) -> List[str]:
    """Add any field the table is missing.

    `CREATE TABLE IF NOT EXISTS` never widens a table that already exists, so a
    database created before a field was added keeps the old shape and every
    insert fails with "no such column" — which is how the `audience` column
    broke the first run against the real archive. The field map is the single
    source of the schema, so it is also the thing that repairs it.

    Only additive: a column is never dropped or retyped here. A destructive
    change would need a migration someone has read.
    """
    have = {r[1] for r in db.execute(f"PRAGMA table_info({TABLE})")}
    added = []
    for name, sqltype, _ch, _doc in FIELDS:
        if name not in have:
            # WITH A DEFAULT, which sqlite backfills into every existing row.
            # Without one the new column is NULL on old records and '' on new
            # ones, so `WHERE outcome = ''` silently skipped the entire
            # existing archive - 34 of 35 leads - while looking like a
            # perfectly ordinary query. The promise of this function is that a
            # caught-up table is indistinguishable from a fresh one, and a
            # column full of NULLs where a fresh table has '' breaks it.
            default = "0" if sqltype in ("INTEGER", "REAL") else "''"
            db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {sqltype} "
                       f"NOT NULL DEFAULT {default}")
            added.append(name)
    if added:
        db.commit()
    return added


def upsert(db: sqlite3.Connection, signals: Iterable[Signal], *, seen_at: Optional[str] = None) -> dict:
    """Insert or update, keyed on record_id. Returns the counts a run report
    needs: how many were new, how many were seen again, how many had changed.

    This is where "repeat import creates no duplicates" actually lives. An
    unchanged record updates `last_seen_utc` only; a changed one also stamps
    `edited_utc` (the first time we notice) and counts a revision. Nothing is
    ever inserted twice, and `first_seen_utc` is never overwritten — it is the
    one date that says how long the archive has known about a problem.
    """
    new = seen = edited = 0
    for s in signals:
        row = s.row(seen_at)
        old = db.execute(
            f"SELECT text_hash, revisions, edited_utc FROM {TABLE} WHERE record_id=?",
            (row["record_id"],),
        ).fetchone()
        if old is None:
            db.execute(
                f"INSERT INTO {TABLE} ({','.join(FIELD_NAMES)}) "
                f"VALUES ({','.join('?' * len(FIELD_NAMES))})",
                [row[n] for n in FIELD_NAMES],
            )
            new += 1
            continue
        changed = old["text_hash"] != row["text_hash"]
        db.execute(
            f"UPDATE {TABLE} SET last_seen_utc=?, excerpt=?, text_hash=?, "
            "match_reason=?, match_confidence=?, relevant=?, audience=?, run_id=?, run_status=?, error=?, "
            "revisions=?, edited_utc=?, removed_utc='' WHERE record_id=?",
            (row["last_seen_utc"], row["excerpt"], row["text_hash"],
             row["match_reason"], row["match_confidence"], row["relevant"], row["audience"],
             row["run_id"], row["run_status"], row["error"],
             (old["revisions"] or 0) + (1 if changed else 0),
             (old["edited_utc"] or (row["last_seen_utc"] if changed else "")),
             row["record_id"]),
        )
        seen += 1
        edited += 1 if changed else 0
    db.commit()
    return {"new": new, "seen_again": seen, "edited": edited}


def mark_removed(db: sqlite3.Connection, record_ids: Iterable[str], *, at: Optional[str] = None) -> int:
    """A record the source stopped returning. Removal is recorded, never
    deleted: the signal was real when it was collected, and an archive that
    silently loses rows cannot reconcile its own counts. Retention (§14) is
    what eventually removes the text."""
    stamp = at or _now()
    ids = list(record_ids)
    if not ids:
        return 0
    db.executemany(
        f"UPDATE {TABLE} SET removed_utc=? WHERE record_id=? AND removed_utc=''",
        [(stamp, rid) for rid in ids],
    )
    db.commit()
    return db.total_changes and len(ids)


class OutcomeError(ValueError):
    """An outcome that is not one of the seven, or a record that is not there.

    Loud, because the alternative is a silent no-op: someone records the one
    meeting this archive has ever produced, sees no error, and the row stays
    empty.
    """


def set_outcome(db: sqlite3.Connection, record_id: str, outcome: str,
                *, note: str = "", at: Optional[str] = None) -> dict:
    """Record what came of a signal. The only field a person writes.

    Deliberately one record at a time and deliberately by hand. Everything
    else in this archive is derived from a source or from the rules; this is
    the one column that comes from reality, and the moment anything infers it
    the archive stops being able to answer whether the rules are any good.

    Re-recording is allowed and overwrites, because a lead moves: contacted
    today, replied on Friday, meeting next week. The note is replaced with it
    rather than appended - a field that grows without bound is a field nobody
    reads.
    """
    if outcome not in OUTCOMES:
        raise OutcomeError(
            f"{outcome!r} is not an outcome; use one of: "
            + ", ".join(repr(o) for o in OUTCOMES if o))
    row = db.execute(
        f"SELECT outcome FROM {TABLE} WHERE record_id = ?", (record_id,)).fetchone()
    if row is None:
        raise OutcomeError(f"no record {record_id!r} in this archive")
    stamp = "" if not outcome else (at or _now())
    db.execute(
        f"UPDATE {TABLE} SET outcome = ?, outcome_at = ?, outcome_note = ? "
        "WHERE record_id = ?", (outcome, stamp, note[:400], record_id))
    db.commit()
    return {"record_id": record_id, "was": row["outcome"] or "",
            "now": outcome, "at": stamp}


def outcomes(db: sqlite3.Connection, *, mode: str = "") -> dict:
    """What came of the buyer signals, and how much of the archive was worked.

    `untouched` is the number that matters most early on: a high buyer count
    with nothing acted on means the collector is producing and nobody is
    consuming, which is a different problem from producing badly.
    """
    where = ["audience = 'buyer'"]
    args = []
    if mode:
        where.append("mode = ?")
        args.append(mode)
    rows = db.execute(
        "SELECT COALESCE(NULLIF(outcome, ''), 'untouched') AS o, COUNT(*) AS n "
        f"FROM {TABLE} WHERE {' AND '.join(where)} GROUP BY o", args).fetchall()
    by_outcome = {r["o"]: r["n"] for r in rows}
    total = sum(by_outcome.values())
    untouched = by_outcome.get("untouched", 0)
    return {
        "by_outcome": by_outcome,
        "buyers": total,
        "worked": total - untouched,
        "untouched": untouched,
        # The only conversion figure in the project that is not circular:
        # every other number here is the rules agreeing with themselves.
        "replied_or_better": sum(by_outcome.get(o, 0)
                                 for o in ("replied", "meeting", "won")),
        # A lead that should never have reached outreach is a fault in the
        # rules; one that said no is not. Kept apart for that reason.
        "unfit": by_outcome.get("unfit", 0),
    }


def counts(db: sqlite3.Connection) -> dict:
    """The numbers a run has to report, grouped by mode so a fixture count can
    never be read as a live one."""
    out = {}
    for mode, total, relevant, buyer, practitioner, removed, errors in db.execute(
        f"SELECT mode, COUNT(*), SUM(relevant), SUM(audience='buyer'), "
        f"SUM(audience='practitioner'), SUM(removed_utc != ''), "
        f"SUM(run_status != 'ok') FROM {TABLE} GROUP BY mode"
    ).fetchall():
        out[mode] = {
            "unique_collected": total,
            "relevant": int(relevant or 0),
            # Reported apart because they are different products: `buyer` is
            # what outreach can act on, `practitioner` is content
            # material. Adding them together would inflate the only number
            # anyone will read.
            "buyer": int(buyer or 0),
            "practitioner": int(practitioner or 0),
            "removed": int(removed or 0),
            "errors": int(errors or 0),
        }
    return out


def export_csv(db: sqlite3.Connection, out_dir: str | Path, *, mode: str = "") -> dict:
    """Write the signals as one CSV plus a manifest.

    The manifest is the reconciliation the M2 check asks for: it states how
    many rows the database holds and how many the file carries, so "the export
    matches the table" is a fact on disk rather than a claim in a report.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    where, args = ("WHERE mode = ?", (mode,)) if mode else ("", ())
    rows = db.execute(
        f"SELECT {','.join(FIELD_NAMES)} FROM {TABLE} {where} ORDER BY first_seen_utc, record_id", args
    ).fetchall()
    csv_path = out / ("signals.csv" if not mode else f"signals-{mode}.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELD_NAMES)
        for r in rows:
            w.writerow([r[n] for n in FIELD_NAMES])
    in_db = db.execute(
        f"SELECT COUNT(*) FROM {TABLE} {where}", args
    ).fetchone()[0]
    manifest = {
        "written_at": _now(),
        "mode_filter": mode or "all",
        "rows_in_db": in_db,
        "rows_in_csv": len(rows),
        "reconciles": in_db == len(rows),
        "counts_by_mode": counts(db),
        "fields": FIELD_NAMES,
        "excerpt_chars": EXCERPT_CHARS,
        "csv": csv_path.name,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "field-map.md").write_text(field_map_markdown(), encoding="utf-8")
    (out / "schema.clickhouse.sql").write_text(clickhouse_ddl(), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
# Five records, every one labeled `fixture`, covering the case types the
# pipeline has to handle: a clear signal, a near-miss that is kept with low
# confidence, an irrelevant post that is collected and rejected, a post that
# gets edited between runs, and one whose fetch failed. They are written by
# hand rather than sampled from the live site: nothing here has been collected
# from Reddit, and the mode column says so on every row.

FIXTURE_QUERY = "software delivery pain"

#: The five cases, each declaring the category it exists to exercise. The
#: category is the fixture's claim; the verdict is the classifier's. Keeping
#: them apart is the whole point — a case passes when the
#: classifier independently lands where the label says it should, and the 20-case
#: set extends this list rather than replacing it.
#:
#: `expected_relevant` is what `relevant` must come out as. `expected_band` is
#: where the confidence must land: "signal" at or above relevant_at,
#: "ambiguous" between the thresholds, "rejected" below ambiguous_at.
FIXTURE_CASES = [
    {
        # A buyer: speaks as the business, names the work, names what it costs.
        "category": "relevant",
        "expected_audience": "buyer",
        "expected_ambiguous": False,
        "signal": Signal(
            source_id="t3_syn0001", kind="post", community="r/synthetic-smallbiz",
            source_url="https://example.invalid/synthetic/t3_syn0001",
            author="synthetic_user_1", created_utc="2026-09-15T09:12:00+00:00",
            title="We run a small parts distributor and re-key every order by hand",
            text=("Two of us spend about six hours a week copying order details out of "
                  "emails into a spreadsheet and then into the invoicing tool. We tried "
                  "to automate it and it breaks whenever a supplier changes their format. "
                  "Is it worth paying someone to build this properly?"),
            query=FIXTURE_QUERY),
    },
    {
        # A buyer voice with a real problem but nothing about its size: kept,
        # flagged, and never handed over as if it were qualified.
        "category": "ambiguous",
        "expected_audience": "buyer",
        "expected_ambiguous": True,
        "signal": Signal(
            source_id="t3_syn0002", kind="post", community="r/synthetic-smallbiz",
            source_url="https://example.invalid/synthetic/t3_syn0002",
            author="synthetic_user_2", created_utc="2026-09-16T14:40:00+00:00",
            title="How do you handle the admin side as you grow?",
            text=("I run a small studio and things are getting busier. Not sure what to "
                  "change first. Curious what worked for other people."),
            query=FIXTURE_QUERY),
    },
    {
        "category": "irrelevant",
        "expected_audience": "none",
        "expected_ambiguous": False,
        "signal": Signal(
            source_id="t3_syn0003", kind="post", community="r/synthetic-devops",
            source_url="https://example.invalid/synthetic/t3_syn0003",
            author="synthetic_user_3", created_utc="2026-09-17T08:05:00+00:00",
            title="Photos of my new rack",
            text="Finally finished cable management on my homelab. No question, just showing off.",
            query=FIXTURE_QUERY),
    },
    {
        # An engineer describing why the DIY fix failed: content, and
        # explicitly NOT a lead. This case also carries the edit replay.
        "category": "edited",
        "expected_audience": "practitioner",
        "expected_ambiguous": False,
        "signal": Signal(
            source_id="t1_syn0004", kind="comment", community="r/synthetic-devops",
            source_url="https://example.invalid/synthetic/t3_syn0001/t1_syn0004",
            author="synthetic_user_4", created_utc="2026-09-15T10:02:00+00:00",
            text=("Same here. EDIT: we ended up writing a script to automate the manual "
                  "steps but the integration breaks every time the provider changes "
                  "their API. It costs us most of a day a month just keeping it alive."),
            query=FIXTURE_QUERY),
    },
    {
        "category": "failure",
        "expected_audience": "none",
        "expected_ambiguous": False,
        "signal": Signal(
            source_id="t3_syn0005", kind="post", community="r/synthetic-dataeng",
            source_url="https://example.invalid/synthetic/t3_syn0005",
            author="synthetic_user_5", created_utc="2026-09-18T19:30:00+00:00",
            title="(fetch failed)", text="",
            query=FIXTURE_QUERY,
            run_status="error", error="synthetic: source returned 503 on two attempts"),
    },
]


def synthetic_signals(run_id: str = "signals-fixture", rules=None) -> List[Signal]:
    """The five fixture records, classified by the real classifier.

    The audience, reason and confidence on an exported record are the
    classifier's own output, not values typed into the fixture — otherwise the
    CSV would show numbers no code produced, and the gate would be checking a
    label against itself.
    """
    from .filters import apply_to, load_rules
    rules = rules or load_rules()
    out = []
    for case in FIXTURE_CASES:
        c = Signal(**asdict(case["signal"]))
        c.run_id = run_id
        c.mode = "fixture"
        apply_to(c, rules)
        out.append(c)
    return out


def check_fixtures(rules=None) -> dict:
    """Run every fixture case through the classifier and report pass/fail.

    A case passes when the classifier independently lands on the audience the
    label claims, with the ambiguity flag the label claims. This is the shape
    the gate is graded: one command, a table, and a non-zero exit when any
    case misses.
    """
    from .filters import classify, load_rules
    rules = rules or load_rules()
    rows = []
    for case in FIXTURE_CASES:
        s = case["signal"]
        v = classify(s.text, rules, title=s.title)
        ok = (v.audience == case["expected_audience"]
              and v.ambiguous == case["expected_ambiguous"])
        rows.append({
            "source_id": s.source_id,
            "category": case["category"],
            "expected_audience": case["expected_audience"],
            "expected_ambiguous": case["expected_ambiguous"],
            "audience": v.audience,
            "ambiguous": v.ambiguous,
            "confidence": v.confidence,
            "reason": v.reason,
            "pass": ok,
        })
    return {
        "cases": rows,
        "total": len(rows),
        "passed": sum(1 for r in rows if r["pass"]),
        "failed": [r["source_id"] for r in rows if not r["pass"]],
        "categories": sorted({r["category"] for r in rows}),
        "audiences": sorted({r["audience"] for r in rows}),
    }


def run_fixture_export(db_path: str, out_dir: str | Path, *, run_id: str = "signals-fixture",
                       seen_at: Optional[str] = None, rules=None) -> dict:
    """The one repeatable command's body: seed the five synthetic records,
    upsert them (so a second run proves the dedup rather than doubling the
    file), and export.

    Idempotent on purpose. `jester signals --fixture` twice must leave five
    rows and one CSV with five rows, and the manifest must say so.
    """
    from .filters import crossposts

    db = open_signals(db_path)
    stats = upsert(db, synthetic_signals(run_id, rules), seen_at=seen_at)
    manifest = export_csv(db, out_dir, mode="fixture")
    manifest["upsert"] = stats
    manifest["crossposts"] = crossposts(db)
    return manifest
