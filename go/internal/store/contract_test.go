package store

// §37.20 Phase A: prove Go's ingest_batch writer works against a database
// CREATED BY PYTHON. Gated on JESTER_CONTRACT_DB so `go test ./...` stays
// hermetic; run explicitly:
//
//	python -c "from jester.store import open_db; open_db('contract.db')"
//	JESTER_CONTRACT_DB=contract.db go test ./internal/store -run Contract -v

import (
	"os"
	"testing"
)

func contractDB(t *testing.T) *Store {
	t.Helper()
	path := os.Getenv("JESTER_CONTRACT_DB")
	if path == "" {
		t.Skip("JESTER_CONTRACT_DB not set; skipping Python-created-db contract test")
	}
	s, err := Open(path)
	if err != nil {
		t.Fatalf("open python-created db: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func TestContractEnqueueBatchOnPythonCreatedDB(t *testing.T) {
	s := contractDB(t)
	id, err := s.EnqueueBatch(Batch{
		RunID: "go-contract", Platform: "reddit", Source: "src",
		ThreadID: "tid-contract", Index: 0,
		Comments: []Comment{{Body: "hello from go", Fingerprint: "gofp1", Upvotes: 7}},
	})
	if err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	if id <= 0 {
		t.Fatalf("expected positive row id, got %d", id)
	}
	n, err := s.PendingBatches()
	if err != nil {
		t.Fatalf("pending: %v", err)
	}
	if n < 1 {
		t.Fatalf("expected >=1 pending batch, got %d", n)
	}
}

func TestContractSkipListOnPythonCreatedDB(t *testing.T) {
	s := contractDB(t)
	if err := s.MarkIngested("contract-fp"); err != nil {
		t.Fatalf("mark: %v", err)
	}
	seen, err := s.AlreadyIngested("contract-fp")
	if err != nil || !seen {
		t.Fatalf("skip-list round-trip failed: seen=%v err=%v", seen, err)
	}
}
