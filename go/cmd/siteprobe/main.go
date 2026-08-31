// Command siteprobe classifies the property-portal roster before any adapter
// is written for it.
//
//	go run ./cmd/siteprobe                 # whole roster
//	go run ./cmd/siteprobe -market uk      # one paired market
//	go run ./cmd/siteprobe -delay 3s       # slower
//
// One GET per portal, paced, bounded read. See internal/siteprobe for why that
// is the whole of it.
//
// WHAT THIS ANSWERS AND WHAT IT DOES NOT. One request to a landing page names
// the bot wall, the image CDN, and whether the markup already carries
// structured data — the three facts that decide what an adapter costs. It does
// NOT confirm that listings are reachable at depth, or how pagination works;
// those need a second probe against a real search URL, per portal, once this
// pass has said which portals are worth one.
package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"

	"jester/internal/siteprobe"
)

// portal is one candidate. Market groups the competing portals that serve the
// same properties — the pairing is the unit of work now that the plan is
// cross-portal matching rather than one feed per market.
type portal struct {
	name   string
	market string
	region string
	url    string
}

// The roster. Landing pages deliberately: a search URL differs per portal and
// guessing one wrong reports a dead site that is merely a bad path.
var roster = []portal{
	{"Zillow", "us", "North America", "https://www.zillow.com/"},
	{"Realtor", "us", "North America", "https://www.realtor.com/"},
	{"Redfin", "us", "North America", "https://www.redfin.com/"},
	{"Trulia", "us", "North America", "https://www.trulia.com/"},

	{"Rightmove", "uk", "Europe", "https://www.rightmove.co.uk/"},
	{"Zoopla", "uk", "Europe", "https://www.zoopla.co.uk/"},
	{"Idealista", "es-it-pt", "Europe", "https://www.idealista.com/"},
	{"SeLoger", "fr", "Europe", "https://www.seloger.com/"},
	{"Immobiliare", "it", "Europe", "https://www.immobiliare.it/"},
	{"Funda", "nl", "Europe", "https://www.funda.nl/"},
	{"Immoweb", "be", "Europe", "https://www.immoweb.be/"},
	{"ImmoScout24", "de-at", "Europe", "https://www.immobilienscout24.de/"},

	{"Property Finder", "gulf", "Middle East", "https://www.propertyfinder.ae/"},
	{"Bayut", "gulf", "Middle East", "https://www.bayut.com/"},
	{"Dubizzle", "gulf", "Middle East", "https://www.dubizzle.com/"},

	{"Property24", "za", "Africa", "https://www.property24.com/"},
	{"Private Property", "za", "Africa", "https://www.privateproperty.co.za/"},
	{"Mubawab", "ma", "Africa", "https://www.mubawab.ma/"},

	{"PropertyGuru", "sg", "Asia", "https://www.propertyguru.com.sg/"},
	{"99.co", "sg", "Asia", "https://www.99.co/singapore"},
	{"MagicBricks", "in", "Asia", "https://www.magicbricks.com/"},
	{"99acres", "in", "Asia", "https://www.99acres.com/"},
	{"Housing.com", "in", "Asia", "https://housing.com/"},

	{"Realestate.com.au", "au", "Oceania", "https://www.realestate.com.au/"},
	{"Domain", "au", "Oceania", "https://www.domain.com.au/"},
	{"Trade Me Property", "nz", "Oceania", "https://www.trademe.co.nz/a/property"},
}

