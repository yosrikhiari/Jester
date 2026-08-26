// Package store implements the SQLite durable queue and result tables shared
// with the Python agent pipeline. The Go worker writes ingest_batch rows; the
// Python side reads them and writes nuggets/ideas/runs.
//
// schemaVersion in meta must equal config.SchemaVersion or both sides refuse to
// run (cross-language contract guard, §15 Checkpoint 1).
package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"jester/internal/config"
	"jester/internal/reddit"
)

// schema is the DDL for the tables Go OWNS under §25.3/D-17: meta,
// ingest_batch, ingested_comments. Python owns nuggets/ideas/runs/quota —
// creating them from here would poison shared databases with stale M1.0-era
// shapes (§37.20 Phase A), so this side no longer touches them.
const schema = `
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS ingest_batch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  source TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  batch_index INTEGER NOT NULL,
  comments TEXT NOT NULL,
  -- The thread/video/topic the comments hang off, as JSON: title, author,
  -- score, upvote ratio, view and comment counts. A batch used to carry a
  -- bare thread_id, so nothing downstream could say what the discussion was
  -- even about.
  thread_meta TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  claimed_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ingested_comments (
  fingerprint TEXT PRIMARY KEY,
  thread_id TEXT,
  ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- When each curated source was last walked. Without this a run budget is
-- always spent on whatever sits first in sources.yaml: the scheduler visited
-- hn-ask's same two threads every 30 minutes and never reached the other
-- fifteen sources, so it re-fetched an exhausted thread forever.
CREATE TABLE IF NOT EXISTS source_state (
  name TEXT PRIMARY KEY,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queued INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0
);
`

// addColumns are the columns added to Go-owned tables after the first release.
// Each must be safe to re-run: SQLite has no ADD COLUMN IF NOT EXISTS, so the
// duplicate error is the expected outcome on an up-to-date database.
var addColumns = []string{
	"ALTER TABLE ingest_batch ADD COLUMN thread_meta TEXT",
}

// isDuplicateColumn reports whether err is SQLite's "this column is already
// here" — the ONLY error an idempotent ALTER may ignore. Swallowing every
// error would hide a genuinely broken migration behind a working-looking
// startup.
func isDuplicateColumn(err error) bool {
	return err != nil && strings.Contains(strings.ToLower(err.Error()), "duplicate column name")
}

// Store wraps a *sql.DB for Jester.
type Store struct {
	db *sql.DB
}

