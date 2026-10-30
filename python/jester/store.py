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
from pathlib import Path
from typing import List, Optional

from jester.models import Idea, Nugget

SCHEMA_VERSION = 1


#: Milliseconds a connection waits on a locked database before raising
#: SQLITE_BUSY. Raised from 5 s (F-02): the queue claim races the worker's
#: enqueue on every treat, and 5 s was tight enough to abort runs that a
#: slightly longer wait would have ridden out.
BUSY_TIMEOUT_MS = 15000

#: Environment variable selecting the SQLite journal mode. Default DELETE
#: preserves the documented bind-mount behaviour (WAL's -wal/-shm sidecars
#: cannot be arbitrated reliably over a Windows bind-mounted volume). Set
#: `JESTER_SQLITE_JOURNAL_MODE=wal` where the database lives on a local disk
#: or the isolated Docker volume — readers stop blocking writers there, which
#: is the contention the overview page feels most. Anything else falls back
#: to DELETE rather than failing the open.
JOURNAL_MODE_ENV = "JESTER_SQLITE_JOURNAL_MODE"


def _journal_mode() -> str:
    want = os.environ.get(JOURNAL_MODE_ENV, "delete").strip().upper()
    return "WAL" if want == "WAL" else "DELETE"


class SchemaVersionError(Exception):
    pass


class ConcurrentRunError(Exception):
    """Raised by start_run when another run row is still 'running' (R25 guard)."""

    pass


