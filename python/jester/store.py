"""SQLite storage layer. The nuggets/meta DDL matches go/internal/store/store.go.

Fields from jester-setup.md 3.2 that Go's table lacks (needs_reembed, embedding_id,
thread_id, engagement_score, timestamp) are added via idempotent ALTERs so the shared
schema_version=1 guard still holds across both languages.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from jester.models import Idea, Nugget

SCHEMA_VERSION = 1


class SchemaVersionError(Exception):
    pass


class ConcurrentRunError(Exception):
    """Raised by start_run when another run row is still 'running' (R25 guard)."""

    pass


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS nuggets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_key        TEXT UNIQUE NOT NULL,
    category          TEXT,
    raw_text           TEXT,
    extracted_insight TEXT,
    source_url         TEXT,
    platform           TEXT,
    run_id             TEXT,
    embedding_model    TEXT,
    synthesized_at     TEXT,
    trivial            INTEGER DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ingest_batch (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    platform  TEXT NOT NULL,
    source    TEXT,
    thread_id TEXT,
    comments  TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ideas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT NOT NULL,
    problem_statement  TEXT,
    proposed_solution   TEXT,
    supporting_nuggets  TEXT,              -- JSON list of source nugget unique_keys
    source_threads      INTEGER,
    source_platforms    TEXT,              -- JSON list
    demand_signal        REAL,
    feasibility           REAL,
    competition           REAL,
    overall               REAL,
    competitor_notes      TEXT,
    competition_checked   INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'new',
    last_scored_at       TEXT,
    critic_model         TEXT,
    synthesis_model       TEXT,
    run_id               TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS quota (
    platform TEXT NOT NULL,
    day_key  TEXT NOT NULL,
    used     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(platform, day_key)
);
CREATE TABLE IF NOT EXISTS ingested_comments (
    fingerprint TEXT PRIMARY KEY,
    thread_id   TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'running',
    started_at          TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at         TEXT,
    models_used         TEXT,
    n_comments          INTEGER,
    n_batches           INTEGER,
    n_nuggets_kept      INTEGER,
    n_discarded_trivial INTEGER,
    trivial_share       REAL,
    n_ideas             INTEGER,
    floor_flags         TEXT,              -- JSON list of §13.1 flags
    phase_checkpoints   TEXT,              -- JSON list of {phase, at}
    error               TEXT,
    prefilter_funnel    TEXT,              -- JSON: kept + per-heuristic drops (R35)
    top_near_misses     TEXT,              -- JSON list of dedup near-miss scores (§13.1)
    platform_counts     TEXT,              -- JSON {platform: kept} for the run (M3.2)
    origin              TEXT NOT NULL DEFAULT 'manual',
    n_posts             INTEGER,
    duration_expected_s REAL,
    duration_actual_s   REAL
);
"""

