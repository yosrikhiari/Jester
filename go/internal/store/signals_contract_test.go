package store

// The signals archive is created by Python and never opened by Go, so the
// cross-language guard had nothing to say about it: schema_version covers the
// nuggets archive only. These tests pin the check that closes that gap, and
// the collision that made it necessary -- two tables named `source_state`,
// one in each language, with no column in common.

import (
	"database/sql"
	"path/filepath"
	"strings"
	"testing"
)

// The Python module's DDL, verbatim (jester/signals/health.py).
const pythonSignalsStateDDL = `
CREATE TABLE IF NOT EXISTS signals_source_state (
  source TEXT PRIMARY KEY,
  consecutive_zero INTEGER NOT NULL DEFAULT 0,
  consecutive_error INTEGER NOT NULL DEFAULT 0,
  last_ok_utc TEXT NOT NULL DEFAULT '',
  last_rows INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated_utc TEXT NOT NULL DEFAULT ''
);
`

// What the module wrote before the rename.
const pythonLegacyStateDDL = `
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY,
  consecutive_zero INTEGER NOT NULL DEFAULT 0,
  consecutive_error INTEGER NOT NULL DEFAULT 0,
  last_ok_utc TEXT NOT NULL DEFAULT '',
  last_rows INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated_utc TEXT NOT NULL DEFAULT ''
);
`

func makeSignalsDB(t *testing.T, ddl ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "signals.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()
	for _, stmt := range ddl {
		if _, err := db.Exec(stmt); err != nil {
			t.Fatalf("ddl: %v", err)
		}
	}
	return path
}

func TestSignalsCheckAcceptsAPythonCreatedArchive(t *testing.T) {
	path := makeSignalsDB(t, pythonSignalsStateDDL)

	if err := CheckSignalsSchema(path); err != nil {
		t.Fatalf("a Python-shaped signals archive must pass: %v", err)
	}
}

func TestSignalsCheckAcceptsTheGoTableBesideIt(t *testing.T) {
	// Go-shaped `source_state` alongside the Python streaks: the shape they
	// were meant to have when one file held both components.
	path := makeSignalsDB(t, pythonSignalsStateDDL)
	db, err := Open(path)
	if err != nil {
		t.Fatalf("open go store: %v", err)
	}
	db.Close()

	if err := CheckSignalsSchema(path); err != nil {
		t.Fatalf("the Go table beside the Python one is fine: %v", err)
	}
}

func TestSignalsCheckRejectsAPythonShapedSourceState(t *testing.T) {
	// The collision itself: a Python streak table under the Go name.
	path := makeSignalsDB(t, pythonSignalsStateDDL, pythonLegacyStateDDL)

	err := CheckSignalsSchema(path)

	if err == nil {
		t.Fatal("a Python-shaped source_state must fail the check")
	}
	if !strings.Contains(err.Error(), "source_state") {
		t.Fatalf("the failure must name the table: %v", err)
	}
}

func TestSignalsCheckRejectsAnArchiveWithNoStreakTable(t *testing.T) {
	// A database that exists and holds something else -- schema_version only,
	// say. The streak table is what makes it a signals archive.
	path := makeSignalsDB(t, "CREATE TABLE IF NOT EXISTS meta (key TEXT, value TEXT);")

	err := CheckSignalsSchema(path)

	if err == nil || !strings.Contains(err.Error(), "signals_source_state") {
		t.Fatalf("want a missing-table failure, got %v", err)
	}
}

func TestSignalsCheckRejectsAnAbsentFile(t *testing.T) {
	err := CheckSignalsSchema(filepath.Join(t.TempDir(), "absent.db"))

	if err == nil || !strings.Contains(err.Error(), "no database") {
		t.Fatalf("want a missing-file failure, got %v", err)
	}
}

func TestSignalsCheckRejectsAnIncompleteStreakTable(t *testing.T) {
	path := makeSignalsDB(t, `
CREATE TABLE signals_source_state (
  source TEXT PRIMARY KEY,
  consecutive_zero INTEGER NOT NULL DEFAULT 0
);`)

	err := CheckSignalsSchema(path)

	if err == nil || !strings.Contains(err.Error(), "want") {
		t.Fatalf("want a column-list failure, got %v", err)
	}
}
