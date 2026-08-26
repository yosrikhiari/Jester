package store

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"jester/internal/reddit"
)

// unmarshalOne decodes the single comment stored in a batch payload.
func unmarshalOne(t *testing.T, raw string) map[string]any {
	t.Helper()
	var rows []map[string]any
	if err := json.Unmarshal([]byte(raw), &rows); err != nil {
		t.Fatalf("payload is not a JSON list: %v", err)
	}
	if len(rows) != 1 {
		t.Fatalf("want 1 comment, got %d", len(rows))
	}
	return rows[0]
}

func TestStoredCommentKeepsTheLegacyContractAndTheDetail(t *testing.T) {
	st, err := Open(filepath.Join(t.TempDir(), "j.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	c := reddit.FetchedComment{
		ID: "t1_a", Body: "secrets are a mess", Score: 12,
		Author: "asimovs-auditor", CreatedAt: "2026-08-26T07:27:21Z",
		Upvotes: reddit.I64(12), Replies: reddit.I64(2),
	}
	if _, err := st.EnqueueBatch(Batch{
		RunID: "r", Platform: "reddit", Source: "s", ThreadID: "t",
		Comments: []Comment{NewComment("fp1", c)},
		Post: &reddit.FetchedPost{
			Title: "Centralized secrets", UpvoteRatio: reddit.F64(0.66),
		},
	}); err != nil {
		t.Fatal(err)
	}

	var payload, meta string
	row := st.db.QueryRow("SELECT comments, thread_meta FROM ingest_batch LIMIT 1")
	if err := row.Scan(&payload, &meta); err != nil {
		t.Fatal(err)
	}

	got := unmarshalOne(t, payload)
	// The ORIGINAL contract: the Python pipeline has always read these three
	// names, and widening the capture must not have moved them.
	if got["body"] != "secrets are a mess" || got["fingerprint"] != "fp1" {
		t.Errorf("legacy keys moved: %+v", got)
	}
	if got["upvotes"] != float64(12) {
		t.Errorf("legacy upvotes key wrong: %v", got["upvotes"])
	}

	detail, ok := got["detail"].(map[string]any)
	if !ok {
		t.Fatalf("detail missing: %+v", got)
	}
	if detail["author"] != "asimovs-auditor" {
		t.Errorf("author lost: %+v", detail)
	}
	// The body is NOT repeated inside detail — a thread can carry 500
	// comments and duplicating each one doubles the queue payload to say the
	// same thing twice.
	if _, repeated := detail["body"]; repeated {
		t.Errorf("body duplicated inside detail: %+v", detail)
	}

	var post map[string]any
	if err := json.Unmarshal([]byte(meta), &post); err != nil {
		t.Fatalf("thread_meta is not JSON: %v", err)
	}
	if post["title"] != "Centralized secrets" {
		t.Errorf("post title lost: %+v", post)
	}
	if post["upvote_ratio"] != 0.66 {
		t.Errorf("upvote ratio lost: %+v", post)
	}
}

// The whole reason detail is NESTED rather than embedded: with the adapter
// struct embedded, encoding/json resolves the two `upvotes` keys by depth and
// the plain int64 wins, flattening "this platform publishes no per-comment
// score" (every Hacker News comment) into "nobody upvoted it".
func TestUnpublishedCountsStayAbsentInTheStoredJSON(t *testing.T) {
	c := reddit.FetchedComment{ID: "hn:1", Body: "spatial reasoning", Score: 0}
	raw, err := json.Marshal([]Comment{NewComment("fp", c)})
	if err != nil {
		t.Fatal(err)
	}
	got := unmarshalOne(t, string(raw))
	detail := got["detail"].(map[string]any)
	for _, key := range []string{"upvotes", "downvotes", "likes", "dislikes"} {
		if _, present := detail[key]; present {
			t.Errorf("%q must be absent when the platform does not publish it: %+v",
				key, detail)
		}
	}
	// …while a published zero IS recorded as zero.
	withZero := NewComment("fp", reddit.FetchedComment{
		ID: "t1_b", Body: "x", Awards: reddit.I64(0),
	})
	raw, _ = json.Marshal([]Comment{withZero})
	detail = unmarshalOne(t, string(raw))["detail"].(map[string]any)
	if v, present := detail["awards"]; !present || v != float64(0) {
		t.Errorf("a published zero must survive: %+v", detail)
	}
}

func TestOpenAddsThreadMetaToAnOlderDatabase(t *testing.T) {
	// A database written before the column existed must be widened in place,
	// not left to fail every INSERT that names it. CREATE TABLE IF NOT EXISTS
	// does not do that, which is why Open carries an ALTER list.
	path := filepath.Join(t.TempDir(), "old.db")
	st, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := st.db.Exec("ALTER TABLE ingest_batch DROP COLUMN thread_meta"); err != nil {
		t.Skipf("this SQLite build cannot DROP COLUMN: %v", err)
	}
	st.Close()

	st2, err := Open(path)
	if err != nil {
		t.Fatalf("reopening an older database must widen it, not fail: %v", err)
	}
	defer st2.Close()
	if _, err := st2.EnqueueBatch(Batch{
		RunID: "r", Platform: "reddit", Source: "s", ThreadID: "t",
		Comments: []Comment{NewComment("fp", reddit.FetchedComment{ID: "a", Body: "b"})},
		Post:     &reddit.FetchedPost{Title: "t"},
	}); err != nil {
		t.Errorf("enqueue after migration: %v", err)
	}
}

// Re-running Open must be a no-op, not an error: it happens on every worker
// invocation, several times an hour under the scheduler.
func TestOpenIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "j.db")
	for i := 0; i < 3; i++ {
		st, err := Open(path)
		if err != nil {
			t.Fatalf("open #%d: %v", i+1, err)
		}
		st.Close()
	}
}