_ADD_COLUMNS = [
    "ALTER TABLE nuggets ADD COLUMN needs_reembed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE nuggets ADD COLUMN embedding_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN thread_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN engagement_score REAL",
    "ALTER TABLE nuggets ADD COLUMN timestamp TEXT",
    "ALTER TABLE runs ADD COLUMN trivial_share REAL",
    "ALTER TABLE runs ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE runs ADD COLUMN n_posts INTEGER",
    "ALTER TABLE runs ADD COLUMN duration_expected_s REAL",
    "ALTER TABLE runs ADD COLUMN duration_actual_s REAL",
    "ALTER TABLE runs ADD COLUMN prefilter_funnel TEXT",
    "ALTER TABLE runs ADD COLUMN top_near_misses TEXT",
    # M3.2: per-platform ingest counts for a multi-platform run.
    "ALTER TABLE runs ADD COLUMN platform_counts TEXT",
    # §37.20 Phase A: ingest_batch parity with the Go owner (D-17) so Go's
    # EnqueueBatch works against Python-created databases.
    "ALTER TABLE ingest_batch ADD COLUMN run_id TEXT",
    "ALTER TABLE ingest_batch ADD COLUMN batch_index INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE ingest_batch ADD COLUMN claimed_at TEXT",
    "ALTER TABLE ingest_batch ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE ingest_batch ADD COLUMN error TEXT",
    # §37.22: ingested_comments is SHARED (Go worker + Python both mark it);
    # legacy Go-created tables lack thread_id — add it idempotently.
    "ALTER TABLE ingested_comments ADD COLUMN thread_id TEXT",
    # The thread/video/topic a batch came from, as JSON. Go's store writes it;
    # this keeps a Python-created database wide enough to receive it (D-17).
    "ALTER TABLE ingest_batch ADD COLUMN thread_meta TEXT",
    # ---- what the platform actually published about a comment -------------
    # The adapters have always had all of this and dropped it at the point of
    # capture; a nugget could name its platform and its score and nothing else
    # — not who said it, not when, not where to read it.
    #
    # The vote columns are NULLable on purpose. NULL means "this platform does
    # not publish that figure", which is the true state for far more of them
    # than one would guess: Reddit has not exposed per-comment downvotes since
    # 2014, YouTube withdrew dislike counts in 2021, Hacker News never
    # published per-comment scores, and Discourse has no downvote at all. A 0
    # would assert that nobody voted, which is a different claim entirely.
    "ALTER TABLE nuggets ADD COLUMN author TEXT",
    "ALTER TABLE nuggets ADD COLUMN author_url TEXT",
    "ALTER TABLE nuggets ADD COLUMN comment_url TEXT",
    "ALTER TABLE nuggets ADD COLUMN comment_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN parent_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN depth INTEGER",
    # created_utc is when the comment was WRITTEN. `timestamp` is when jester
    # ingested it — two different facts that the schema could not tell apart.
    "ALTER TABLE nuggets ADD COLUMN created_utc TEXT",
    "ALTER TABLE nuggets ADD COLUMN created_raw TEXT",
    "ALTER TABLE nuggets ADD COLUMN upvotes INTEGER",
    "ALTER TABLE nuggets ADD COLUMN downvotes INTEGER",
    "ALTER TABLE nuggets ADD COLUMN likes INTEGER",
    "ALTER TABLE nuggets ADD COLUMN dislikes INTEGER",
    "ALTER TABLE nuggets ADD COLUMN replies INTEGER",
    "ALTER TABLE nuggets ADD COLUMN awards INTEGER",
    "ALTER TABLE nuggets ADD COLUMN reads INTEGER",
    "ALTER TABLE nuggets ADD COLUMN edited INTEGER",
    "ALTER TABLE nuggets ADD COLUMN pinned INTEGER",
    "ALTER TABLE nuggets ADD COLUMN author_is_op INTEGER",
    "ALTER TABLE nuggets ADD COLUMN distinguished INTEGER",
    "ALTER TABLE nuggets ADD COLUMN accepted_answer INTEGER",
    # ---- the thread it came from ------------------------------------------
    "ALTER TABLE nuggets ADD COLUMN post_title TEXT",
    "ALTER TABLE nuggets ADD COLUMN post_url TEXT",
    "ALTER TABLE nuggets ADD COLUMN post_author TEXT",
    "ALTER TABLE nuggets ADD COLUMN post_created_utc TEXT",
    "ALTER TABLE nuggets ADD COLUMN post_score INTEGER",
    # Reddit's published fraction of votes that were upvotes — the ONLY
    # downvote signal any of the four platforms exposes.
    "ALTER TABLE nuggets ADD COLUMN post_upvote_ratio REAL",
    "ALTER TABLE nuggets ADD COLUMN post_comment_count INTEGER",
    "ALTER TABLE nuggets ADD COLUMN post_views INTEGER",
    "ALTER TABLE nuggets ADD COLUMN community TEXT",
    # The long tail one platform publishes and the others do not, as JSON, so
    # a Discourse trust level or a YouTube creator heart is kept rather than
    # discarded for not generalising.
    "ALTER TABLE nuggets ADD COLUMN extra TEXT",
]


