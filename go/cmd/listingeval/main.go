// Command listingeval grades a harvested JSONL file against the profiles that
// produced it, and exits non-zero when a gate fails.
//
// WHY A SEPARATE EVAL. The unit tests measure profiles against captured
// fixtures, and fixtures are curated: every shipped profile scores 100% on
// its own excerpt, so they cannot see the failures that only appear at
// volume - a development promo parsed as a listing, an anchor that stops
// matching on page 3, a price that arrives on two thirds of tiles. Those show
// up in the harvested data or nowhere.
//
// The grading lives in internal/grade and is shared with the worker's per-run
// grades, so a grade printed here and a grade the worker carried into the
// run_outcome ledger are the same math over the same record shape. The flags
// stay this command's knobs; the registers and verdict math are the package's.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"jester/internal/grade"
	"jester/internal/siteprofile"
)

func main() {
	in := flag.String("in", "listings.jsonl", "harvested JSONL")
	configDir := flag.String("config", "../config", "directory holding profiles/")
	minTotal := flag.Int("min-total", 1000, "fail if fewer than this many unique records")
	urlFloor := flag.Float64("url-floor", 0.95, "share of records that must carry a URL")
	priceFloor := flag.Float64("price-floor", 0.80, "share that must carry a parsed price")
	mediaFloor := flag.Float64("media-floor", 0.90, "share that must carry at least one image")
	flag.Parse()

	f, err := os.Open(*in)
	if err != nil {
		fatal("open %s: %v", *in, err)
	}
	defer f.Close()

	// The harvest's line shape is the grading package's record shape.
	var records []grade.Record
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var r grade.Record
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			fatal("line %d: %v", len(records)+1, err)
		}
		records = append(records, r)
	}
	if err := sc.Err(); err != nil {
		fatal("read: %v", err)
	}

	portals := map[string]bool{}
	for _, r := range records {
		portals[r.Portal] = true
	}
	names := make([]string, 0, len(portals))
	for n := range portals {
		names = append(names, n)
	}
	sort.Strings(names)

	// Load every portal the sample carries. A profile that cannot load is a
	// finding like any other - the parser's spec is missing - reported where
	// that portal's section sits, with the underlying open/parse error.
	profiles := map[string]*siteprofile.Profile{}
	broken := map[string]string{}
	for _, portal := range names {
		b, err := os.ReadFile(filepath.Join(*configDir, "profiles", portal+".yaml"))
		if err != nil {
			broken[portal] = fmt.Sprintf("%s: no profile to grade against: %v", portal, err)
			continue
		}
		p, err := siteprofile.Load(b)
		if err != nil {
			broken[portal] = fmt.Sprintf("%s: profile does not load: %v", portal, err)
			continue
		}
		profiles[portal] = p
	}

	opts := grade.DefaultOptions()
	opts.URLFloor = *urlFloor
	opts.PriceFloor = *priceFloor
	opts.MediaFloor = *mediaFloor
	opts.MinUnique = *minTotal
	rep := grade.Grade(records, opts, profiles)
	byName := map[string]grade.Portal{}
	for _, p := range rep.Portals {
		byName[p.Portal] = p
	}

	failures := 0
	fail := func(format string, a ...any) {
		failures++
		fmt.Printf("  FAIL  "+format+"\n", a...)
	}

	fmt.Printf("records: %d   unique (portal,listing_id): %d   duplicates: %d\n\n", rep.Total, rep.Unique, rep.Dupes)
	for _, g := range rep.GlobalFailures {
		fail("%s", g)
	}

	for _, portal := range names {
		if why, bad := broken[portal]; bad {
			fmt.Printf("%s  (%d records)\n", portal, byName[portal].Total)
			fail("%s", why)
			// No blank line, exactly as before: the failure IS the
			// section, and the next portal header follows it.
			continue
		}
		p := byName[portal]
		fmt.Printf("%s  (%d records)\n", portal, p.Total)

		for _, g := range p.Gates {
			switch g.Verdict {
			case grade.None:
				fmt.Printf("  none  %-16s %5.1f%%  (declared: %s)\n", g.Name, g.Got*100, g.Note)
				continue
			case grade.Thin:
				// Unreachable under this command's quorum of one, but
				// the verdict exists in the shared math and prints as
				// what it is rather than as an ok.
			case grade.Fail:
				failures++
			}
			detail := fmt.Sprintf("  %s  %-16s %5.1f%%  (%d/%d, floor %.0f%%)",
				mark(g), g.Name, g.Got*100, g.N, g.Total, g.Floor*100)
			if g.Note != "" {
				detail += " - " + g.Note
			}
			fmt.Println(detail)
		}
		for _, finding := range p.Failings {
			fail("%s", finding)
		}
		kinds := make([]string, 0, len(p.Deals))
		for k := range p.Deals {
			kinds = append(kinds, fmt.Sprintf("%s=%d", k, p.Deals[k]))
		}
		sort.Strings(kinds)
		fmt.Printf("  ok    %-16s %s (priced: %d sale, %d rental)\n", "deal_type", strings.Join(kinds, " "), p.SalePriced, p.RentPriced)
		if p.LowPrice > 0 {
			fmt.Printf("  warn  %d price(s) under 1000 %s - lettings, or a portal that changed\n",
				p.LowPrice, profiles[portal].Currency)
		}
		fmt.Println()
	}

	if failures > 0 {
		fmt.Printf("%d gate(s) failed\n", failures)
		os.Exit(1)
	}
	fmt.Println("all gates passed")
}

// mark names a gate's verdict for its printed line, the two-character-wide
// marks the eval has always used.
func mark(g grade.Gate) string {
	switch g.Verdict {
	case grade.Fail:
		return "FAIL"
	case grade.Thin:
		return "thin"
	default:
		return "ok  "
	}
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "listingeval: "+format+"\n", a...)
	os.Exit(1)
}
