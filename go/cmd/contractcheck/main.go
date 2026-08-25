// Command contractcheck opens a SQLite database with the Go store, enforcing
// the cross-language schema_version guard (§15 Checkpoint 1). Point it at a
// database created by `python -m jester` (or any Python open_db call):
//
//	python -c "from jester.store import open_db; open_db('data/jester.db')"
//	go run ./cmd/contractcheck data/jester.db
//
// Exit 0 + "CONTRACT OK" when both sides agree on the schema.
package main

import (
	"fmt"
	"os"

	"jester/internal/store"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: contractcheck <db path>")
		os.Exit(2)
	}
	s, err := store.Open(os.Args[1])
	if err != nil {
		fmt.Println("CONTRACT FAIL:", err)
		os.Exit(1)
	}
	defer s.Close()
	v, err := s.Version()
	if err != nil {
		fmt.Println("CONTRACT FAIL:", err)
		os.Exit(1)
	}
	fmt.Printf("CONTRACT OK: Go and Python agree on schema_version=%d\n", v)
}