// Open opens (creating if needed) the SQLite db at path and ensures schema and
// schema_version match config.SchemaVersion.
func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Rollback journal (DELETE), not WAL — matches the Python side. The DB is
	// shared with the Python pipeline across processes (sometimes containers)
	// over a bind-mounted volume where WAL's -wal/-shm sidecars cannot be
	// arbitrated reliably, surfacing as "unable to open database file". Forcing
	// DELETE here keeps both sides on one mode and drops the sidecars entirely;
	// busy_timeout absorbs brief writer locks.
	for _, pragma := range []string{
		"PRAGMA journal_mode=DELETE",
		"PRAGMA busy_timeout=5000",
	} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, fmt.Errorf("set %s: %w", pragma, err)
		}
	}
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("ensure schema: %w", err)
	}
	// CREATE TABLE IF NOT EXISTS never widens a table that already exists, so
	// a database written by an earlier build keeps its old shape and every
	// INSERT naming a new column fails on it. Python's store has carried an
	// idempotent ALTER list for exactly this reason; this side needed one too
	// the moment it grew a column. A duplicate-column error IS the success
	// case here, so it is swallowed and nothing else is.
	for _, stmt := range addColumns {
		if _, err := db.Exec(stmt); err != nil && !isDuplicateColumn(err) {
			db.Close()
			return nil, fmt.Errorf("%s: %w", stmt, err)
		}
	}
	s := &Store{db: db}
	if err := s.ensureVersion(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

func (s *Store) ensureVersion() error {
	want := fmt.Sprintf("%d", config.SchemaVersion)
	var have string
	err := s.db.QueryRow("SELECT value FROM meta WHERE key='schema_version'").Scan(&have)
	if err == sql.ErrNoRows {
		if _, err := s.db.Exec("INSERT INTO meta(key,value) VALUES('schema_version',?)", want); err != nil {
			return fmt.Errorf("set schema_version: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("read schema_version: %w", err)
	}
	if have != want {
		return fmt.Errorf("schema_version mismatch: db=%s code=%s", have, want)
	}
	return nil
}

// Version returns the stored schema_version.
func (s *Store) Version() (int, error) {
	var v string
	if err := s.db.QueryRow("SELECT value FROM meta WHERE key='schema_version'").Scan(&v); err != nil {
		return 0, err
	}
	var n int
	if _, err := fmt.Sscanf(v, "%d", &n); err != nil {
		return 0, err
	}
	return n, nil
}

// Batch is a unit of fetched comments enqueued for the Python pipeline.
type Batch struct {
	RunID    string
	Platform string
	Source   string
	ThreadID string
	Index    int
	Comments []Comment
	// Post is the thread / video / topic these comments came from. Nil when
	// the adapter could not read it — which is a real state, distinct from
	// "read it and it was empty", and is stored as SQL NULL.
	Post *reddit.FetchedPost
}

// Comment is one raw comment with its dedup fingerprint, serialised into
// ingest_batch.comments as the cross-language handoff (D-17).
//
// The top three keys — body, fingerprint, upvotes — are the ORIGINAL contract
// and keep their exact names and meanings, because the Python pipeline has
// always read those and a schema change is not worth a flag day. Everything
// the adapters know beyond them lives under `detail`.
//
// Nested, NOT embedded, and the distinction matters: with the adapter struct
// embedded, encoding/json resolves the two `upvotes` keys by depth and the
// shallower plain int64 wins. That silently flattens *upvotes = nil ("this
// platform does not publish a per-comment score" — every Hacker News comment)
// into `"upvotes": 0` ("nobody upvoted it"). The nesting keeps the nullable
// field addressable at detail.upvotes, where absent still means absent.
type Comment struct {
	Body        string `json:"body"`
	Fingerprint string `json:"fingerprint"`
	// Upvotes is the legacy engagement number: the platform's published score,
	// whatever that platform counts. It is NOT a claim that downvotes were
	// zero — see detail for what was actually published.
	Upvotes int64 `json:"upvotes"`
	// Detail is everything else the adapter captured: author, timestamp,
	// permalink, tree position, likes, replies, awards, flags.
	Detail reddit.FetchedComment `json:"detail"`
}

// NewComment builds the stored shape from what an adapter fetched.
//
// Detail.Body is deliberately cleared: it is already the top-level `body`, and
// a thread can carry 500 comments — duplicating every one of them would double
// the queue payload to say the same thing twice.
func NewComment(fp string, c reddit.FetchedComment) Comment {
	detail := c
	detail.Body = ""
	return Comment{
		Body:        c.Body,
		Fingerprint: fp,
		Upvotes:     c.Score,
		Detail:      detail,
	}
}

// EnqueueBatch inserts a pending ingest_batch row.
func (s *Store) EnqueueBatch(b Batch) (int64, error) {
	payload, err := json.Marshal(b.Comments)
	if err != nil {
		return 0, err
	}
	// NULL, not "{}" or "null", when there is no post: a reader can then tell
	// "the adapter never captured this" from "it captured an empty one".
	var meta any
	if b.Post != nil {
		raw, err := json.Marshal(b.Post)
		if err != nil {
			return 0, err
		}
		meta = string(raw)
	}
	res, err := s.db.Exec(
		`INSERT INTO ingest_batch(run_id,platform,source,thread_id,batch_index,comments,thread_meta,status)
		 VALUES(?,?,?,?,?,?,?,'pending')`,
		b.RunID, b.Platform, b.Source, b.ThreadID, b.Index, string(payload), meta)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// MarkIngested records a comment fingerprint in the skip-list.
func (s *Store) MarkIngested(fp string) error {
	_, err := s.db.Exec("INSERT OR IGNORE INTO ingested_comments(fingerprint) VALUES(?)", fp)
	return err
}

// AlreadyIngested reports whether fp is in the skip-list.
func (s *Store) AlreadyIngested(fp string) (bool, error) {
	var n int
	if err := s.db.QueryRow("SELECT count(1) FROM ingested_comments WHERE fingerprint=?", fp).Scan(&n); err != nil {
		return false, err
	}
	return n > 0, nil
}

// PendingBatches returns pending batch rows (used by contract/integration tests).
func (s *Store) PendingBatches() (int, error) {
	var n int
	if err := s.db.QueryRow("SELECT count(1) FROM ingest_batch WHERE status='pending'").Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// Now is a stable clock helper.
func Now() string { return time.Now().UTC().Format(time.RFC3339) }

// TouchSource records that a source was walked, and what it yielded. Called
// for every source the run reaches — including one that returned nothing,
// because "visited and empty" is precisely what must push it to the back of
// the queue next time.
func (s *Store) TouchSource(name string, queued int) error {
	if strings.TrimSpace(name) == "" {
		return nil
	}
	_, err := s.db.Exec(`
INSERT INTO source_state(name, last_fetched_at, last_queued, visits)
VALUES(?, datetime('now'), ?, 1)
ON CONFLICT(name) DO UPDATE SET
  last_fetched_at = datetime('now'),
  last_queued     = excluded.last_queued,
  visits          = source_state.visits + 1`, name, queued)
	return err
}

// SourceOrder returns the configured names sorted least-recently-fetched
// first, so a capped run spreads across ticks instead of re-walking the head
// of sources.yaml. Names never fetched sort first — a newly added source
// should be tried before one that was visited an hour ago.
//
// Ties keep the configured order, which keeps a run reproducible.
func (s *Store) SourceOrder(names []string) ([]string, error) {
	if len(names) < 2 {
		return names, nil
	}
	seen := map[string]string{}
	rows, err := s.db.Query("SELECT name, last_fetched_at FROM source_state")
	if err != nil {
		return nil, fmt.Errorf("source order: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var n, at string
		if err := rows.Scan(&n, &at); err != nil {
			return nil, fmt.Errorf("source order scan: %w", err)
		}
		seen[n] = at
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("source order rows: %w", err)
	}
	order := make([]string, len(names))
	copy(order, names)
	sort.SliceStable(order, func(i, j int) bool {
		ai, oki := seen[order[i]]
		aj, okj := seen[order[j]]
		if oki != okj {
			return !oki // never fetched goes first
		}
		if !oki {
			return false // both unseen: configured order stands
		}
		return ai < aj // older timestamp first
	})
	return order, nil
}

// SourceStates is the whole table, for the console's Sources view.
func (s *Store) SourceStates() (map[string]struct {
	LastFetchedAt string
	LastQueued    int
	Visits        int
}, error) {
	out := map[string]struct {
		LastFetchedAt string
		LastQueued    int
		Visits        int
	}{}
	rows, err := s.db.Query("SELECT name, last_fetched_at, last_queued, visits FROM source_state")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var n, at string
		var q, v int
		if err := rows.Scan(&n, &at, &q, &v); err != nil {
			return nil, err
		}
		out[n] = struct {
			LastFetchedAt string
			LastQueued    int
			Visits        int
		}{at, q, v}
	}
	return out, rows.Err()
}