#: The `cluster_ideas.cluster_id` a draft carries when a fresh clustering pass
#: no longer groups any of the comments it was built from. Zero is safe as a
#: sentinel: `clusters.id` is AUTOINCREMENT and starts at 1, and
#: `cluster_ideas.cluster_id` has no foreign key. The draft is kept -- it is
#: somebody's work -- and surfaced as a synthetic "Detached drafts" group.
DETACHED_CLUSTER_ID = 0


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
    extractor_model    TEXT,
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
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    needs_score          INTEGER NOT NULL DEFAULT 0,
    -- EX-G3: failed re-score attempts since the idea entered (or last re-
    -- entered) the queue. Ordering by this before id is what stops one
    -- permanently refused idea from being asked first every single run.
    rescore_attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clusters (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT,
    label              TEXT,
    problem_statement  TEXT,
    size               INTEGER,            -- chunks, not nuggets
    n_nuggets          INTEGER,
    -- The number that says whether a theme means anything. Five chunks from
    -- one prolific commenter cluster beautifully and signify nothing.
    n_authors          INTEGER,
    -- Members whose author the archive never recorded. "0 authors" and "3
    -- authors we did not capture" are different findings.
    authors_unknown    INTEGER NOT NULL DEFAULT 0,
    n_threads          INTEGER,
    platforms          TEXT,               -- JSON list
    nugget_keys        TEXT,               -- JSON list
    coherence          REAL,               -- mean cosine of members to centroid
    -- WHICH embeddings produced this. A cluster built from the 32-dim hash
    -- stand-in is noise, and a row that cannot say which model made it cannot
    -- be told apart from a real one later.
    embedding_model    TEXT,
    single_author      INTEGER NOT NULL DEFAULT 0,
    single_thread      INTEGER NOT NULL DEFAULT 0,
    -- DD-M1: the labeller's disagreement with the threshold. Kept, not hidden:
    -- a group the model says shares no subject is a finding about the pass.
    incoherent         INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id  INTEGER NOT NULL,
    nugget_key  TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_text  TEXT,
    similarity  REAL,
    PRIMARY KEY (cluster_id, nugget_key, chunk_index)
);
-- Ideas generated FROM a cluster, held apart from the `ideas` archive until
-- somebody promotes one. Drafts are cheap to make and most will be discarded;
-- writing them straight into `ideas` would make the archive a scratchpad.
CREATE TABLE IF NOT EXISTS cluster_ideas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id         INTEGER NOT NULL,
    title              TEXT NOT NULL,
    problem_statement  TEXT,
    proposed_solution   TEXT,
    supporting_nuggets  TEXT,              -- JSON list
    demand_signal       REAL,
    feasibility         REAL,
    competition         REAL,
    overall             REAL,
    synthesis_model     TEXT,
    -- Set to the `ideas`.id once promoted, so a draft cannot be saved twice
    -- and the console can show which ones already landed.
    promoted_idea_id    INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
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
    duration_actual_s   REAL,
    -- T-C2: the environment the run executed in, as JSON (run_env_fingerprint
    -- builds it; start_run writes it once at row creation). The journal keeps
    -- the same pattern for experiment runs (lab.fingerprint): models_used
    -- says what was ASKED to run, and this says what machine and what code
    -- actually produced the numbers. DEFAULT '{}' covers a caller that does
    -- not pass one -- an empty record is a fact, never fabricated keys.
    env                 TEXT NOT NULL DEFAULT '{}'
);
-- ── The edit regime (plans/2026-10-17) ──────────────────────────────────
-- Everything a chat, agent or scheduler changes to this archive is routed
-- through python/jester/edits.py: proposals first, an append-only audit
-- row always, reverts written as NEW rows. No UPDATE and no DELETE is ever
-- issued against edit_log by code in this repo (guarded in tests). New
-- databases get the tables here; legacy ones get them from
-- edits.ensure_edits_schema, because the console opens archives with
-- migrate=False and would otherwise never run BASE_SCHEMA.
CREATE TABLE IF NOT EXISTS edit_log (
    id            TEXT PRIMARY KEY,
    created_utc   TEXT NOT NULL,
    actor_kind    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    tool_name     TEXT,
    edit_kind     TEXT NOT NULL,
    target_table  TEXT NOT NULL,
    target_keys   TEXT NOT NULL,
    before_json   TEXT NOT NULL,
    after_json    TEXT NOT NULL,
    reason        TEXT,
    models_used   TEXT,
    proposal_id   TEXT,
    reverts_edit  TEXT,
    outcome       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edit_proposals (
    proposal_id     TEXT PRIMARY KEY,
    created_utc     TEXT NOT NULL,
    actor_kind      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    edit_kind       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    target_keys     TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    diff_json       TEXT NOT NULL,
    affected_json   TEXT NOT NULL,
    downstream_json TEXT NOT NULL,
    reason          TEXT,
    models_used     TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed',
    decided_utc     TEXT,
    decision_note   TEXT,
    edit_id         TEXT
);
CREATE TABLE IF NOT EXISTS signal_rule_proposals (
    id            TEXT PRIMARY KEY,
    created_utc   TEXT NOT NULL,
    actor_kind    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    justification TEXT,
    status        TEXT NOT NULL DEFAULT 'proposed',
    decided_utc   TEXT,
    models_used   TEXT
);
"""

_ADD_COLUMNS = [
    "ALTER TABLE nuggets ADD COLUMN needs_reembed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE nuggets ADD COLUMN embedding_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN thread_id TEXT",
    "ALTER TABLE nuggets ADD COLUMN engagement_score REAL",
    "ALTER TABLE nuggets ADD COLUMN timestamp TEXT",
    # Which extractor produced the insight. Without it a run that crossed its
    # daily allowance halfway leaves real and stand-in rows indistinguishable.
    "ALTER TABLE nuggets ADD COLUMN extractor_model TEXT",
    # The critic was asked and did not answer: scores absent or stale, re-scored
    # by a later run. A column rather than a `status` value because status is
    # the operator's own marking and a failed re-score must not overwrite it.
    "ALTER TABLE ideas ADD COLUMN needs_score INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE ideas ADD COLUMN rescore_attempts INTEGER NOT NULL DEFAULT 0",
    # DD-M1: the labeller's `coherent: false` verdict survived parsing but had
    # nowhere to live, so the console could not mark the theme it described.
    "ALTER TABLE clusters ADD COLUMN incoherent INTEGER NOT NULL DEFAULT 0",
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
    # The record-type fork (C2). Go's worker writes 'listing' batches this side
    # deliberately never claims; the column has to exist on a Python-created
    # database or the Go writer hits a missing column on its first listing.
    "ALTER TABLE ingest_batch ADD COLUMN kind TEXT NOT NULL DEFAULT 'comment'",
    "ALTER TABLE ingest_batch ADD COLUMN listings TEXT",
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
    # How many nuggets a run set aside because the dedup lookup was unavailable.
    # NULL on rows written before this existed, which is a different fact from
    # 0 — the runs that died on exactly this recorded nothing at all.
    "ALTER TABLE runs ADD COLUMN n_dedup_deferred INTEGER",
    # ---- T-A2 R53: when competition was last verified on the web ------------
    # `competitors.needs_recheck` consults this stamp so a merged idea inside
    # the TTL window keeps its verdict instead of paying the search budget
    # again. NULL means the idea was never web-checked (the critic saw it and
    # answered, or the check failed/skipped) — a different fact from a recent
    # check, and the shape needs_recheck already treats as "must check".
    "ALTER TABLE ideas ADD COLUMN competition_checked_at TEXT",
    # T-C2: the pipeline run environment (mirror of the journal's
    # lab.fingerprint for experiment runs) — os/python/machine/cpu_count plus
    # a code snapshot of the repo root, written ONCE at start_run and never
    # merged into afterwards. The NOT NULL DEFAULT backfills every row a
    # legacy archive already holds, so the ALTER is legal with rows present;
    # old rows read '{}' — "no environment was recorded", which is true of
    # them rather than a fabricated stamp.
    "ALTER TABLE runs ADD COLUMN env TEXT NOT NULL DEFAULT '{}'",
]


#: Hot-path read indexes (F-03). Every one of these columns is filtered or
#: ordered by the console overview, the queue claim, or the ideas list on
#: every request, and none of them had a declarative index — only
#: `nuggets.unique_key` (UNIQUE) did, plus one ad-hoc CREATE INDEX in the
#: console. Applied idempotently after _ADD_COLUMNS, so the columns exist
#: first; each statement is attempted individually and OperationalError is
#: tolerated exactly like the column ALTERs (a legacy table missing a column
#: keeps working, just without that index until it is migrated).
_ADD_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ingest_batch_status ON ingest_batch(status, kind)",
    "CREATE INDEX IF NOT EXISTS idx_ingest_batch_thread ON ingest_batch(thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_nuggets_platform ON nuggets(platform, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_nuggets_run ON nuggets(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_nuggets_thread ON nuggets(thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_ideas_status_overall ON ideas(status, overall)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status_started ON runs(status, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_edit_proposals_status ON edit_proposals(status)",
    "CREATE INDEX IF NOT EXISTS idx_edit_log_target ON edit_log(target_table)",
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


#: Tables that are DERIVED and regenerated wholesale. When their shape
#: changes, reconcile_schema must drop them rather than preserving a renamed
#: copy: keeping `runs` history is the point of that mechanism, but a stale
#: `clusters_legacy` is pure cruft, and one accumulates on every schema change.
DERIVED_TABLES = frozenset({"clusters", "cluster_members"})


def schema_problem(db: sqlite3.Connection) -> Optional[dict]:
    """What this archive is missing for the current build, or None.

    The console opens with ``migrate=False`` on purpose: it must not reshape
    an archive another process owns. That leaves a stale file able to answer
    every request with a different "no such table/column" 500. This is the
    one check that turns those into a single actionable sentence instead.

    Tables and columns are compared against BASE_SCHEMA plus the columns
    ``open_db`` would add in place. Extra columns are not a problem here:
    reads ignore them, and reconcile_schema owns incompatible shapes.
    """
    try:
        have_tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        want = {table: list(columns) for table, columns in _schema_columns().items()}
        for table, columns in _alter_supplied().items():
            want.setdefault(table, [])
            want[table].extend(c for c in sorted(columns) if c not in want[table])
        missing_tables: List[str] = []
        missing_columns: List[str] = []
        for table, columns in want.items():
            if table not in have_tables:
                missing_tables.append(table)
                continue
            have = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            missing_columns.extend(f"{table}.{c}" for c in columns if c not in have)
    except sqlite3.OperationalError as exc:
        # A writer holds the lock: answer "nothing known to be missing" and let
        # the route's own lock handling decide. Refusing the console because a
        # check could not run is worse than the check not running.
        if "locked" in str(exc) or "busy" in str(exc):
            return None
        raise
    if not missing_tables and not missing_columns:
        return None
    return {
        "tables": sorted(missing_tables),
        "columns": sorted(missing_columns),
        "missing": sorted(missing_tables) + sorted(missing_columns),
    }


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
        if rows == 0 or table in DERIVED_TABLES:
            db.execute(f"DROP TABLE {table}")
            why = "was empty" if rows == 0 else f"derived, {rows} row(s) regenerable"
            actions.append(
                f"{table}: recreated ({why}, missing {', '.join(missing)})"
            )
        else:
            db.execute(f"DROP TABLE IF EXISTS {table}_legacy")
            db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
            actions.append(
                f"{table}: {rows} row(s) kept as {table}_legacy "
                f"(missing {', '.join(missing)})"
            )
    # Sweep any legacy copy of a derived table left by an earlier build. These
    # were never worth keeping — `clusters` is rebuilt from scratch on every
    # pass — and one appears on each schema change until something removes it.
    for table in DERIVED_TABLES:
        stale = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (f"{table}_legacy",),
        ).fetchone()
        if stale:
            db.execute(f"DROP TABLE {table}_legacy")
            actions.append(f"{table}_legacy: dropped (derived data, regenerable)")
    if actions:
        db.commit()
    return actions


def open_db(path: str, *, migrate: bool = True) -> sqlite3.Connection:
    """Open the archive. `migrate=False` opens it without reshaping it.

    A normal open MIGRATES: it reconciles the schema, runs BASE_SCHEMA and
    stamps schema_version. Every one of those needs write access, and
    `executescript` opens a write transaction even when the schema is already
    current. A second console pointed at an archive it does not own should not
    be doing that — two processes racing to reshape one file is a worse
    failure than anything it would fix.

    WHAT THIS DELIBERATELY IS NOT: a SQLite `mode=ro` connection. That was
    tried first and it does not survive a LIVE database. Reading a file whose
    other writers are active still needs write access to the DIRECTORY, for
    lock and journal handling, so plain SELECTs intermittently died with
    "attempt to write a readonly database" — a message that names the file and
    blames the wrong thing. On a quiet database the same code passed, which is
    exactly how that would have shipped.

    So the connection is ordinary and the restraint is in the caller: it takes
    read locks like any reader, and simply never writes.
    """
    if path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000.0)
    # Rollback journal (DELETE) by default, NOT WAL. The database is shared
    # between the console server and the ingestion worker — separate
    # processes, sometimes separate containers — over a Windows bind-mounted
    # volume. WAL requires the -wal/-shm sidecars, which that filesystem
    # cannot create/arbitrate reliably under concurrent access, surfacing as
    # "OperationalError: unable to open database file". The default rollback
    # journal uses a single -journal file with ordinary locking and avoids the
    # sidecars entirely; busy_timeout absorbs brief writer locks.
    #
    # F-02 opt-in: JESTER_SQLITE_JOURNAL_MODE=wal selects WAL on databases
    # that live on a local disk or the isolated Docker volume (see
    # docker-compose.yml: the throwaway archive already avoids the bind
    # mount). WAL mode is persistent — once set it survives reopen — so both
    # the Python and Go sides read the same variable and default identically.
    try:
        db.execute(f"PRAGMA journal_mode={_journal_mode()}")
    except sqlite3.OperationalError:
        pass
    db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if not migrate:
        db.row_factory = sqlite3.Row
        return db
    for note in reconcile_schema(db):
        # Never silently: an operator must know a legacy table was set aside.
        print(f"schema: {note}", file=sys.stderr)
    db.executescript(BASE_SCHEMA)
    for stmt in _ADD_COLUMNS:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # already present
    for stmt in _ADD_INDEXES:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # legacy shape without the column: index comes with migration
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
        # kind='comment' is the record-type fork, and it is load-bearing rather
        # than tidy. A listing is already structured — price, size, location,
        # dates — and has nothing whatsoever to gain from being read by a
        # language model; routing one through the extractor would spend tokens
        # to turn a number back into a sentence. This clause is what makes
        # "listings bypass the LLM" a property of the code instead of a
        # convention someone has to remember.
        #
        # IS NULL is belt-and-braces, not the migration path. SQLite's
        # `ADD COLUMN kind TEXT NOT NULL DEFAULT 'comment'` backfills every
        # pre-existing row with 'comment' and then forbids NULL, so the
        # 20,589-comment backlog stays claimable through the first clause
        # alone. The second only covers a database some other tool widened
        # without a default — cheap, and the failure it prevents (the whole
        # backlog silently disappearing) is far worse than the clause.
        "SELECT id, platform, source, thread_id, comments, thread_meta "
        "FROM ingest_batch "
        "WHERE status='pending' AND (kind = 'comment' OR kind IS NULL) "
        "ORDER BY id"
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
        "platform", "run_id", "embedding_model", "extractor_model",
        "synthesized_at", "trivial",
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
        n.extractor_model or "",
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


def replace_clusters(db: sqlite3.Connection, run_id: str, payloads: list) -> int:
    """Persist a clustering pass, replacing whatever the last one produced.

    Clusters are DERIVED — a fresh pass over the same archive is the truth, and
    keeping the previous pass's rows alongside would leave the console showing
    two contradictory groupings of the same nuggets with no way to tell which
    is current. Draft ideas are preserved: those are somebody's work, not
    derived data, so they outlive the cluster that prompted them.

    A draft also has to outlive the cluster ID. The pass deletes every cluster
    row and mints fresh ids, so a surviving `cluster_ideas.cluster_id` names a
    group that no longer exists — and on an archive whose `clusters` table
    predates AUTOINCREMENT, an id another theme now reuses. Either way the
    draft silently vanishes from every theme's list. Each draft is re-homed to
    the new cluster sharing the most comments with it — a tie goes to the
    LOWEST cluster id; one that shares none moves to `DETACHED_CLUSTER_ID`
    instead of being deleted or left orphaned.
    """
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    drafts = cur.execute(
        "SELECT id, supporting_nuggets FROM cluster_ideas").fetchall()
    db.execute("DELETE FROM cluster_members")
    db.execute("DELETE FROM clusters")
    placed = []  # (new cluster id, its nugget keys), in payload order
    n = 0
    for p in payloads:
        cur = db.execute(
            """
            INSERT INTO clusters
                (run_id, label, problem_statement, size, n_nuggets, n_authors,
                 authors_unknown, n_threads, platforms, nugget_keys, coherence,
                 embedding_model, single_author, single_thread, incoherent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                p.get("label") or "",
                p.get("problem_statement") or "",
                int(p.get("size") or 0),
                int(p.get("n_nuggets") or 0),
                int(p.get("n_authors") or 0),
                int(p.get("authors_unknown") or 0),
                int(p.get("n_threads") or 0),
                p.get("platforms") or "[]",
                p.get("nugget_keys") or "[]",
                float(p.get("coherence") or 0.0),
                p.get("embedding_model") or "",
                1 if p.get("single_author") else 0,
                1 if p.get("single_thread") else 0,
                1 if p.get("incoherent") else 0,
            ),
        )
        cid = cur.lastrowid
        try:
            keys = set(json.loads(p.get("nugget_keys") or "[]"))
        except (TypeError, ValueError):
            keys = set()
        placed.append((cid, keys))
        for m in p.get("members") or []:
            db.execute(
                "INSERT OR REPLACE INTO cluster_members "
                "(cluster_id, nugget_key, chunk_index, chunk_text, similarity) "
                "VALUES (?,?,?,?,?)",
                (cid, m["nugget_key"], int(m.get("chunk_index") or 0),
                 m.get("text") or "", float(m.get("similarity") or 0.0)),
            )
        n += 1
    for draft in drafts:
        try:
            keys = set(json.loads(draft["supporting_nuggets"] or "[]"))
        except (TypeError, ValueError):
            keys = set()
        home = _best_overlap_home(db, keys)
        db.execute("UPDATE cluster_ideas SET cluster_id=? WHERE id=?",
                   (home, draft["id"]))
    db.commit()
    return n


