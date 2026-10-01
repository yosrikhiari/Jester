"""The journal: research -> experiment -> article, with proof required.

Jester already turns community posts into cited, scored ideas. The journal is
the other half: pick a question, run a real experiment, write it up, and let
nothing reach a reader that cannot be traced to a run or a stored source.

This module is the store. It owns `data/journal.db` -- a separate file, like
the signals archive, so a journal experiment can never disturb the nightly
pipeline. The prose itself is NOT in here: articles are Markdown files in
their own git repository (see `files.py`), and a revision row only records
which commit a version is.

What lives where, so nobody has to guess:

    journal          one article: its brief (question, metric, baseline...),
                     its state, where its file is
    transition       every state change, who made it, and what the gates said
    approval         every human decision. Gates only count actor = 'human';
                     an agent can propose, never approve
    revision         one saved version = one git commit of the article file
    check_result     the outcome of one proof check on one revision. The
                     checkers themselves (citations, numbers, lint, origin)
                     are slice J1; this table is the contract they write to
    experiment       a pre-registered test: hypothesis, metric, threshold,
                     baseline, planned runs, the benchmark checklist
    experiment_run   one run of it, with its raw data path (slice J2 fills it)
    publication      one post of one revision to one channel (slice J3)
    channel_metric   numbers read back from a publication (slice J8)
    artifact         a figure or table made from runs, with the hash of the
                     file as made, so a hand-edited chart is caught
    snapshot         a saved copy of a cited page: status, final URL, and the
                     path of its readable text. Shared by every journal; a
                     URL keeps every copy ever taken, newest wins
    generated_text   fingerprints of paragraphs a tool wrote, so the origin
                     check can tell them from paragraphs you typed

The tables for later slices exist now because the gates read them. A gate
that checked a placeholder would pass silently; a gate that reads an empty
table blocks, which is the honest answer until the slice that fills it lands.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DEFAULT_DB = "data/journal.db"

#: Who may open a human gate. Anything else -- an agent role name, a script --
#: is recorded but never counted by a gate.
HUMAN = "human"

#: Kinds of writing. Only these two must carry an experiment: a TIL, a note
#: or a post-mortem stands on sources and first-hand observation, and forcing
#: a fake experiment onto one would be exactly the theatre the journal exists
#: to prevent.
KINDS = ("til", "note", "article", "benchmark", "postmortem")
EXPERIMENT_KINDS = frozenset({"article", "benchmark"})

#: Human decisions a gate can ask for.
APPROVALS = ("choose", "plan", "review", "publish", "post")

#: The proof checks a revision must pass before review. Slice J1 builds the
#: checkers; until then nothing writes these rows, so no draft can reach
#: review by accident.
REQUIRED_CHECKS = ("citations", "numbers", "lint", "origin")

#: The benchmark checklist every experiment answers before its article can be
#: drafted. Brendan Gregg's seven questions, plus three of Gernot Heiser's
#: "benchmarking crimes" that the seven do not cover. "n/a" is an answer, as
#: long as it comes with a reason; a blank is not.
CHECKLIST = (
    ("why_not_double", "Why not double? What limits the result?"),
    ("tuned", "Was it tuned? Were both sides tuned the same way?"),
    ("limits", "Did it break limits (memory, rate, disk)?"),
    ("errors", "Did anything error, and are errors counted?"),
    ("reproduces", "Does it reproduce? How many runs, what spread?"),
    ("matters", "Does the difference matter in practice?"),
    ("happened", "Did it even happen? How do you know the work ran?"),
    ("baseline_fair", "Is the baseline fair and not your own straw man?"),
    ("absolute_numbers", "Are absolute numbers shown, not only ratios?"),
    ("platform_listed", "Is the platform listed (hardware, versions, seed)?"),
)

TABLES = ("journal", "transition", "approval", "revision", "check_result",
          "experiment", "experiment_run", "publication", "channel_metric",
          "snapshot", "artifact", "generated_text")

#: Columns added after a table first shipped. `CREATE TABLE IF NOT EXISTS`
#: never widens an existing table, so a database made by an older build gets
#: them here -- the same lesson the signals archive learned.
_EMPTY_TEXT = "TEXT NOT NULL DEFAULT ''"
_EMPTY_LIST = "TEXT NOT NULL DEFAULT '[]'"
ADDED_COLUMNS = (
    ("experiment", "threats", _EMPTY_LIST),
    ("experiment", "changes_after_lock", _EMPTY_LIST),
    ("experiment_run", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("experiment_run", "command", _EMPTY_TEXT),
    ("experiment_run", "env", "TEXT NOT NULL DEFAULT '{}'"),
    ("experiment_run", "exit_code", "INTEGER"),
    ("experiment_run", "duration_s", "REAL"),
    ("publication", "body_path", _EMPTY_TEXT),
    ("publication", "approved_sha256", _EMPTY_TEXT),
    ("publication", "external_id", _EMPTY_TEXT),
)

DDL = """
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  hypothesis TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT '',
  baseline TEXT NOT NULL DEFAULT '',
  prior_coverage TEXT NOT NULL DEFAULT '',
  disclosure TEXT NOT NULL DEFAULT '',
  canonical_url TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  parked_from TEXT NOT NULL DEFAULT '',
  abandoned_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS transition (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  checks TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS approval (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  what TEXT NOT NULL,
  actor TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  revision_id INTEGER,
  publication_id INTEGER
);
CREATE TABLE IF NOT EXISTS revision (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  n INTEGER NOT NULL,
  git_sha TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  path TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (journal_id, n)
);
CREATE TABLE IF NOT EXISTS check_result (
  id INTEGER PRIMARY KEY,
  revision_id INTEGER NOT NULL REFERENCES revision(id),
  kind TEXT NOT NULL,
  ok INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  hypothesis TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT '',
  threshold TEXT NOT NULL DEFAULT '',
  baseline TEXT NOT NULL DEFAULT '',
  n_planned INTEGER NOT NULL DEFAULT 0,
  checklist TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  locked_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS experiment_run (
  id INTEGER PRIMARY KEY,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id),
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '',
  raw_path TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS publication (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  revision_id INTEGER NOT NULL REFERENCES revision(id),
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  posted_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS channel_metric (
  id INTEGER PRIMARY KEY,
  publication_id INTEGER NOT NULL REFERENCES publication(id),
  at TEXT NOT NULL,
  views INTEGER,
  points INTEGER,
  comments INTEGER
);
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  final_url TEXT NOT NULL DEFAULT '',
  status INTEGER NOT NULL,
  content_type TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  text_path TEXT NOT NULL DEFAULT '',
  text_sha256 TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshot_url ON snapshot (url);
CREATE TABLE IF NOT EXISTS artifact (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  runs TEXT NOT NULL DEFAULT '[]',
  caption TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generated_text (
  id INTEGER PRIMARY KEY,
  journal_id INTEGER NOT NULL REFERENCES journal(id),
  sha256 TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class JournalError(Exception):
    """A request the store refuses: unknown slug, bad kind, duplicate."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_journal(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    """Open (and create) the journal database, making its directory if needed.

    Same courtesy as `open_signals`: `data/` does not exist in a fresh clone,
    and sqlite's error for a missing parent reads like a permissions problem.
    """
    parent = Path(db_path).parent
    if db_path != ":memory:" and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(DDL)
    _catch_up_columns(db)
    db.commit()
    return db


def _catch_up_columns(db: sqlite3.Connection) -> List[str]:
    added = []
    for table, column, ddl in ADDED_COLUMNS:
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")
    return added


def slugify(title: str, limit: int = 60) -> str:
    """`"Rules vs an 8B LLM: 500 posts"` -> `rules-vs-an-8b-llm-500-posts`."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:limit].rstrip("-")


# ---- journals ---------------------------------------------------------------

#: Brief fields `update_brief` may change. State, path and timestamps move
#: only through their own functions, so a typo in a field name cannot skip a
#: gate.
BRIEF_FIELDS = ("title", "question", "hypothesis", "metric", "baseline",
                "prior_coverage", "disclosure", "canonical_url")


def create_journal(db: sqlite3.Connection, title: str, kind: str = "article",
                   slug: str = "", question: str = "", path: str = "",
                   at: Optional[str] = None) -> sqlite3.Row:
    title = title.strip()
    if not title:
        raise JournalError("a journal needs a title")
    if kind not in KINDS:
        raise JournalError(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
    slug = slug or slugify(title)
    if not slug:
        raise JournalError(f"cannot make a slug from {title!r}; pass one")
    if find(db, slug) is not None:
        raise JournalError(f"a journal called {slug!r} already exists")
    at = at or now_utc()
    db.execute(
        "INSERT INTO journal (slug, title, kind, state, question, path, created_at, updated_at) "
        "VALUES (?, ?, ?, 'captured', ?, ?, ?, ?)",
        (slug, title, kind, question.strip(), path, at, at))
    db.commit()
    return get(db, slug)


def find(db: sqlite3.Connection, slug: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM journal WHERE slug = ?", (slug,)).fetchone()


def get(db: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = find(db, slug)
    if row is None:
        raise JournalError(f"no journal called {slug!r}")
    return row


def list_journals(db: sqlite3.Connection, state: str = "") -> List[sqlite3.Row]:
    if state:
        return db.execute("SELECT * FROM journal WHERE state = ? ORDER BY updated_at DESC, id DESC",
                          (state,)).fetchall()
    return db.execute("SELECT * FROM journal ORDER BY updated_at DESC, id DESC").fetchall()


def update_brief(db: sqlite3.Connection, slug: str, **fields: str) -> sqlite3.Row:
    row = get(db, slug)
    unknown = set(fields) - set(BRIEF_FIELDS)
    if unknown:
        raise JournalError(f"not a brief field: {', '.join(sorted(unknown))}")
    changes = {k: (v or "").strip() for k, v in fields.items() if v is not None}
    if "title" in changes and not changes["title"]:
        raise JournalError("the title cannot be blank")
    if not changes:
        return row
    sets = ", ".join(f"{k} = ?" for k in changes)
    db.execute(f"UPDATE journal SET {sets}, updated_at = ? WHERE id = ?",
               (*changes.values(), now_utc(), row["id"]))
    db.commit()
    return get(db, slug)


def set_path(db: sqlite3.Connection, slug: str, path: str) -> None:
    db.execute("UPDATE journal SET path = ? WHERE slug = ?", (path, slug))
    db.commit()


def history(db: sqlite3.Connection, journal_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM transition WHERE journal_id = ? ORDER BY id",
                      (journal_id,)).fetchall()


# ---- approvals --------------------------------------------------------------

def approve(db: sqlite3.Connection, journal_id: int, what: str, actor: str = HUMAN,
            note: str = "", revision_id: Optional[int] = None,
            publication_id: Optional[int] = None, at: Optional[str] = None) -> int:
    """Record a decision. Anyone may record one; only `actor == HUMAN` counts."""
    if what not in APPROVALS:
        raise JournalError(f"approval must be one of {', '.join(APPROVALS)} (got {what!r})")
    cur = db.execute(
        "INSERT INTO approval (journal_id, what, actor, at, note, revision_id, publication_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (journal_id, what, actor, at or now_utc(), note, revision_id, publication_id))
    db.commit()
    return cur.lastrowid


def human_approvals(db: sqlite3.Connection, journal_id: int, what: str) -> List[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM approval WHERE journal_id = ? AND what = ? AND actor = ? ORDER BY id",
        (journal_id, what, HUMAN)).fetchall()


# ---- revisions and checks ---------------------------------------------------

def record_revision(db: sqlite3.Connection, journal_id: int, git_sha: str,
                    content_sha256: str, path: str, note: str = "",
                    at: Optional[str] = None) -> sqlite3.Row:
    last = latest_revision(db, journal_id)
    n = (last["n"] + 1) if last else 1
    db.execute(
        "INSERT INTO revision (journal_id, n, git_sha, content_sha256, path, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (journal_id, n, git_sha, content_sha256, path, note, at or now_utc()))
    db.execute("UPDATE journal SET updated_at = ? WHERE id = ?", (now_utc(), journal_id))
    db.commit()
    return latest_revision(db, journal_id)


def latest_revision(db: sqlite3.Connection, journal_id: int) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM revision WHERE journal_id = ? ORDER BY n DESC LIMIT 1",
                      (journal_id,)).fetchone()


def revisions(db: sqlite3.Connection, journal_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM revision WHERE journal_id = ? ORDER BY n",
                      (journal_id,)).fetchall()


def record_check(db: sqlite3.Connection, revision_id: int, kind: str, ok: bool,
                 detail: Optional[Dict] = None, at: Optional[str] = None) -> int:
    if kind not in REQUIRED_CHECKS:
        raise JournalError(f"check must be one of {', '.join(REQUIRED_CHECKS)} (got {kind!r})")
    cur = db.execute(
        "INSERT INTO check_result (revision_id, kind, ok, detail, at) VALUES (?, ?, ?, ?, ?)",
        (revision_id, kind, int(bool(ok)), json.dumps(detail or {}), at or now_utc()))
    db.commit()
    return cur.lastrowid


def latest_checks(db: sqlite3.Connection, revision_id: int) -> Dict[str, bool]:
    """The newest result of each check kind on one revision.

    Newest, not any: a check that failed, then passed after a fix, passes; a
    check that passed and then failed on a re-run does not.
    """
    rows = db.execute(
        "SELECT kind, ok FROM check_result WHERE revision_id = ? ORDER BY id",
        (revision_id,)).fetchall()
    out: Dict[str, bool] = {}
    for r in rows:
        out[r["kind"]] = bool(r["ok"])
    return out


# ---- experiments ------------------------------------------------------------

def add_experiment(db: sqlite3.Connection, journal_id: int, hypothesis: str = "",
                   metric: str = "", threshold: str = "", baseline: str = "",
                   n_planned: int = 0, checklist: Optional[Dict[str, str]] = None,
                   at: Optional[str] = None) -> int:
    cur = db.execute(
        "INSERT INTO experiment (journal_id, hypothesis, metric, threshold, baseline, "
        "n_planned, checklist, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (journal_id, hypothesis, metric, threshold, baseline, n_planned,
         json.dumps(checklist or {}), at or now_utc()))
    db.commit()
    return cur.lastrowid


def experiments(db: sqlite3.Connection, journal_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM experiment WHERE journal_id = ? ORDER BY id",
                      (journal_id,)).fetchall()


def answer_checklist(db: sqlite3.Connection, experiment_id: int, answers: Dict[str, str]) -> None:
    unknown = set(answers) - {k for k, _ in CHECKLIST}
    if unknown:
        raise JournalError(f"not a checklist question: {', '.join(sorted(unknown))}")
    row = db.execute("SELECT checklist FROM experiment WHERE id = ?", (experiment_id,)).fetchone()
    if row is None:
        raise JournalError(f"no experiment {experiment_id}")
    merged = {**json.loads(row["checklist"] or "{}"), **answers}
    db.execute("UPDATE experiment SET checklist = ? WHERE id = ?",
               (json.dumps(merged), experiment_id))
    db.commit()


def add_run(db: sqlite3.Connection, experiment_id: int, status: str = "ok",
            raw_path: str = "", result: Optional[Dict] = None,
            started_at: Optional[str] = None, finished_at: Optional[str] = None) -> int:
    """Record one run. The first run locks the experiment's pre-registration."""
    at = started_at or now_utc()
    cur = db.execute(
        "INSERT INTO experiment_run (experiment_id, status, started_at, finished_at, raw_path, result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (experiment_id, status, at, finished_at or (at if status == "ok" else ""),
         raw_path, json.dumps(result or {})))
    db.execute("UPDATE experiment SET locked_at = ? WHERE id = ? AND locked_at = ''",
               (at, experiment_id))
    db.commit()
    return cur.lastrowid


def runs(db: sqlite3.Connection, experiment_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM experiment_run WHERE experiment_id = ? ORDER BY id",
                      (experiment_id,)).fetchall()


# ---- publications -----------------------------------------------------------

def add_publication(db: sqlite3.Connection, journal_id: int, revision_id: int,
                    channel: str, status: str = "draft", url: str = "",
                    posted_at: str = "") -> int:
    cur = db.execute(
        "INSERT INTO publication (journal_id, revision_id, channel, status, url, posted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (journal_id, revision_id, channel, status, url, posted_at))
    db.commit()
    return cur.lastrowid


def publications(db: sqlite3.Connection, journal_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM publication WHERE journal_id = ? ORDER BY id",
                      (journal_id,)).fetchall()


def add_metric(db: sqlite3.Connection, publication_id: int, at: str,
               views: Optional[int] = None, points: Optional[int] = None,
               comments: Optional[int] = None) -> int:
    cur = db.execute(
        "INSERT INTO channel_metric (publication_id, at, views, points, comments) "
        "VALUES (?, ?, ?, ?, ?)", (publication_id, at, views, points, comments))
    db.commit()
    return cur.lastrowid


def metric_times(db: sqlite3.Connection, publication_id: int) -> List[str]:
    return [r["at"] for r in db.execute(
        "SELECT at FROM channel_metric WHERE publication_id = ? ORDER BY at",
        (publication_id,))]


def register_generated(db: sqlite3.Connection, journal_id: int, text: str, role: str,
                       at: Optional[str] = None) -> str:
    """Record that a tool wrote this paragraph. Returns its fingerprint.

    The origin check compares fingerprints, so a paragraph you then edit is
    no longer the tool's: it is yours, and you answer for what it says.
    """
    from jester.journal.text import paragraph_hash

    digest = paragraph_hash(text)
    db.execute("INSERT INTO generated_text (journal_id, sha256, role, created_at) VALUES (?, ?, ?, ?)",
               (journal_id, digest, role, at or now_utc()))
    db.commit()
    return digest


def generated_hashes(db: sqlite3.Connection, journal_id: int) -> Dict[str, str]:
    return {r["sha256"]: r["role"] for r in db.execute(
        "SELECT sha256, role FROM generated_text WHERE journal_id = ?", (journal_id,))}


def counts_by_state(db: sqlite3.Connection) -> Dict[str, int]:
    return {r["state"]: r["n"] for r in db.execute(
        "SELECT state, COUNT(*) AS n FROM journal GROUP BY state")}


def missing_answers(checklist_json: str, keys: Iterable[str] = ()) -> List[str]:
    """Checklist questions with no answer. Whitespace is not an answer."""
    answers = json.loads(checklist_json or "{}")
    wanted = list(keys) or [k for k, _ in CHECKLIST]
    return [k for k in wanted if not str(answers.get(k, "")).strip()]
