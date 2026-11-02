package store

// The Python side's signals archive has its own schema and its own file. This
// check is the other half of the cross-language guard: schema_version proves
// the two sides agree about the NUGGETS archive, and it says nothing about the
// collector's, which Go never opens.
//
// The rule it enforces is the one a shared `source_state` name broke: the
// Python streak table is `signals_source_state`, and if a `source_state`
// table exists in that file it must be the Go one (keyed by `name`, holding
// visit counters). A Python-shaped `source_state` in the signals archive is
// the collision, not a legacy detail.

import (
	"database/sql"
	"fmt"
	"os"
	"sort"
	"strings"
)

// SignalsStateTable is the Python side's per-source streak table.
const SignalsStateTable = "signals_source_state"

// signalsStateColumns is that table's shape, exactly. A missing column is a
// reader that will raise; an extra one is a schema the Python side does not
// know it wrote.
var signalsStateColumns = []string{
	"source",
	"consecutive_zero",
	"consecutive_error",
	"last_ok_utc",
	"last_rows",
	"last_error",
	"updated_utc",
}

// tableColumns returns the column names of a table, or nil when it does not
// exist. It reads the schema and nothing else: no DDL, no PRAGMA that writes.
func tableColumns(db *sql.DB, table string) ([]string, error) {
	rows, err := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", table))
	if err != nil {
		return nil, fmt.Errorf("read %s schema: %w", table, err)
	}
	defer rows.Close()
	var columns []string
	for rows.Next() {
		var (
			cid        int
			name, kind string
			notNull    int
			defaultVal any
			pk         int
		)
		if err := rows.Scan(&cid, &name, &kind, &notNull, &defaultVal, &pk); err != nil {
			return nil, fmt.Errorf("read %s schema: %w", table, err)
		}
		columns = append(columns, name)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read %s schema: %w", table, err)
	}
	return columns, nil
}

func contains(list []string, want string) bool {
	for _, got := range list {
		if got == want {
			return true
		}
	}
	return false
}

// CheckSignalsSchema verifies a database created by the Python signals
// collector: the streak table is present with its exact shape, and no
// Python-shaped `source_state` has crept back in beside the Go one.
//
// It opens the file with a plain connection rather than store.Open, because
// Open runs the GO schema -- which would create a `source_state` table in the
// collector's archive and turn a green check into a self-fulfilling one.
func CheckSignalsSchema(path string) error {
	if _, err := os.Stat(path); err != nil {
		return fmt.Errorf("no database at %s: %w", path, err)
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return fmt.Errorf("open sqlite: %w", err)
	}
	defer db.Close()

	have, err := tableColumns(db, SignalsStateTable)
	if err != nil {
		return err
	}
	if have == nil {
		return fmt.Errorf("%s is missing: this is not a signals archive", SignalsStateTable)
	}
	want := append([]string(nil), signalsStateColumns...)
	got := append([]string(nil), have...)
	sort.Strings(want)
	sort.Strings(got)
	if strings.Join(want, ",") != strings.Join(got, ",") {
		return fmt.Errorf("%s has columns [%s]; want [%s]",
			SignalsStateTable, strings.Join(got, ", "), strings.Join(want, ", "))
	}

	legacy, err := tableColumns(db, "source_state")
	if err != nil {
		return err
	}
	if legacy == nil {
		// The collector's file need not carry the Go table at all; when it
		// does not, there is nothing to disagree with.
		return nil
	}
	if !contains(legacy, "name") || contains(legacy, "consecutive_zero") {
		return fmt.Errorf("source_state in this file has columns [%s]; "+
			"that is the Python shape, and the collector's streaks belong in %s",
			strings.Join(legacy, ", "), SignalsStateTable)
	}
	return nil
}