def _best_overlap_home(db: sqlite3.Connection, keys: set) -> int:
    """The live cluster sharing the most nugget keys with `keys`, else the
    detached sentinel. Ties go to the LOWEST cluster id.

    Both placements go through here — the draft re-homing after a pass and
    the insert-time resolution for a draft whose theme vanished mid-work —
    so the two paths cannot drift apart.
    """
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    placed = []
    for c in cur.execute("SELECT id, nugget_keys FROM clusters").fetchall():
        try:
            cluster_keys = set(json.loads(c["nugget_keys"] or "[]"))
        except (TypeError, ValueError):
            cluster_keys = set()
        placed.append((c["id"], cluster_keys))
    best_id, best_overlap = DETACHED_CLUSTER_ID, 0
    # sorted() pins the walk to ascending ids, so with strict `>` a tie
    # keeps the LOWEST cluster id — a property of the ids, not of the
    # order the rows happen to come back in.
    for cid, cluster_keys in sorted(placed):
        overlap = len(keys & cluster_keys)
        if overlap > best_overlap:
            best_id, best_overlap = cid, overlap
    return best_id


def list_clusters(db: sqlite3.Connection) -> List[sqlite3.Row]:
    """Themes, biggest first. No cap: see the Nuggets page for what a silent
    default limit costs."""
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM cluster_ideas ci WHERE ci.cluster_id = c.id) AS n_ideas, "
        # What the theme list tags a row with ("draft 7.3" / "idea 7.3")
        # without fetching every theme's detail.
        "(SELECT MAX(ci.overall) FROM cluster_ideas ci WHERE ci.cluster_id = c.id) AS idea_best, "
        "(SELECT COUNT(*) FROM cluster_ideas ci WHERE ci.cluster_id = c.id "
        " AND ci.promoted_idea_id IS NOT NULL) AS n_ideas_saved "
        "FROM clusters c ORDER BY c.n_nuggets DESC, c.id ASC"
    ).fetchall()


