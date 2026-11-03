package store

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

// The run_outcome ledger is the drift-telemetry plane: one row per visited
// source per run, written for clean walks and failures alike, so a portal
// that quietly stops yielding shows up as a trend rather than as silence.
func TestRunOutcomeLedger(t *testing.T) {
	st := testStore(t)

	row := RunOutcome{
		RunID:      "r-1",
		Source:     "zoopla",
		Platform:   "realestate",
		Kind:       "listing",
		Items:      30,
		Fresh:      28,
		Dup:        2,
		PagesOK:    2,
		PagesErr:   1,
		Bytes:      120000,
		LatencyMS:  3400,
		FetchMode:  "browser",
		StatsJSON:  `{"pages":3,"pages_ok":2,"pages_err":1,"parse":{"anchors":28,"kept":28,"fields":{"price":28}}}`,
		FirstErr:   "",
	}
	if err := st.RecordRunOutcome(row); err != nil {
		t.Fatalf("record: %v", err)
	}

	empty := RunOutcome{RunID: "r-1", Source: "hn-ask", Platform: "hackernews", Kind: "feed", FirstErr: "no adapter that can 500"}
	if err := st.RecordRunOutcome(empty); err != nil {
		t.Fatalf("record empty: %v", err)
	}

	// ObservedAt comes from Now() when the caller leaves it empty, and the
	// row must round-trip exactly as recorded.
	var at, statsJSON, firstErr string
	var items, fresh, dup, pagesOK, pagesErr int
	var latencyMS int64
	var bytes int
	var fetchMode, platform, kind string
	err := st.db.QueryRow(
		`SELECT platform, kind, observed_at, items, fresh, dup, pages_ok, pages_err,
		        bytes, latency_ms, fetch_mode, stats_json, first_err
		 FROM run_outcome WHERE run_id='r-1' AND source='zoopla'`).
		Scan(&platform, &kind, &at, &items, &fresh, &dup, &pagesOK, &pagesErr, &bytes, &latencyMS, &fetchMode, &statsJSON, &firstErr)
	if err != nil {
		t.Fatalf("select zoopla: %v", err)
	}
	if platform != "realestate" || kind != "listing" || fetchMode != "browser" {
		t.Errorf("identity columns: %+v", row)
	}
	if items != 30 || fresh != 28 || dup != 2 || pagesOK != 2 || pagesErr != 1 || bytes != 120000 || latencyMS != 3400 {
		t.Errorf("counters: %+v", row)
	}
	if at == "" {
		t.Error("observed_at must be stamped when the caller leaves it empty")
	}
	var stats map[string]any
	if err := json.Unmarshal([]byte(statsJSON), &stats); err != nil {
		t.Fatalf("stats_json is not JSON: %v", err)
	}
	if stats["pages"].(float64) != 3 || stats["pages_ok"].(float64) != 2 {
		t.Errorf("stats round-trip: %v", stats)
	}

	err = st.db.QueryRow(
		`SELECT first_err FROM run_outcome WHERE run_id='r-1' AND source='hn-ask'`).Scan(&firstErr)
	if err != nil {
		t.Fatalf("select hn-ask: %v", err)
	}
	if firstErr == "" {
		t.Error("a failed visit must keep its first error")
	}
	var emptyStats any
	err = st.db.QueryRow(
		`SELECT stats_json FROM run_outcome WHERE run_id='r-1' AND source='hn-ask'`).Scan(&emptyStats)
	if err != nil {
		t.Fatalf("select hn-ask stats: %v", err)
	}
	if emptyStats != nil {
		t.Errorf("community row should carry NULL stats, got %v", emptyStats)
	}

	// A re-walked source replaces its row: one row per source.
	rowDup := row
	rowDup.Items = 31
	rowDup.Dup = 1
	if err := st.RecordRunOutcome(rowDup); err != nil {
		t.Fatalf("re-record: %v", err)
	}
	var count int
	if err := st.db.QueryRow(`SELECT count(1) FROM run_outcome WHERE run_id='r-1'`).Scan(&count); err != nil {
		t.Fatalf("count: %v", err)
	}
	if count != 2 {
		t.Fatalf("want one row per source, got %d", count)
	}
	if err := st.db.QueryRow(
		`SELECT items, dup FROM run_outcome WHERE run_id='r-1' AND source='zoopla'`).Scan(&items, &dup); err != nil {
		t.Fatalf("reselect: %v", err)
	}
	if items != 31 || dup != 1 {
		t.Errorf("replace did not take: items=%d dup=%d", items, dup)
	}

	// The version guard is untouched: adding a table was never a version bump.
	if v, err := st.Version(); err != nil || v != 1 {
		t.Errorf("schema_version=%d err=%v, want 1/nil", v, err)
	}
}

// A database written before the ledger existed gains the table silently on
// the next Go open, and anything already in it survives — the same upgrade
// path every Go-owned table here has taken.
func TestRunOutcomeMigrationReAddsTheTable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "j.db")
	st, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if _, err := st.db.Exec("DROP TABLE run_outcome"); err != nil {
		t.Fatalf("drop: %v", err)
	}
	st.Close()

	st2, err := Open(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer st2.Close()
	if err := st2.RecordRunOutcome(RunOutcome{RunID: "r-2", Source: "tayara", Items: 1}); err != nil {
		t.Fatalf("record after re-add: %v", err)
	}
	var n int
	if err := st2.db.QueryRow(`SELECT count(1) FROM run_outcome WHERE run_id='r-2'`).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != 1 {
		t.Fatalf("want the row back, got %d", n)
	}
}
