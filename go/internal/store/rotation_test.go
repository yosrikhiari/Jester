package store

import (
	"database/sql"
	"path/filepath"
	"strings"
	"testing"
)

// A run budget spent in sources.yaml order always lands on the same head of
// the list. With -max-posts 2 the scheduler re-walked hn-ask's two exhausted
// threads every thirty minutes and never reached the other fifteen sources —
// it fetched forever and archived nothing. These pin the rotation that fixes
// it.

func testStore(t *testing.T) *Store {
	t.Helper()
	st, err := Open(filepath.Join(t.TempDir(), "j.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	return st
}

func names(t *testing.T, st *Store, in ...string) []string {
	t.Helper()
	got, err := st.SourceOrder(in)
	if err != nil {
		t.Fatalf("SourceOrder: %v", err)
	}
	return got
}

func TestNeverFetchedSourcesComeFirst(t *testing.T) {
	st := testStore(t)
	if err := st.TouchSource("a", 5); err != nil {
		t.Fatalf("touch: %v", err)
	}
	got := names(t, st, "a", "b", "c")
	if got[0] == "a" {
		t.Fatalf("a was just fetched and must not lead: %v", got)
	}
	// b and c have never been fetched; between them the configured order holds.
	if strings.Join(got, ",") != "b,c,a" {
		t.Fatalf("want b,c,a got %v", got)
	}
}

func TestLeastRecentlyFetchedLeads(t *testing.T) {
	st := testStore(t)
	// Backdate a and b so their timestamps genuinely differ; datetime('now')
	// has one-second resolution and three touches in a row can tie.
	for name, offset := range map[string]string{"a": "-3 hours", "b": "-1 hours", "c": "-2 hours"} {
		if _, err := st.db.Exec(
			`INSERT INTO source_state(name, last_fetched_at, last_queued, visits)
			 VALUES(?, datetime('now', ?), 0, 1)`, name, offset); err != nil {
			t.Fatalf("seed %s: %v", name, err)
		}
	}
	got := names(t, st, "a", "b", "c")
	if strings.Join(got, ",") != "a,c,b" {
		t.Fatalf("want oldest-first a,c,b got %v", got)
	}
}

func TestAVisitMovesASourceToTheBack(t *testing.T) {
	st := testStore(t)
	for _, n := range []string{"a", "b", "c"} {
		if _, err := st.db.Exec(
			`INSERT INTO source_state(name, last_fetched_at, last_queued, visits)
			 VALUES(?, datetime('now', '-1 hours'), 0, 1)`, n); err != nil {
			t.Fatalf("seed: %v", err)
		}
	}
	first := names(t, st, "a", "b", "c")[0]
	if err := st.TouchSource(first, 0); err != nil {
		t.Fatalf("touch: %v", err)
	}
	if got := names(t, st, "a", "b", "c"); got[0] == first {
		t.Fatalf("%s was just visited and must not lead again: %v", first, got)
	}
}

// A source that yields nothing must still count as visited, or a dead or
// exhausted source is retried first on every single tick — which is the exact
// loop that produced a scheduler fetching the same two threads forever.
func TestAnEmptyVisitStillCounts(t *testing.T) {
	st := testStore(t)
	if err := st.TouchSource("dry", 0); err != nil {
		t.Fatalf("touch: %v", err)
	}
	got := names(t, st, "dry", "fresh")
	if got[0] != "fresh" {
		t.Fatalf("a visited-but-empty source must yield to an unvisited one: %v", got)
	}
}

func TestTouchRecordsYieldAndCountsVisits(t *testing.T) {
	st := testStore(t)
	for i := 0; i < 3; i++ {
		if err := st.TouchSource("a", i); err != nil {
			t.Fatalf("touch: %v", err)
		}
	}
	states, err := st.SourceStates()
	if err != nil {
		t.Fatalf("states: %v", err)
	}
	if states["a"].Visits != 3 {
		t.Fatalf("want 3 visits, got %d", states["a"].Visits)
	}
	if states["a"].LastQueued != 2 {
		t.Fatalf("want the latest yield (2), got %d", states["a"].LastQueued)
	}
}

func TestOrderIsStableForUnknownSources(t *testing.T) {
	st := testStore(t)
	got := names(t, st, "z", "y", "x")
	if strings.Join(got, ",") != "z,y,x" {
		t.Fatalf("nothing fetched yet, configured order must stand: %v", got)
	}
}

func TestOrderKeepsEverySource(t *testing.T) {
	st := testStore(t)
	if err := st.TouchSource("b", 1); err != nil {
		t.Fatalf("touch: %v", err)
	}
	got := names(t, st, "a", "b", "c", "d")
	if len(got) != 4 {
		t.Fatalf("rotation must not drop a source: %v", got)
	}
}

func TestShortListsAreLeftAlone(t *testing.T) {
	st := testStore(t)
	for _, in := range [][]string{nil, {"only"}} {
		got, err := st.SourceOrder(in)
		if err != nil {
			t.Fatalf("SourceOrder(%v): %v", in, err)
		}
		if len(got) != len(in) {
			t.Fatalf("want %v got %v", in, got)
		}
	}
}

func TestTouchIgnoresABlankName(t *testing.T) {
	st := testStore(t)
	if err := st.TouchSource("   ", 1); err != nil {
		t.Fatalf("a blank name should be a no-op, not an error: %v", err)
	}
	states, err := st.SourceStates()
	if err != nil {
		t.Fatalf("states: %v", err)
	}
	if len(states) != 0 {
		t.Fatalf("blank name must not create a row: %v", states)
	}
}

// last_queued answers "what did the last visit yield", which cannot condemn a
// source: "walked three times, never produced anything" and "walked three
// times, produced plenty on the second" both end on a zero. With 90 configured
// sources the difference is the whole point — one deserves parking, the other
// is merely quiet this round.
func TestTotalQueuedSeparatesDeadSourcesFromQuietOnes(t *testing.T) {
	st := testStore(t)
	for _, y := range []int{0, 0, 0} {
		if err := st.TouchSource("dead", y); err != nil {
			t.Fatalf("touch: %v", err)
		}
	}
	for _, y := range []int{0, 7, 0} {
		if err := st.TouchSource("quiet", y); err != nil {
			t.Fatalf("touch: %v", err)
		}
	}
	states, err := st.SourceStates()
	if err != nil {
		t.Fatalf("states: %v", err)
	}
	if states["dead"].LastQueued != states["quiet"].LastQueued {
		t.Fatalf("premise broken: the two must be indistinguishable through last_queued")
	}
	if states["dead"].TotalQueued != 0 {
		t.Fatalf("dead source has produced nothing ever, got %d", states["dead"].TotalQueued)
	}
	if states["quiet"].TotalQueued != 7 {
		t.Fatalf("want the sum of every visit (7), got %d", states["quiet"].TotalQueued)
	}
}

// CREATE TABLE IF NOT EXISTS never widens a table that already exists, so a
// database written before total_queued existed keeps the old four columns and
// every INSERT naming the new one fails on it. Only the idempotent ALTER list
// saves it, and only a database built WITHOUT the column can prove that.
func TestSourceStateMigratesAnOlderDatabase(t *testing.T) {
	path := filepath.Join(t.TempDir(), "old.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open raw: %v", err)
	}
	if _, err := db.Exec(`
CREATE TABLE source_state (
  name TEXT PRIMARY KEY,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queued INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0
);
INSERT INTO source_state(name, last_queued, visits) VALUES('legacy', 4, 2);`); err != nil {
		t.Fatalf("seed old schema: %v", err)
	}
	db.Close()

	st, err := Open(path)
	if err != nil {
		t.Fatalf("open must migrate, not fail: %v", err)
	}
	defer st.Close()
	if err := st.TouchSource("legacy", 3); err != nil {
		t.Fatalf("touch after migration: %v", err)
	}
	states, err := st.SourceStates()
	if err != nil {
		t.Fatalf("states: %v", err)
	}
	if got := states["legacy"].TotalQueued; got != 3 {
		t.Fatalf("want 3 counted from the first post-migration visit, got %d", got)
	}
	if states["legacy"].Visits != 3 {
		t.Fatalf("migration must preserve the old visit count, got %d", states["legacy"].Visits)
	}
}

// A database that has been working for weeks migrates with total_queued at its
// DEFAULT of 0 on every row. Reading that as "walked nine times, never produced
// anything" would offer to park every source in it. The pre-migration yield is
// genuinely unrecoverable — ingest_batch.source holds a thread URL, not a
// source name, so there is nothing to recover it from — so the guard is that
// the verdict counts only visits that were actually measured.
func TestMigrationDoesNotMakeAWorkingSourceLookDead(t *testing.T) {
	path := filepath.Join(t.TempDir(), "working.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open raw: %v", err)
	}
	if _, err := db.Exec(`
CREATE TABLE source_state (
  name TEXT PRIMARY KEY,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queued INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0
);
INSERT INTO source_state(name, last_queued, visits) VALUES('busy', 0, 9);`); err != nil {
		t.Fatalf("seed working database: %v", err)
	}
	db.Close()

	st, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()
	states, err := st.SourceStates()
	if err != nil {
		t.Fatalf("states: %v", err)
	}
	if states["busy"].Visits != 9 {
		t.Fatalf("the lifetime visit count is real and must survive, got %d", states["busy"].Visits)
	}
	if got := states["busy"].MeasuredVisits; got != 0 {
		t.Fatalf("none of those nine visits recorded a yield, want 0 measured, got %d", got)
	}

	// One real visit, and only that one counts as evidence.
	if err := st.TouchSource("busy", 5); err != nil {
		t.Fatalf("touch: %v", err)
	}
	states, _ = st.SourceStates()
	if states["busy"].MeasuredVisits != 1 || states["busy"].TotalQueued != 5 {
		t.Fatalf("want 1 measured visit worth 5, got %d/%d",
			states["busy"].MeasuredVisits, states["busy"].TotalQueued)
	}
	if states["busy"].Visits != 10 {
		t.Fatalf("lifetime visits keep counting from where they were, got %d", states["busy"].Visits)
	}
}