def get_cluster(db: sqlite3.Connection, cluster_id: int):
    db.row_factory = sqlite3.Row
    return db.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()


def cluster_members(db: sqlite3.Connection, cluster_id: int) -> List[sqlite3.Row]:
    """Members with the nugget detail joined on, so a reviewer can see who said
    a thing and where — not just the text."""
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT m.nugget_key, m.chunk_index, m.chunk_text, m.similarity, "
        "n.platform, n.author, n.source_url, n.created_utc, n.upvotes, "
        "n.post_title, n.community, n.extracted_insight "
        "FROM cluster_members m LEFT JOIN nuggets n ON n.unique_key = m.nugget_key "
        "WHERE m.cluster_id=? ORDER BY m.similarity DESC",
        (cluster_id,),
    ).fetchall()


def insert_cluster_idea(db: sqlite3.Connection, cluster_id: int, idea) -> int:
    return _insert_cluster_idea_row(db, cluster_id, idea, commit=True)


def _insert_cluster_idea_row(db: sqlite3.Connection, cluster_id: int, idea,
                             commit: bool = True) -> int:
    cur = db.execute(
        """
        INSERT INTO cluster_ideas
            (cluster_id, title, problem_statement, proposed_solution,
             supporting_nuggets, demand_signal, feasibility, competition,
             overall, synthesis_model)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cluster_id,
            idea.title,
            idea.problem_statement,
            idea.proposed_solution,
            json.dumps(idea.supporting_nuggets or []),
            idea.scores.demand_signal,
            idea.scores.feasibility,
            idea.scores.competition,
            idea.scores.overall,
            idea.synthesis_model or "",
        ),
    )
    draft_id = int(cur.lastrowid)
    if commit:
        db.commit()
    return draft_id


def insert_cluster_idea_for_theme(db: sqlite3.Connection, cluster_id: int,
                                  idea) -> int:
    """Save a draft, deciding its theme at INSERT time.

    The console checks the theme exists, then spends tens of seconds inside
    the synthesizer and the critic. A re-clustering pass can commit in that
    window — it deletes every cluster row and mints fresh ids — so the id
    in hand may name nothing by the time the draft is written, the exact
    orphan replace_clusters exists to stop. Placement is therefore resolved
    inside the same transaction as the insert: a theme still there keeps
    the draft; a dissolved theme hands it to the live theme sharing the
    most supporting_nuggets; one that shares none goes to
    DETACHED_CLUSTER_ID. A dead id is never written.
    """
    try:
        keys = set(idea.supporting_nuggets or [])
    except TypeError:
        keys = set()
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    db.execute("BEGIN IMMEDIATE")
    try:
        row = cur.execute("SELECT id FROM clusters WHERE id=?",
                          (cluster_id,)).fetchone()
        home = cluster_id if row is not None else _best_overlap_home(db, keys)
        draft_id = _insert_cluster_idea_row(db, home, idea, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return draft_id


def list_cluster_ideas(db: sqlite3.Connection, cluster_id: Optional[int] = None):
    db.row_factory = sqlite3.Row
    if cluster_id is None:
        return db.execute(
            "SELECT * FROM cluster_ideas ORDER BY id DESC"
        ).fetchall()
    return db.execute(
        "SELECT * FROM cluster_ideas WHERE cluster_id=? ORDER BY id DESC",
        (cluster_id,),
    ).fetchall()


def promote_cluster_idea(db: sqlite3.Connection, draft_id: int) -> dict:
    """Move one draft into the `ideas` archive.

    Refuses a second promotion rather than silently creating a duplicate: the
    draft records which idea it became, and a double-click on Save is a much
    more likely event than a deliberate wish for two copies.
    """
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM cluster_ideas WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": f"no draft idea {draft_id}"}
    if row["promoted_idea_id"]:
        return {
            "ok": False,
            "error": f"already saved as idea #{row['promoted_idea_id']}",
            "idea_id": row["promoted_idea_id"],
        }
    keys = json.loads(row["supporting_nuggets"] or "[]")
    platforms = sorted({
        (r["platform"] or "")
        for r in db.execute(
            "SELECT platform FROM nuggets WHERE unique_key IN (%s)"
            % ",".join("?" * len(keys)),
            keys,
        ).fetchall()
    } - {""}) if keys else []
    threads = db.execute(
        "SELECT COUNT(DISTINCT thread_id) FROM nuggets WHERE unique_key IN (%s)"
        % ",".join("?" * len(keys)),
        keys,
    ).fetchone()[0] if keys else 0
    cur = db.execute(
        """
        INSERT INTO ideas
            (title, problem_statement, proposed_solution, supporting_nuggets,
             source_threads, source_platforms, demand_signal, feasibility,
             competition, overall, status, synthesis_model, run_id, last_scored_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,'new',?,?,?)
        """,
        (
            row["title"], row["problem_statement"], row["proposed_solution"],
            row["supporting_nuggets"], int(threads or 0), json.dumps(platforms),
            row["demand_signal"], row["feasibility"], row["competition"],
            row["overall"], row["synthesis_model"] or "",
            f"cluster-{row['cluster_id']}", _now(),
        ),
    )
    idea_id = int(cur.lastrowid)
    db.execute(
        "UPDATE cluster_ideas SET promoted_idea_id=? WHERE id=?", (idea_id, draft_id)
    )
    db.commit()
    return {"ok": True, "idea_id": idea_id}


def delete_cluster_idea(db: sqlite3.Connection, draft_id: int) -> bool:
    cur = db.execute("DELETE FROM cluster_ideas WHERE id=?", (draft_id,))
    db.commit()
    return cur.rowcount > 0


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
        "engagement_score, synthesized_at, created_at, extractor_model, "
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
    # T-A2 R53: the web-check stamp round-trips. getattr (not an attribute
    # access) because Idea pins no field for it — a hand- or model-built idea
    # with no verdict behind it simply stores NULL, which is the truth.
    stamp = getattr(idea, "competition_checked_at", None)  # T-A2 R53
    cur = db.execute(
        """
        INSERT INTO ideas
            (title, problem_statement, proposed_solution, supporting_nuggets,
             source_threads, source_platforms, demand_signal, feasibility,
             competition, overall, competitor_notes, competition_checked,
             status, last_scored_at, critic_model, synthesis_model, run_id,
             needs_score, competition_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            1 if idea.needs_score else 0,
            stamp,  # T-A2 R53
        ),
    )
    db.commit()
    return cur.lastrowid