_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.S)
_ALTER_RE = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.I)


def _alter_supplied():
    """{table: {column, ...}} that ``_ADD_COLUMNS`` can add in place.

    reconcile_schema must not treat these as an incompatible shape: several
    live in BASE_SCHEMA *and* in the ALTER list, so an older database missing
    one would otherwise be renamed aside — losing run history from the active
    table — when a plain ALTER two lines later would have fixed it.
    """
    out = {}
    for stmt in _ADD_COLUMNS:
        m = _ALTER_RE.search(stmt)
        if m:
            out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def _schema_columns():
    """{table: [column, ...]} as declared in BASE_SCHEMA."""
    out = {}
    for match in _TABLE_RE.finditer(BASE_SCHEMA):
        table, body = match.group(1), match.group(2)
        cols = []
        for line in body.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if not line or line.upper().startswith(
                ("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK")
            ):
                continue
            cols.append(line.split()[0])
        out[table] = cols
    return out


def reconcile_schema(db: sqlite3.Connection):
    """Bring tables written by an older build up to BASE_SCHEMA.

    ``CREATE TABLE IF NOT EXISTS`` never upgrades a table that already exists,
    so a database from an earlier schema kept its old column names and every
    read died on `no such column` — that is what took the console overview and
    the ideas list down against a pre-existing data/jester.db.

    Incremental columns are still ``_ADD_COLUMNS``' job. This only handles a
    table whose shape is *incompatible*: it is recreated when empty, and
    renamed to ``<table>_legacy`` when it holds rows. Nothing is ever dropped
    with data in it, and old rows are never re-attributed to the new columns
    (R29: never fabricate).
    """
    actions = []
    addable = _alter_supplied()
    for table, want in _schema_columns().items():
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            continue
        have = {c[1] for c in db.execute(f"PRAGMA table_info({table})")}
        # A column the ALTER pass can add is not an incompatible shape.
        skip = addable.get(table, set())
        missing = [c for c in want if c not in have and c not in skip]
        if not missing:
            continue
        rows = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if rows == 0:
            db.execute(f"DROP TABLE {table}")
            actions.append(
                f"{table}: recreated (was empty, missing {', '.join(missing)})"
            )
        else:
            db.execute(f"DROP TABLE IF EXISTS {table}_legacy")
            db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
            actions.append(
                f"{table}: {rows} row(s) kept as {table}_legacy "
                f"(missing {', '.join(missing)})"
            )
    if actions:
        db.commit()
    return actions


def open_db(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    db = sqlite3.connect(path, timeout=5.0)
    # Rollback journal (DELETE), NOT WAL. The database is shared between the
    # console server and the ingestion worker — separate processes, sometimes
    # separate containers — over a Windows bind-mounted volume. WAL requires
    # the -wal/-shm sidecars, which that filesystem cannot create/arbitrate
    # reliably under concurrent access, surfacing as
    # "OperationalError: unable to open database file". The default rollback
    # journal uses a single -journal file with ordinary locking and avoids the
    # sidecars entirely; busy_timeout absorbs brief writer locks.
    try:
        db.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.OperationalError:
        pass
    db.execute("PRAGMA busy_timeout=5000")
    for note in reconcile_schema(db):
        # Never silently: an operator must know a legacy table was set aside.
        print(f"schema: {note}", file=sys.stderr)
    db.executescript(BASE_SCHEMA)
    for stmt in _ADD_COLUMNS:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # already present
    existing = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if existing is None:
        db.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
    else:
        if int(existing[0]) != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"schema_version db={existing[0]} != code={SCHEMA_VERSION}"
            )
    db.commit()
    return db