func main() {
	delay := flag.Duration("delay", 2*time.Second, "pause between requests")
	timeout := flag.Duration("timeout", 20*time.Second, "per-request timeout")
	market := flag.String("market", "", "probe only this market key (e.g. uk, in, za)")
	flag.Parse()

	targets := roster
	if *market != "" {
		targets = nil
		for _, p := range roster {
			if strings.EqualFold(p.market, *market) {
				targets = append(targets, p)
			}
		}
		if len(targets) == 0 {
			fmt.Fprintf(os.Stderr, "no portal in market %q\n", *market)
			os.Exit(2)
		}
	}

	// Sequential, not the concurrency forumprobe used. These are commercial
	// portals behind bot detection reached from one unproxied residential IP;
	// a burst of parallel requests is exactly the shape that gets noticed, and
	// 26 paced requests still finish in under a minute.
	client := &http.Client{Timeout: *timeout}
	results := make([]siteprobe.Result, 0, len(targets))
	fmt.Printf("probing %d portal(s), %v apart\n\n", len(targets), *delay)

	for i, p := range targets {
		if i > 0 {
			time.Sleep(*delay)
		}
		ctx, cancel := context.WithTimeout(context.Background(), *timeout)
		r := siteprobe.Probe(ctx, client, p.name, p.url)
		cancel()
		results = append(results, r)
		fmt.Printf("  %-18s %-7s %s\n", p.name, r.Tier, oneLine(r))
	}

	report(targets, results)
}

func oneLine(r siteprobe.Result) string {
	var bits []string
	if r.Status > 0 {
		bits = append(bits, fmt.Sprintf("HTTP %d", r.Status))
	}
	if r.Wall != "" {
		bits = append(bits, "wall: "+r.Wall)
	}
	if r.NextJSON {
		bits = append(bits, "embedded JSON")
	}
	if len(r.ImageHosts) > 0 {
		bits = append(bits, "img: "+r.ImageHosts[0])
	}
	if r.Note != "" {
		bits = append(bits, r.Note)
	}
	if r.Err != nil {
		bits = append(bits, "err: "+r.Err.Error())
	}
	bits = append(bits, fmt.Sprintf("%dms", r.Elapsed.Milliseconds()))
	return strings.Join(bits, " · ")
}

func report(targets []portal, results []siteprobe.Result) {
	byTier := map[siteprobe.Tier][]string{}
	for i, r := range results {
		byTier[r.Tier] = append(byTier[r.Tier], targets[i].name)
	}
	fmt.Println("\n── build order (cheapest tier first) ──")
	for _, t := range []siteprobe.Tier{
		siteprobe.TierJSON, siteprobe.TierHTML,
		siteprobe.TierWalled, siteprobe.TierAuth, siteprobe.TierDead,
	} {
		names := byTier[t]
		if len(names) == 0 {
			continue
		}
		sort.Strings(names)
		fmt.Printf("  %-7s %2d  %s\n", t, len(names), strings.Join(names, ", "))
	}

	// The pairing view. Cross-portal matching needs BOTH portals in a market
	// to be reachable, so a market whose cheaper half is walled is not a
	// cheaper market — it is the cost of its harder half.
	fmt.Println("\n── paired markets (both halves must be reachable) ──")
	markets := map[string][]int{}
	var order []string
	for i, p := range targets {
		if _, ok := markets[p.market]; !ok {
			order = append(order, p.market)
		}
		markets[p.market] = append(markets[p.market], i)
	}
	sort.Strings(order)
	for _, m := range order {
		idx := markets[m]
		if len(idx) < 2 {
			continue
		}
		worst, parts := 0, make([]string, 0, len(idx))
		for _, i := range idx {
			parts = append(parts, fmt.Sprintf("%s=%s", targets[i].name, results[i].Tier))
			if c := tierCost(results[i].Tier); c > worst {
				worst = c
			}
		}
		fmt.Printf("  %-9s hardest=%-7s %s\n", m, costName(worst), strings.Join(parts, "  "))
	}

	// The one-session ceiling, restated as a number the operator can act on.
	walled := len(byTier[siteprobe.TierWalled])
	fmt.Printf("\n%d of %d portal(s) need the browser; they share one CloakBrowser session.\n",
		walled, len(results))
	fmt.Println("Landing pages only — pagination and listing depth need a second probe per portal.")
}

func tierCost(t siteprobe.Tier) int {
	switch t {
	case siteprobe.TierJSON:
		return 0
	case siteprobe.TierHTML:
		return 1
	case siteprobe.TierWalled:
		return 2
	case siteprobe.TierAuth:
		return 3
	default:
		return 4
	}
}

func costName(c int) string {
	return [...]string{"json", "html", "walled", "auth", "dead"}[c]
}
