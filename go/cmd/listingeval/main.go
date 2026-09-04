// Command listingeval grades a harvested JSONL file against the profiles that
// produced it, and exits non-zero when a gate fails.
//
// WHY A SEPARATE EVAL. The unit tests measure profiles against captured
// fixtures, and fixtures are curated: every shipped profile scores 100% on
// its own excerpt, so they cannot see the failures that only appear at
// volume - a development promo parsed as a listing, an anchor that stops
// matching on page 3, a price that arrives on two thirds of tiles. Those show
// up in the harvested data or nowhere.
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

	"jester/internal/siteprofile"
)

type record struct {
	Portal    string         `json:"portal"`
	Market    string         `json:"market"`
	ListingID string         `json:"listing_id"`
	URL       string         `json:"url"`
	Price     *int64         `json:"price"`
	Currency  string         `json:"currency"`
	MediaN    int            `json:"media_count"`
	Payload   map[string]any `json:"payload"`
}

// knownAbsent records fields a portal does not publish on its result page,
// with the evidence for saying so. Verified 2026-09-04.
//
// WHY DECLARE THEM. A gate that fails on every run teaches everyone to ignore
// the eval, and the next real regression goes past unread. A gap that is named,
// with the reason and the date, keeps the gate meaningful for every OTHER field
// while staying honest about what the portal actually serves - and it turns
// "this has always been red" into a claim someone can check and retire.
var knownAbsent = map[string]map[string]string{
	"houni": {
		"price parsed": "a Houni result tile carries no price at all - no TND/DT " +
			"token, no Prix label, no ld+json, no numeric text node anywhere in " +
			"the block. The figure is on the listing page, in a schema.org Offer, " +
			"and the profile now reads it there: run with -detail to fill this " +
			"field (measured 18 of 18). Absent without it, by design",
	},
	"redfin": {
		"media present": "Redfin's schema.org Product blocks carry an Offer and " +
			"an address but no image; the photographs are on the listing page",
	},
	"tunisieannonce": {
		"media present": "the result table is text only; it links to photos on the " +
			"detail page but shows none in the list",
		"deal_type": "sales and lettings share one table and the Vente/Location " +
			"text on the page is category navigation, not a per-row field",
	},
}

// gate is one measured property and the floor it must clear.
type gate struct {
	name  string
	floor float64 // share of records, 0..1
	got   float64
	n     int
	// note explains a floor that differs from the default, so a lowered bar is
	// never a silently lowered one.
	note string
}