def meta_get(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def exists_unique_key(db: sqlite3.Connection, unique_key: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM nuggets WHERE unique_key=?", (unique_key,)
    ).fetchone()
    return row is not None


def enqueue_batch(
    db: sqlite3.Connection,
    platform: str,
    source: str,
    thread_id: str,
    comments: list,
    run_id: str = "",
    batch_index: int = 0,
    thread_meta: Optional[dict] = None,
) -> int:
    """Insert one raw ingestion job (comments is a JSON-serializable list).

    §37.20: ingest_batch is owned by the Go worker, whose DDL declares run_id
    and batch_index NOT NULL. Omitting them here worked only against a
    Python-created table and failed with `NOT NULL constraint failed` on any
    database the worker had touched first — so both are always written.

    ``thread_meta`` is the thread/video/topic the comments came from. None
    stays SQL NULL rather than becoming ``{}``: "the fetcher never captured
    this" and "it captured one and it was empty" are different states, and
    only the first is true of a batch queued before the widened capture.
    """
    cur = db.execute(
        "INSERT INTO ingest_batch(run_id, platform, source, thread_id, "
        "batch_index, comments, thread_meta, status) VALUES(?,?,?,?,?,?,?,'pending')",
        (
            run_id,
            platform,
            source,
            thread_id,
            batch_index,
            json.dumps(comments),
            json.dumps(thread_meta, ensure_ascii=False) if thread_meta else None,
        ),
    )
    db.commit()
    return cur.lastrowid


def pending_batches(db: sqlite3.Connection) -> List[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute(
        # thread_meta carries the post the comments came from; open_db's ALTER
        # list guarantees the column exists, and the reader tolerates its
        # absence anyway for a database opened by an older build.
        "SELECT id, platform, source, thread_id, comments, thread_meta "
        "FROM ingest_batch WHERE status='pending' ORDER BY id"
    ).fetchall()


# --- M1.2 fingerprint skip-list (§37.14) --------------------------------------


def seen_fingerprints(db: sqlite3.Connection, prints: list) -> set:
    prints = [p for p in prints if p]
    if not prints:
        return set()
    q = (
        "SELECT fingerprint FROM ingested_comments WHERE fingerprint IN ("
        + ",".join("?" * len(prints))
        + ")"
    )
    return {fp for (fp,) in db.execute(q, prints)}


def mark_comments_ingested(
    db: sqlite3.Connection, thread_id: str, prints: list
) -> None:
    prints = [p for p in prints if p]
    if not prints:
        return
    db.executemany(
        "INSERT OR IGNORE INTO ingested_comments(fingerprint, thread_id) VALUES(?,?)",
        [(fp, thread_id) for fp in prints],
    )
    db.commit()


def enqueue_new_only(
    db: sqlite3.Connection,
    platform: str,
    source: str,
    thread_id: str,
    comments: list,
    run_id: str = "",
    thread_meta: Optional[dict] = None,
) -> int:
    """M1.2 batcher: drop already-ingested fingerprints before queueing.
    Returns the number of NEW comments queued (0 ⇒ zero new batches)."""
    seen = seen_fingerprints(db, [c.get("fingerprint") for c in comments])
    fresh = [
        c for c in comments if not c.get("fingerprint") or c["fingerprint"] not in seen
    ]
    if not fresh:
        return 0
    enqueue_batch(
        db, platform, source, thread_id, fresh, run_id=run_id, thread_meta=thread_meta
    )
    return len(fresh)


def mark_batch_processed(db: sqlite3.Connection, batch_id: int) -> None:
    db.execute("UPDATE ingest_batch SET status='done' WHERE id=?", (batch_id,))
    db.commit()


def _nugget_trivial(n: Nugget) -> int:
    """R37 judge verdict when set; legacy heuristic fallback otherwise."""
    if n.trivial is not None:
        return 1 if n.trivial else 0
    return 1 if (n.category == "pain_point" and not n.extracted_insight) else 0


#: Columns written straight off the Nugget dataclass, in one list so adding a
#: captured field means naming it once instead of editing three parallel
#: tuples that drift apart.
_NUGGET_DETAIL_COLUMNS = (
    "author",
    "author_url",
    "comment_url",
    "comment_id",
    "parent_id",
    "depth",
    "created_utc",
    "created_raw",
    "upvotes",
    "downvotes",
    "likes",
    "dislikes",
    "replies",
    "awards",
    "reads",
    "edited",
    "pinned",
    "author_is_op",
    "distinguished",
    "accepted_answer",
    "post_title",
    "post_url",
    "post_author",
    "post_created_utc",
    "post_score",
    "post_upvote_ratio",
    "post_comment_count",
    "post_views",
    "community",
)

#: Flags stored as 0/1. None stays None — "not published" is not "false".
_NUGGET_FLAG_COLUMNS = frozenset(
    {"edited", "pinned", "author_is_op", "distinguished", "accepted_answer"}
)


def _detail_value(n: Nugget, col: str):
    v = getattr(n, col, None)
    if v is None:
        return None
    if col in _NUGGET_FLAG_COLUMNS:
        return 1 if v else 0
    return v


def insert_nugget(db: sqlite3.Connection, n: Nugget) -> None:
    base_cols = [
        "unique_key", "category", "raw_text", "extracted_insight", "source_url",
        "platform", "run_id", "embedding_model", "synthesized_at", "trivial",
        "needs_reembed", "embedding_id", "thread_id", "engagement_score",
        "timestamp", "extra",
    ]
    base_vals = [
        n.unique_key,
        n.category,
        n.raw_text,
        n.extracted_insight,
        n.source_url,
        n.platform,
        n.run_id,
        "nomic-embed-text",
        n.synthesized_at if n.synthesized_at else None,
        _nugget_trivial(n),
        1 if n.needs_reembed else 0,
        n.embedding_id,
        n.thread_id,
        n.engagement_score,
        n.timestamp or _now(),
        json.dumps(n.extra, ensure_ascii=False) if n.extra else None,
    ]
    cols = base_cols + list(_NUGGET_DETAIL_COLUMNS)
    vals = base_vals + [_detail_value(n, c) for c in _NUGGET_DETAIL_COLUMNS]
    db.execute(
        "INSERT OR IGNORE INTO nuggets (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join("?" * len(cols))),
        tuple(vals),
    )
    db.commit()


def list_nuggets(
    db: sqlite3.Connection, limit: Optional[int] = None
) -> List[sqlite3.Row]:
    """Archived nuggets, newest first. ``limit=None`` (the default) means ALL
    of them — a cap is something a caller asks for, never something the
    archive quietly applies to itself."""
    db.row_factory = sqlite3.Row
    # trivial/needs_reembed/synthesized_at are part of the reviewer's read of a
    # nugget (R37 floor + R40 reembed backlog) — selecting them here is what
    # lets the console show the flags instead of silently rendering nothing.
    sql = (
        "SELECT unique_key, category, raw_text, extracted_insight, platform, "
        "source_url, thread_id, run_id, trivial, needs_reembed, "
        "engagement_score, synthesized_at, created_at, "
        # Everything the scrapers now capture. Selecting it here is what lets
        # the console and the CSV export show it instead of holding it in a
        # column nothing reads.
        + ", ".join(_NUGGET_DETAIL_COLUMNS)
        + ", extra "
        "FROM nuggets ORDER BY id DESC"
    )
    # The limit belongs in SQL, not in a slice of the result. The console used
    # to load every row, build a dict for every one, and then throw away
    # everything past 500 — paying the full cost of the query it was trying to
    # avoid, and getting no protection for it.
    if limit is not None and int(limit) > 0:
        return db.execute(sql + " LIMIT ?", (int(limit),)).fetchall()
    return db.execute(sql).fetchall()


def count_nuggets(db: sqlite3.Connection) -> int:
    """How many nuggets the archive actually holds.

    Separate from ``list_nuggets`` so a caller that limits its rows can still
    report the true total. Without this the console had no way to tell a full
    page of 500 from a truncated view of 1,761, and neither did anyone
    reading it."""
    return int(db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0])


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def insert_idea(db: sqlite3.Connection, idea: Idea) -> int:
    """Persist a synthesized + scored idea. ``competition`` None is stored as
    NULL (R29/R41: never fabricated). Returns the new row id."""
    cur = db.execute(
        """
        INSERT INTO ideas
            (title, problem_statement, proposed_solution, supporting_nuggets,
             source_threads, source_platforms, demand_signal, feasibility,
             competition, overall, competitor_notes, competition_checked,
             status, last_scored_at, critic_model, synthesis_model, run_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            idea.title,
            idea.problem_statement,
            idea.proposed_solution,
            json.dumps(idea.supporting_nuggets),
            idea.source_threads,
            json.dumps(idea.source_platforms),
            idea.scores.demand_signal,
            idea.scores.feasibility,
            idea.scores.competition,  # NULL if unchecked (R29)
            idea.scores.overall,
            idea.competitor_notes,
            1 if idea.competition_checked else 0,
            idea.status,
            idea.last_scored_at,
            idea.critic_model,
            idea.synthesis_model,
            idea.run_id,
        ),
    )
    db.commit()
    return cur.lastrowid


def update_idea(db: sqlite3.Connection, idea: Idea) -> None:
    """Persist mutable score/status fields of an existing idea (R49/R53 re-scoring)."""
    db.execute(
        """
        UPDATE ideas SET
            demand_signal=?, feasibility=?, competition=?, overall=?,
            competitor_notes=?, competition_checked=?, status=?,
            last_scored_at=?, critic_model=?, synthesis_model=?,
            supporting_nuggets=?, source_threads=?, source_platforms=?
        WHERE id=?
        """,
        (
            idea.scores.demand_signal,
            idea.scores.feasibility,
            idea.scores.competition,
            idea.scores.overall,
            idea.competitor_notes,
            1 if idea.competition_checked else 0,
            idea.status,
            idea.last_scored_at,
            idea.critic_model,
            idea.synthesis_model,
            json.dumps(idea.supporting_nuggets),
            idea.source_threads,
            json.dumps(idea.source_platforms),
            idea.id,
        ),
    )
    db.commit()


def get_idea(db: sqlite3.Connection, idea_id: int) -> Optional[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()


def list_ideas(
    db: sqlite3.Connection, status: Optional[str] = None
) -> List[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    # §3.3 comparability: group by critic_model, strongest first within a model.
    order = "critic_model, overall DESC, id"
    if status:
        return db.execute(
            f"SELECT * FROM ideas WHERE status=? ORDER BY {order}", (status,)
        ).fetchall()
    return db.execute(f"SELECT * FROM ideas ORDER BY {order}").fetchall()


def unprocessed_nuggets(db: sqlite3.Connection) -> List[sqlite3.Row]:
    """R41: only nuggets the synthesizer has not yet claimed (synthesized_at IS NULL)."""
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT * FROM nuggets WHERE synthesized_at IS NULL ORDER BY id"
    ).fetchall()


def set_synthesized(db: sqlite3.Connection, unique_keys: List[str], when: str) -> None:
    """R50: stamp synthesized_at ONLY on the nuggets that joined an idea."""
    if not unique_keys:
        return
    db.executemany(
        "UPDATE nuggets SET synthesized_at=? WHERE unique_key=?",
        [(when, k) for k in unique_keys],
    )
    db.commit()


def nugget_by_key(db: sqlite3.Connection, unique_key: str) -> Optional[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT * FROM nuggets WHERE unique_key=?", (unique_key,)
    ).fetchone()


# --- M2.4: run ledger (jester-setup.md 13.1) -------------------------------


def start_run(
    db: sqlite3.Connection, run_id: str, models_used=None, origin: str = "manual"
) -> int:
    """Open a new run row. Refuses while another run is still 'running' (R25)."""
    row = db.execute("SELECT id FROM runs WHERE status='running' LIMIT 1").fetchone()
    if row is not None:
        raise ConcurrentRunError(f"run already in progress (id={row[0]})")
    cur = db.execute(
        "INSERT INTO runs(run_id, status, started_at, models_used, origin) "
        "VALUES(?, 'running', datetime('now'), ?, ?)",
        (run_id, json.dumps(models_used) if models_used else None, origin),
    )
    db.commit()
    return cur.lastrowid


def reap_running(
    db: sqlite3.Connection, older_than_minutes: Optional[int] = None
) -> int:
    """Flip orphaned 'running' rows (crashed session) to 'aborted'.

    With no age, every 'running' row is treated as an orphan. That is sound
    when runs are hours apart and only one session exists — and wrong the
    moment a schedule fires every N minutes, because the tick that is still
    working looks exactly like a corpse to the tick that just started. Pass
    `older_than_minutes` to reap only rows too old to still be alive.
    """
    if older_than_minutes is None:
        cur = db.execute(
            "UPDATE runs SET status='aborted', finished_at=datetime('now') "
            "WHERE status='running'"
        )
    else:
        cur = db.execute(
            "UPDATE runs SET status='aborted', finished_at=datetime('now') "
            "WHERE status='running' AND started_at <= datetime('now', ?)",
            (f"-{int(older_than_minutes)} minutes",),
        )
    db.commit()
    return cur.rowcount


def active_run(db: sqlite3.Connection) -> Optional[str]:
    """The run_id still marked 'running', or None. Lets a scheduled tick see
    that the previous one has not finished and stand down, rather than
    discovering it at `start_run` after it has already re-fetched the web."""
    row = db.execute(
        "SELECT run_id FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def get_run(db: sqlite3.Connection, run_pk: int) -> Optional[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute("SELECT * FROM runs WHERE id=?", (run_pk,)).fetchone()


def run_activity(
    db: sqlite3.Connection, origin: str = "scheduled", windows=(1, 24)
) -> dict:
    """How often a given origin has actually run, and what it produced.

    Counts rows, which is the only thing that can be counted honestly: a tick
    that stood down because the previous one was still working never opened a
    run, so it is absent here by design. That is the point — comparing this
    against the cadence is how you notice a schedule that fires but achieves
    nothing, which is exactly the failure this console kept hiding.

    `windows` are hours. SQLite stores `started_at` as UTC 'YYYY-MM-DD HH:MM:SS'
    via datetime('now'), so the comparison is done in the same terms.
    """
    db.row_factory = sqlite3.Row
    out = {"origin": origin, "windows": {}}
    total = db.execute(
        "SELECT COUNT(*) FROM runs WHERE origin=?", (origin,)
    ).fetchone()[0]
    out["total"] = total
    for hours in windows:
        row = db.execute(
            "SELECT COUNT(*) AS runs, "
            "  COALESCE(SUM(n_nuggets_kept), 0) AS nuggets, "
            "  COALESCE(SUM(n_ideas), 0) AS ideas, "
            "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed "
            "FROM runs WHERE origin=? AND started_at >= datetime('now', ?)",
            (origin, f"-{int(hours)} hours"),
        ).fetchone()
        out["windows"][str(hours)] = {
            "runs": row["runs"],
            "completed": row["completed"] or 0,
            "nuggets": row["nuggets"],
            "ideas": row["ideas"],
        }
    last = db.execute(
        "SELECT run_id, status, started_at, finished_at, n_comments, "
        "       n_nuggets_kept, n_ideas "
        "FROM runs WHERE origin=? ORDER BY id DESC LIMIT 1",
        (origin,),
    ).fetchone()
    out["last"] = dict(last) if last else None
    out["recent"] = [
        dict(r)
        for r in db.execute(
            "SELECT run_id, status, started_at, n_comments, n_nuggets_kept, n_ideas "
            "FROM runs WHERE origin=? ORDER BY id DESC LIMIT 12",
            (origin,),
        ).fetchall()
    ]
    return out


def get_runs(db: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    """Run history, newest first (id DESC == insertion order)."""
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


_RUN_SUMMARY_FIELDS = frozenset(
    {
        "status",
        "models_used",
        "n_comments",
        "n_batches",
        "n_posts",
        "n_nuggets_kept",
        "n_discarded_trivial",
        "trivial_share",
        "n_ideas",
        "floor_flags",
        "phase_checkpoints",
        "error",
        "duration_expected_s",
        "duration_actual_s",
        "prefilter_funnel",
        "top_near_misses",
        "platform_counts",
    }
)


def update_run_summary(db: sqlite3.Connection, run_id: str, **fields) -> None:
    """Patch counters/checkpoints on the run row; lists/dicts are JSON-encoded."""
    sets, vals = [], []
    for k, v in fields.items():
        if k not in _RUN_SUMMARY_FIELDS:
            raise ValueError(f"unknown runs field: {k}")
        if isinstance(v, (list, dict)):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(run_id)
    db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id=?", vals)
    db.commit()


def finish_run(
    db: sqlite3.Connection, run_id: str, status: str = "completed", **fields
) -> None:
    """Close out a run: stamp finished_at plus any final summary fields."""
    fields["status"] = status
    db.execute("UPDATE runs SET finished_at=datetime('now') WHERE run_id=?", (run_id,))
    update_run_summary(db, run_id, **fields)


def mark_batch_failed(db: sqlite3.Connection, batch_id: int) -> None:
    db.execute("UPDATE ingest_batch SET status='failed' WHERE id=?", (batch_id,))
    db.commit()


def count_failed_batches(db: sqlite3.Connection) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE status='failed'"
    ).fetchone()[0]


def requeue_failed(db: sqlite3.Connection) -> int:
    cur = db.execute("UPDATE ingest_batch SET status='pending' WHERE status='failed'")
    db.commit()
    return cur.rowcount


# --- R30-pattern budget ledger primitives (policy lives in jester.quota) ----


def quota_used(db: sqlite3.Connection, platform: str, day_key: str) -> int:
    row = db.execute(
        "SELECT used FROM quota WHERE platform=? AND day_key=?", (platform, day_key)
    ).fetchone()
    return row[0] if row else 0


def quota_add(db: sqlite3.Connection, platform: str, day_key: str, units: int) -> None:
    db.execute(
        "INSERT INTO quota(platform, day_key, used) VALUES(?,?,?) "
        "ON CONFLICT(platform, day_key) DO UPDATE SET used = used + excluded.used",
        (platform, day_key, units),
    )
    db.commit()


# --- §37.24 reembed recovery ---------------------------------------------------


def pending_reembeds(db: sqlite3.Connection) -> List[sqlite3.Row]:
    """Nuggets whose vector persistence failed (needs_reembed=1)."""
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT unique_key, raw_text FROM nuggets WHERE needs_reembed=1 ORDER BY id"
    ).fetchall()


def mark_reembedded(db: sqlite3.Connection, unique_key: str, embedding_id: str) -> None:
    db.execute(
        "UPDATE nuggets SET needs_reembed=0, embedding_id=? WHERE unique_key=?",
        (embedding_id, unique_key),
    )
    db.commit()


def retention_sweep(db: sqlite3.Connection, ttl_days: int = 30, now=None) -> dict:
    """§14 data minimization: wipe raw_text on nuggets past TTL and drop
    completed ingest batches past TTL. ``now`` injectable for tests."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=ttl_days)).isoformat()
    c1 = db.execute("UPDATE nuggets SET raw_text=NULL WHERE created_at < ?", (cutoff,))
    c2 = db.execute(
        "DELETE FROM ingest_batch WHERE status='done' AND created_at < ?", (cutoff,)
    )
    db.commit()
    return {"nuggets_raw": c1.rowcount, "batches": c2.rowcount}
