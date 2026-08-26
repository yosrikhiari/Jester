package store

import (
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