def update_idea(db: sqlite3.Connection, idea: Idea) -> None:
    """Persist mutable score/status fields of an existing idea (R49/R53 re-scoring)."""
    stamp = getattr(idea, "competition_checked_at", None)  # T-A2 R53
    db.execute(
        """
        UPDATE ideas SET
            demand_signal=?, feasibility=?, competition=?, overall=?,
            competitor_notes=?, competition_checked=?, status=?,
            last_scored_at=?, critic_model=?, synthesis_model=?,
            supporting_nuggets=?, source_threads=?, source_platforms=?,
            needs_score=?, competition_checked_at=?
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
            1 if idea.needs_score else 0,
            stamp,  # T-A2 R53: a re-score must not erase a check stamp it did
            # not renew (apply_scores' preserve path leaves the attr untouched;
            # apply_verdict stamps when a verdict really checked).
            idea.id,
        ),
    )
    db.commit()


def mark_needs_score(db: sqlite3.Connection, idea_id: int) -> None:
    """Flag an idea for a later re-score without touching its scores.

    Entering the queue is a fresh reason to ask, so a counter left over from
    attempts for the PREVIOUS reason starts at zero. Without this, growth
    would put an idea behind every row that merely failed more recently.
    """
    db.execute(
        "UPDATE ideas SET needs_score=1, rescore_attempts=0 WHERE id=?",
        (idea_id,),
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


# T-C2 ---------------------------------------------------------------------
#
# THE RUN'S ENVIRONMENT. The journal stamps its experiment runs with a
# fingerprint (journal/lab.py fingerprint()) and refuses to count runs whose
# code is uncommitted; a pipeline run recorded models_used — what was ASKED
# to run — and nothing about the machine, the interpreter, or the checkout
# that produced the numbers. A score that cannot be traced to its
# environment is not reproducible: exactly the gap the journal closed.
#
# journal/lab is deliberately NOT imported here — the store is a leaf module
# the journal imports, and inverting that for one dict would couple pipeline
# storage to the experiments feature for good. The code part is therefore a
# small local recreation of what lab.code_state measures (rev-parse HEAD +
# status --porcelain), and cli.cmd_run passes the finished dict into
# start_run, which writes it once at row creation.

def _env_git(argv: List[str], timeout: float = 10.0) -> Optional[str]:
    """stdout of a git command, or None when it failed, timed out or is not
    installed — the same contract as lab._quiet, kept local for the same
    decoupling reason. The caller turns None into an explicit
    {"unavailable": ...} entry: a missing stamp must not read as "clean"."""
    import subprocess

    try:
        done = subprocess.run(list(argv), capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _env_code_state(code_dir: Path) -> dict:
    """The checkout's commit + dirty flag, or an explicit unavailable entry.

    Every degradation here is EXPLICIT. A run whose code state could not be
    read must not look like a run from a clean checkout. A failed `rev-parse`
    loses the whole code entry; a failed `status` keeps the commit it did
    read and writes `dirty: None` plus the reason, because "could not ask"
    is not "clean"."""
    commit = _env_git(["git", "-C", str(code_dir), "rev-parse", "HEAD"])
    if commit is None:
        return {"unavailable": "git rev-parse HEAD did not answer "
                               "(not a checkout, or no git binary?)"}
    status = _env_git(["git", "-C", str(code_dir), "status", "--porcelain"])
    if status is None:
        return {"commit": commit.strip(), "dirty": None,
                "unavailable": "git status --porcelain did not answer; "
                               "dirty state unknown"}
    return {"commit": commit.strip(), "dirty": bool(status.strip())}


def run_env_fingerprint(code_dir: Optional[Path] = None) -> dict:
    """The environment a pipeline run executes in, JSON-ready.

    Written once onto the run row (see start_run) — never merged into later,
    and deliberately not a field of update_run_summary. os/python/machine/
    cpu_count mirror lab.fingerprint; code mirrors lab.code_state for the
    repo root this store lives in (python/jester/store.py → parents[2]), or
    the code_dir a caller passes. The whole collection is wrapped: a
    fingerprint problem — no git, a failed platform call, anything —
    degrades the record to a single {"unavailable": ...} entry instead of
    failing or materially slowing the very run it describes.
    """
    import platform

    try:
        root = Path(code_dir) if code_dir else Path(__file__).resolve().parents[2]
        return {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "code": _env_code_state(root),
        }
    except Exception as exc:  # noqa: BLE001 — the snapshot must never fail a run
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def start_run(
    db: sqlite3.Connection, run_id: str, models_used=None, origin: str = "manual",
    env: Optional[dict] = None,
) -> int:
    """Open a new run row. Refuses while another run is still 'running' (R25).

    T-C2: ``env`` is the run's environment fingerprint (run_env_fingerprint
    builds it). It is written ONCE, right here, and is deliberately NOT a
    field of update_run_summary — merging into it afterwards would let a
    run's environment be back-dated, and the row exists precisely so a score
    can be traced to the machine and code that produced it. A caller that
    passes none records '{}' — an empty environment is a fact, same as the
    DEFAULT the column carries."""
    row = db.execute("SELECT id FROM runs WHERE status='running' LIMIT 1").fetchone()
    if row is not None:
        raise ConcurrentRunError(f"run already in progress (id={row[0]})")
    cur = db.execute(
        "INSERT INTO runs(run_id, status, started_at, models_used, origin, env) "
        "VALUES(?, 'running', datetime('now'), ?, ?, ?)",
        (run_id, json.dumps(models_used) if models_used else None, origin,
         json.dumps(env) if env is not None else "{}"),
    )
    db.commit()
    return cur.lastrowid


def add_to_models_used(db: sqlite3.Connection, run_id: str, extra: dict) -> None:
    """Merge `extra` into a run's `models_used` record.

    The record is written when the run starts (what was ASKED for); this adds
    what happened (who answered, who fell back) once it is known.
    """
    row = db.execute(
        "SELECT models_used FROM runs WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)
    ).fetchone()
    if row is None:
        return
    try:
        record = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        record = {}
    if not isinstance(record, dict):
        record = {"models": record}
    record.update(extra)
    db.execute(
        "UPDATE runs SET models_used=? WHERE id=(SELECT id FROM runs WHERE run_id=? "
        "ORDER BY id DESC LIMIT 1)",
        (json.dumps(record), run_id),
    )
    db.commit()


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
    # Whatever killed the run could not write its own epitaph — that is what
    # makes it an orphan. Say so on the row instead of leaving `error` NULL:
    # four consecutive treats aborted here reading as unexplained, and the
    # traceback that explained them existed only in a log file nobody had a
    # reason to open. Never overwrite a real error a process managed to record.
    reason = (
        "no exit recorded — the process died without closing this run "
        "(see the schedule log for the same timestamp)"
    )
    if older_than_minutes is None:
        cur = db.execute(
            "UPDATE runs SET status='aborted', finished_at=datetime('now'), "
            "error=COALESCE(error, ?) WHERE status='running'",
            (reason,),
        )
    else:
        cur = db.execute(
            "UPDATE runs SET status='aborted', finished_at=datetime('now'), "
            "error=COALESCE(error, ?) "
            "WHERE status='running' AND started_at <= datetime('now', ?)",
            (reason, f"-{int(older_than_minutes)} minutes"),
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
        "n_dedup_deferred",
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


# Payload keys stripped from a listing observation once it is past TTL.
#
# NOT the whole row, and not the whole payload. An observation IS the price
# history - the archive appends one every time a listing is seen again, and
# that series is the reason the table exists - so deleting old observations
# would delete the answer to "what did this cost in June" rather than
# minimizing anything. What ages out is the same category §14 wipes from a
# nugget: the bulk free text, plus the one field that names a person.
#
# `description` is an agent's prose, the listing equivalent of raw_text.
# `seller` is a name, and on portals that carry private sellers it is personal
# data with no analytic use once the price series is recorded.
#
# `title` deliberately stays. It is short, it is what makes a surviving row
# legible to whoever reads the series, and dropping it would leave a price
# history nobody can identify.
_LISTING_TTL_KEYS = ("description", "seller")


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    """Whether `name` is a table in this archive.

    Asked explicitly rather than by catching OperationalError around the real
    query. A blanket except here would swallow a genuine schema fault and
    report a clean sweep - the same shape as the bug that made `read_listings`
    return zero rows over a 920-row archive and call it success.
    """
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _sweep_listing_payloads(db: sqlite3.Connection, cutoff: str) -> int:
    """Strip `_LISTING_TTL_KEYS` from listing observations older than cutoff.

    Returns the number of rows actually rewritten - a row that carried none of
    those keys is left alone rather than rewritten to an identical value.
    """
    if not _table_exists(db, "listing_observation"):
        return 0
    rows = db.execute(
        "SELECT id, payload FROM listing_observation WHERE observed_at < ?",
        (cutoff,),
    ).fetchall()
    swept = 0
    for oid, payload in rows:
        try:
            doc = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        if not any(k in doc for k in _LISTING_TTL_KEYS):
            continue
        for k in _LISTING_TTL_KEYS:
            doc.pop(k, None)
        db.execute(
            "UPDATE listing_observation SET payload=? WHERE id=?",
            (json.dumps(doc, ensure_ascii=False, sort_keys=True), oid),
        )
        swept += 1
    return swept


def retention_sweep(db: sqlite3.Connection, ttl_days: int = 30, now=None) -> dict:
    """§14 data minimization: wipe raw_text on nuggets past TTL, drop completed
    ingest batches past TTL, and age the free text and seller name out of
    listing observations past TTL. ``now`` injectable for tests."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=ttl_days)).isoformat()
    c1 = db.execute("UPDATE nuggets SET raw_text=NULL WHERE created_at < ?", (cutoff,))
    c2 = db.execute(
        "DELETE FROM ingest_batch WHERE status='done' AND created_at < ?", (cutoff,)
    )
    listings = _sweep_listing_payloads(db, cutoff)
    db.commit()
    return {
        "nuggets_raw": c1.rowcount,
        "batches": c2.rowcount,
        "listings": listings,
    }
