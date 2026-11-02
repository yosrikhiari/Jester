// Command contractcheck opens a SQLite database with the Go store, enforcing
// the cross-language schema_version guard (§15 Checkpoint 1). Point it at a
// database created by `python -m jester` (or any Python open_db call):
//
//	python -c "from jester.store import open_db; open_db('data/jester.db')"
//	go run ./cmd/contractcheck data/jester.db
//
// Exit 0 + "CONTRACT OK" when both sides agree on the schema.
//
// -signals checks the OTHER archive instead: the collector's, which Python
// creates and Go never opens, so schema_version says nothing about it. It
// verifies the streak table `signals_source_state` and its exact columns, and
// that no Python-shaped `source_state` has been written beside the Go one:
//
//	python -m jester signals export
//	go run ./cmd/contractcheck -signals data/signals-live.db
package main

import (
	"flag"
	"fmt"
	"os"

	"jester/internal/store"
)

func main() {
	signals := flag.Bool("signals", false,
		"check the Python signals archive (signals_source_state) instead of schema_version")
	flag.Usage = func() {
		fmt.Fprintln(os.Stderr, "usage: contractcheck [-signals] <db path>")
	}
	flag.Parse()
	if flag.NArg() != 1 {
		flag.Usage()
		os.Exit(2)
	}
	path := flag.Arg(0)

	if *signals {
		if err := store.CheckSignalsSchema(path); err != nil {
			fmt.Println("CONTRACT FAIL:", err)
			os.Exit(1)
		}
		fmt.Println("CONTRACT OK: the signals archive carries the Python streak " +
			"table and no Python-shaped source_state")
		return
	}

	s, err := store.Open(path)
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
