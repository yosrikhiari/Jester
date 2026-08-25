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
	"time"

	_ "modernc.org/sqlite"

	"jester/internal/config"
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
`

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
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("ensure schema: %w", err)
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
	RunID     string
	Platform  string
	Source    string
	ThreadID  string
	Index     int
	Comments  []Comment
}

// Comment is one raw comment with its dedup fingerprint.
type Comment struct {
	Body        string `json:"body"`
	Fingerprint string `json:"fingerprint"`
	Upvotes     int64  `json:"upvotes"`
}

// EnqueueBatch inserts a pending ingest_batch row.
func (s *Store) EnqueueBatch(b Batch) (int64, error) {
	payload, err := json.Marshal(b.Comments)
	if err != nil {
		return 0, err
	}
	res, err := s.db.Exec(
		`INSERT INTO ingest_batch(run_id,platform,source,thread_id,batch_index,comments,status)
		 VALUES(?,?,?,?,?,?,'pending')`,
		b.RunID, b.Platform, b.Source, b.ThreadID, b.Index, string(payload))
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
