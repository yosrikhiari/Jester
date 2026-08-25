package store

import (
	"testing"

	"jester/internal/config"
)

func TestSchemaVersion(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()
	v, err := st.Version()
	if err != nil {
		t.Fatalf("version: %v", err)
	}
	if v != config.SchemaVersion {
		t.Fatalf("version=%d want %d", v, config.SchemaVersion)
	}
}

func TestEnqueueAndDedup(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	id, err := st.EnqueueBatch(Batch{
		RunID:    "r1",
		Platform: "reddit",
		Source:   "s",
		ThreadID: "t1",
		Index:    0,
		Comments: []Comment{{Body: "hello world this is a comment", Fingerprint: "fp1"}},
	})
	if err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	if id == 0 {
		t.Fatalf("expected nonzero id")
	}
	n, err := st.PendingBatches()
	if err != nil {
		t.Fatalf("pending: %v", err)
	}
	if n != 1 {
		t.Fatalf("pending=%d want 1", n)
	}

	if err := st.MarkIngested("fp1"); err != nil {
		t.Fatalf("mark: %v", err)
	}
	dup, err := st.AlreadyIngested("fp1")
	if err != nil {
		t.Fatalf("already: %v", err)
	}
	if !dup {
		t.Fatalf("fp1 should be ingested")
	}
}