// portalFloors lowers a gate for a portal whose own inventory explains the
// gap, with the count that justifies it.
//
// This is NOT knownAbsent: the field IS published, just not on every listing.
// Mubawab shows "Prix a consulter" instead of a figure on about a fifth of its
// tiles - 7 of 33 counted on a live page, 2026-09-04 - and that is the
// portal's data, not a parser fault. The alternative was dropping the GLOBAL
// floor, which would have hidden the same shortfall at every other portal.
var portalFloors = map[string]map[string]struct {
	floor  float64
	reason string
}{
	"mubawab": {
		"price parsed": {0.70, `about a fifth of tiles read "Prix a consulter" rather than a figure (7 of 33 on a live page)`},
		"media present": {0.78, "a minority of tiles ship without a photograph"},
	},
}

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

	byPortal := map[string][]record{}
	seen := map[string]int{}
	dupes := 0
	total := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var r record
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			fatal("line %d: %v", total+1, err)
		}
		total++
		key := r.Portal + "\x1f" + r.ListingID
		seen[key]++
		if seen[key] > 1 {
			dupes++
		}
		byPortal[r.Portal] = append(byPortal[r.Portal], r)
	}
	if err := sc.Err(); err != nil {
		fatal("read: %v", err)
	}

	failures := 0
	fail := func(format string, a ...any) {
		failures++
		fmt.Printf("  FAIL  "+format+"\n", a...)
	}

	fmt.Printf("records: %d   unique (portal,listing_id): %d   duplicates: %d\n\n", total, len(seen), dupes)
	if dupes > 0 {
		fail("%d duplicate (portal,listing_id) pairs: the harvest emitted the same listing twice", dupes)
	}
	if len(seen) < *minTotal {
		fail("%d unique records, wanted at least %d", len(seen), *minTotal)
	}

	names := make([]string, 0, len(byPortal))
	for n := range byPortal {
		names = append(names, n)
	}
	sort.Strings(names)

	for _, portal := range names {
		rs := byPortal[portal]
		n := float64(len(rs))
		fmt.Printf("%s  (%d records)\n", portal, len(rs))

		// The profile is the spec: currency, host and market are declared
		// there, so grading the data against anything else would only prove
		// the eval and the harvester agree with each other.
		b, err := os.ReadFile(filepath.Join(*configDir, "profiles", portal+".yaml"))
		if err != nil {
			fail("%s: no profile to grade against: %v", portal, err)
			continue
		}
		p, err := siteprofile.Load(b)
		if err != nil {
			fail("%s: profile does not load: %v", portal, err)
			continue
		}
		host := strings.TrimPrefix(strings.TrimPrefix(p.BaseURL, "https://"), "http://")
		host = strings.TrimSuffix(host, "/")

		var withURL, withPrice, withMedia, onHost, badCurrency, emptyPayload int
		var brokenPrice, lowPrice int
		deals := map[string]int{}
		var rentPriced, salePriced int
		for _, r := range rs {
			if r.URL != "" {
				withURL++
				if strings.Contains(r.URL, host) {
					onHost++
				}
			}
			if r.Price != nil {
				withPrice++
				switch {
				case *r.Price <= 0 || *r.Price > 1e12:
					brokenPrice++
				case *r.Price < 1000:
					// NOT a failure, and the first version of this gate had it
					// wrong. Tayara's real-estate category carries lettings
					// beside sales - "location s+1 aux jardins de Carthage" at
					// 1550 TND is a month's rent, not a broken parse - so a
					// flat floor condemns real data. It is still worth
					// counting: a portal that is supposed to be sales-only and
					// starts reporting these has either changed or broken.
					lowPrice++
				}
			}
			if r.MediaN > 0 {
				withMedia++
			}
			if r.Currency != p.Currency {
				badCurrency++
			}
			if len(r.Payload) == 0 {
				emptyPayload++
			}
			dt, _ := r.Payload["deal_type"].(string)
			deals[dt]++
			if r.Price != nil {
				switch dt {
				case "rental":
					rentPriced++
				case "sale":
					salePriced++
				}
			}
		}

		gates := []gate{
			{name: "url present", floor: *urlFloor, got: float64(withURL) / n, n: withURL},
			{name: "price parsed", floor: *priceFloor, got: float64(withPrice) / n, n: withPrice},
			{name: "media present", floor: *mediaFloor, got: float64(withMedia) / n, n: withMedia},
		}
		for i := range gates {
			if o, ok := portalFloors[portal][gates[i].name]; ok {
				gates[i].floor = o.floor
				gates[i].note = o.reason
			}
		}
		for _, g := range gates {
			mark := "ok  "
			if g.got < g.floor {
				if reason, declared := knownAbsent[portal][g.name]; declared {
					fmt.Printf("  none  %-16s %5.1f%%  (declared: %s)\n",
						g.name, g.got*100, reason)
					continue
				}
				mark = "FAIL"
				failures++
			}
			line := fmt.Sprintf("  %s  %-16s %5.1f%%  (%d/%d, floor %.0f%%)",
				mark, g.name, g.got*100, g.n, len(rs), g.floor*100)
			if g.note != "" {
				line += " - " + g.note
			}
			fmt.Println(line)
		}
		if withURL > 0 && onHost != withURL {
			fail("%s: %d URLs are not on %s", portal, withURL-onHost, host)
		}
		if badCurrency > 0 {
			fail("%s: %d records disagree with the profile currency %q", portal, badCurrency, p.Currency)
		}
		if emptyPayload > 0 {
			fail("%s: %d records carry an empty payload", portal, emptyPayload)
		}
		if brokenPrice > 0 {
			fail("%s: %d prices are zero, negative or absurd", portal, brokenPrice)
		}
		// A price is only comparable to another price of the SAME kind. Until
		// deal_type exists on every record, a median over a portal that
		// carries lettings is a median over two different units.
		if missing := deals[""]; missing > 0 {
			if _, declared := knownAbsent[portal]["deal_type"]; !declared {
				fail("%s: %d records carry no deal_type", portal, missing)
			}
		}
		kinds := make([]string, 0, len(deals))
		for k := range deals {
			if k != "" {
				kinds = append(kinds, fmt.Sprintf("%s=%d", k, deals[k]))
			}
		}
		sort.Strings(kinds)
		fmt.Printf("  ok    %-16s %s (priced: %d sale, %d rental)\n", "deal_type", strings.Join(kinds, " "), salePriced, rentPriced)
		if lowPrice > 0 {
			fmt.Printf("  warn  %d price(s) under 1000 %s - lettings, or a portal that changed\n",
				lowPrice, p.Currency)
		}
		fmt.Println()
	}

	if failures > 0 {
		fmt.Printf("%d gate(s) failed\n", failures)
		os.Exit(1)
	}
	fmt.Println("all gates passed")
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "listingeval: "+format+"\n", a...)
	os.Exit(1)
}
