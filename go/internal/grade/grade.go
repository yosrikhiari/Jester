// Package grade is the grading core of the listingeval command, extracted so
// the gates can run themselves.
//
// WHY A SEPARATE EVAL EXISTS AT ALL. The unit tests measure profiles against
// captured fixtures, and fixtures are curated: every shipped profile scores
// 100% on its own excerpt, so they cannot see the failures that only appear at
// volume — a development promo parsed as a listing, an anchor that stops
// matching on page 3, a price that arrives on two thirds of tiles. Those show
// up in harvested data or nowhere. The grading math and the honest registers
// (knownAbsent, the per-portal lowered floors) already exist; what was missing
// was anyone calling them without a human in the loop. The worker now does,
// per run, and carries the verdict in the run_outcome ledger row it already
// writes — so a portal that starts failing its floors alarms from the first
// scheduled visit, not from the next manual harvest.
//
// The grading shapes and verdicts here are the CLI's, moved verbatim: a grade
// produced by the worker and a grade produced by the CLI over the same JSONL
// must agree to the decimal, or one of them is lying.
package grade

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"jester/internal/siteprofile"
	"jester/internal/store"
)

// Record is one listing flattened onto a JSONL line — the exact shape the
// listingharvest command emits and the listingeval CLI graded. Extra keys a
// harvester adds (media, phashes, page, fetched_at) decode harmlessly: grading
// reads only what it can measure.
type Record struct {
	Portal    string         `json:"portal"`
	Market    string         `json:"market"`
	ListingID string         `json:"listing_id"`
	URL       string         `json:"url"`
	Price     *int64         `json:"price"`
	Currency  string         `json:"currency"`
	MediaN    int            `json:"media_count"`
	Payload   map[string]any `json:"payload"`
}

// Verdict is one gate's finding. Four states, because "below the floor" is
// three different facts here: a real regression (fail), a gap the register
// already declared with a reason and a date (none), and a sample too small to
// judge (thin — shares are reported, no verdict is made).
type Verdict string

const (
	// OK reports the share met its floor.
	OK Verdict = "ok"
	// Fail reports the share missed its floor.
	Fail Verdict = "fail"
	// None reports a share below the floor that the knownAbsent register
	// already declared, with the reason — "this has always been red" made
	// checkable, per the register's own comment.
	None Verdict = "none"
	// Thin reports the sample is below the quorum: shares are carried in
	// Got so the trend stays observable, but no verdict is made. A gate
	// that red-alarms off a five-record walk is a gate everyone learns to
	// ignore — the same lesson the register teaches, at the sample side.
	Thin Verdict = "thin"
)

// Gate is one measured property and how the sample scored against its floor.
type Gate struct {
	Name    string  `json:"name"`
	Verdict Verdict `json:"verdict"`
	// Got is the share of records carrying the property, 0..1. Carried even
	// on thin and none so the trend can watch a share recover without
	// pretending a verdict was made.
	Got   float64 `json:"got"`
	Floor float64 `json:"floor"`
	N     int     `json:"n"` // records carrying the property
	Total int     `json:"total"`
	// Note explains a floor that differs from the default, a declared gaps's
	// reason, or a thin sample — so a lowered bar is never a silent one.
	Note string `json:"note,omitempty"`
}

// GateFloor is a per-portal floor override with the reason that justifies it.
type GateFloor struct {
	Floor  float64
	Reason string
}

// Options is how strict a grading is and which honest registers apply.
// Zero-value PortalFloors/KnownAbsent mean "the shipped registers" — an
// explicit map replaces them, it does not merge.
type Options struct {
	URLFloor   float64 // share of records that must carry a URL
	PriceFloor float64 // share that must carry a parsed price
	MediaFloor float64 // share that must carry at least one image
	// MinUnique fails the whole report below this many unique records. Zero
	// turns the check off — a per-run walk visits a couple of pages, and a
	// bulk-volume bar would condemn every routine run.
	MinUnique int
	// Quorum is the smallest sample a portal needs before its gates earn a
	// verdict; below it every gate is thin. One grades anything.
	Quorum int
	// DupesFail counts duplicate (portal,listing_id) pairs as a global
	// failure. An emission that writes the same listing twice is a harvest
	// bug; a WALK may see the same listing across two overlapping pages,
	// which is the portal's pagination, not the worker's fault — run grades
	// report the count without failing it.
	DupesFail bool
	// PortalFloors lowers a floor for a portal whose own inventory explains
	// the gap, with the count that justifies it. NOT knownAbsent: the field
	// IS published, just not on every listing.
	PortalFloors map[string]map[string]GateFloor
	// KnownAbsent records fields a portal does not publish, with the
	// evidence for saying so. Verified 2026-09-04.
	KnownAbsent map[string]map[string]string
}

// DefaultOptions is the listingeval CLI's grading, byte for byte: the flags'
// defaults, the shipped registers, quorum one (any harvest grades), and the
// bulk-disipline failures (min-total, duplicate pairs) on.
func DefaultOptions() Options {
	return Options{
		URLFloor:     0.95,
		PriceFloor:   0.80,
		MediaFloor:   0.90,
		MinUnique:    1000,
		Quorum:       1,
		DupesFail:    true,
		PortalFloors: shippedPortalFloors,
		KnownAbsent:  shippedKnownAbsent,
	}
}

// RunOptions grades one scheduled run's own sample: same floors, same
// registers, but the two bulk-volume failures are off (a routine walk visits
// MaxThreadsPerSource pages at most, so "fewer than 1000 unique" fails every
// run, and overlapping pages legitimately re-see listings) and a quorum stands
// before a gate may speak: too small a sample reports shares, not verdicts.
func RunOptions() Options {
	o := DefaultOptions()
	o.MinUnique = 0
	o.Quorum = 10
	o.DupesFail = false
	return o
}

// shippedKnownAbsent records fields a portal does not publish on its result
// page, with the evidence for saying so. Verified 2026-09-04.
//
// WHY DECLARE THEM. A gate that fails on every run teaches everyone to ignore
// the eval, and the next real regression goes past unread. A gap that is named,
// with the reason and the date, keeps the gate meaningful for every OTHER field
// while staying honest about what the portal actually serves — and it turns
// "this has always been red" into a claim someone can check and retire.
var shippedKnownAbsent = map[string]map[string]string{
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

// shippedPortalFloors lowers a gate for a portal whose own inventory explains
// the gap, with the count that justifies it.
//
// This is NOT knownAbsent: the field IS published, just not on every listing.
// Mubawab shows "Prix a consulter" instead of a figure on about a fifth of its
// tiles - 7 of 33 counted on a live page, 2026-09-04 - and that is the
// portal's data, not a parser fault. The alternative was dropping the GLOBAL
// floor, which would have hidden the same shortfall at every other portal.
var shippedPortalFloors = map[string]map[string]GateFloor{
	"mubawab": {
		"price parsed":  {0.70, `about a fifth of tiles read "Prix a consulter" rather than a figure (7 of 33 on a live page)`},
		"media present": {0.78, "a minority of tiles ship without a photograph"},
	},
}

// Portal is one portal's grading within a report.
type Portal struct {
	Portal string `json:"portal"`
	Total  int    `json:"records"`
	Gates  []Gate `json:"gates"`
	// Failures are the structural findings that are not floor shares: URLs
	// off-host, wrong currency, empty payloads, broken prices, a missing
	// deal_type, no profile to grade against.
	Failings []string `json:"failures,omitempty"`
	// LowPrice counts prices under 1000 — lettings beside sales (real data,
	// counted not failed), or a portal that changed.
	LowPrice int `json:"low_price,omitempty"`
	// The priced counts ride the report: the CLI prints them beside the
	// deal_type kinds, and a trend that can see "sale=0 for a week" is
	// already halfway to knowing why.
	SalePriced int             `json:"sale_priced,omitempty"`
	RentPriced int             `json:"rent_priced,omitempty"`
	Deals      map[string]int `json:"deals,omitempty"`
}

// Report is the grading of one JSONL sample, grouped by portal.
type Report struct {
	Total   int `json:"total"`
	Unique  int `json:"unique"`
	Dupes   int `json:"dupes"`
	MinUnique int `json:"min_unique,omitempty"`
	// GlobalFailures are the sample-wide findings (duplicate emission
	// discipline, the min-unique bar) — the first lines the CLI prints and
	// the ones that fail before any portal is looked at.
	GlobalFailures []string `json:"global_failures,omitempty"`
	Portals        []Portal `json:"portals"`
}

// FailureCount is what the CLI's exit code turns on: every failing gate, every
// structural finding, every global failure — one line each, exactly as the
// CLI printed them.
func (r Report) FailureCount() int {
	n := len(r.GlobalFailures)
	for _, p := range r.Portals {
		n += len(p.Failings)
		for _, g := range p.Gates {
			if g.Verdict == Fail {
				n++
			}
		}
	}
	return n
}

// HasFailures is the alarm test: did this sample fail its gates?
func (r Report) HasFailures() bool { return r.FailureCount() > 0 }

// Grade scores records against the portals' profiles and the options' floors
// and registers. Profiles must be keyed by the portal name the records carry;
// a record from an ungraded portal (no profile) is a finding, not silently
// skipped — the profile is the spec, as the CLI always said.
func Grade(rs []Record, o Options, profiles map[string]*siteprofile.Profile) Report {
	rep := Report{Portals: []Portal{}}
	seen := map[string]int{}
	byPortal := map[string][]Record{}
	for _, r := range rs {
		rep.Total++
		key := r.Portal + "\x1f" + r.ListingID
		seen[key]++
		if seen[key] > 1 {
			rep.Dupes++
		}
		byPortal[r.Portal] = append(byPortal[r.Portal], r)
	}
	rep.Unique = len(seen)
	rep.MinUnique = o.MinUnique
	if o.DupesFail && rep.Dupes > 0 {
		rep.GlobalFailures = append(rep.GlobalFailures,
			fmt.Sprintf("%d duplicate (portal,listing_id) pairs: the harvest emitted the same listing twice", rep.Dupes))
	}
	if o.MinUnique > 0 && rep.Unique < o.MinUnique {
		rep.GlobalFailures = append(rep.GlobalFailures,
			fmt.Sprintf("%d unique records, wanted at least %d", rep.Unique, o.MinUnique))
	}

	names := make([]string, 0, len(byPortal))
	for n := range byPortal {
		names = append(names, n)
	}
	sort.Strings(names)

	floors := shippedPortalFloors
	if o.PortalFloors != nil {
		floors = o.PortalFloors
	}
	absent := shippedKnownAbsent
	if o.KnownAbsent != nil {
		absent = o.KnownAbsent
	}

	for _, portal := range names {
		rs := byPortal[portal]
		n := len(rs)
		p := Portal{Portal: portal, Total: n, Gates: []Gate{}, Deals: map[string]int{}}

		prof, ok := profiles[portal]
		if !ok || prof == nil {
			// The profile is the spec: currency, host and market are
			// declared there, so grading the data against anything else
			// would only prove the grader and the harvester agree with
			// each other.
			p.Failings = append(p.Failings, fmt.Sprintf("%s: no profile to grade against", portal))
			rep.Portals = append(rep.Portals, p)
			continue
		}
		host := strings.TrimPrefix(strings.TrimPrefix(prof.BaseURL, "https://"), "http://")
		host = strings.TrimSuffix(host, "/")

		var withURL, withPrice, withMedia, onHost, badCurrency, emptyPayload int
		var brokenPrice, lowPrice int
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
					// NOT a failure, and the first version of this gate
					// had it wrong. Tayara's real-estate category carries
					// lettings beside sales - "location s+1 aux jardins
					// de Carthage" at 1550 TND is a month's rent, not a
					// broken parse - so a flat floor condemns real data.
					// It is still worth counting: a portal that is
					// supposed to be sales-only and starts reporting
					// these has either changed or broken.
					lowPrice++
				}
			}
			if r.MediaN > 0 {
				withMedia++
			}
			if r.Currency != prof.Currency {
				badCurrency++
			}
			if len(r.Payload) == 0 {
				emptyPayload++
			}
			dt, _ := r.Payload["deal_type"].(string)
			p.Deals[dt]++
			if r.Price != nil {
				switch dt {
				case "rental":
					rentPriced++
				case "sale":
					salePriced++
				}
			}
		}

		gates := []Gate{
			{Name: "url present", Floor: o.URLFloor, Got: float64(withURL) / float64(n), N: withURL},
			{Name: "price parsed", Floor: o.PriceFloor, Got: float64(withPrice) / float64(n), N: withPrice},
			{Name: "media present", Floor: o.MediaFloor, Got: float64(withMedia) / float64(n), N: withMedia},
		}
		for i := range gates {
			if ovr, ok := floors[portal][gates[i].Name]; ok {
				gates[i].Floor = ovr.Floor
				gates[i].Note = ovr.Reason
			}
			g := &gates[i]
			g.Total = n
			switch {
			case o.Quorum > 0 && n < o.Quorum:
				g.Verdict = Thin
				g.Note = fmt.Sprintf("%d record(s) below quorum %d; shares reported, no verdict", n, o.Quorum)
			case g.Got < g.Floor:
				if reason, declared := absent[portal][g.Name]; declared {
					g.Verdict = None
					g.Note = reason
				} else {
					g.Verdict = Fail
				}
			default:
				g.Verdict = OK
			}
		}
		p.Gates = gates
		p.LowPrice = lowPrice

		if withURL > 0 && onHost != withURL {
			p.Failings = append(p.Failings, fmt.Sprintf("%s: %d URLs are not on %s", portal, withURL-onHost, host))
		}
		if badCurrency > 0 {
			p.Failings = append(p.Failings, fmt.Sprintf("%s: %d records disagree with the profile currency %q", portal, badCurrency, prof.Currency))
		}
		if emptyPayload > 0 {
			p.Failings = append(p.Failings, fmt.Sprintf("%s: %d records carry an empty payload", portal, emptyPayload))
		}
		if brokenPrice > 0 {
			p.Failings = append(p.Failings, fmt.Sprintf("%s: %d prices are zero, negative or absurd", portal, brokenPrice))
		}
		// A price is only comparable to another price of the SAME kind.
		// Until deal_type exists on every record, a median over a portal
		// that carries lettings is a median over two different units.
		if missing := p.Deals[""]; missing > 0 {
			if _, declared := absent[portal]["deal_type"]; !declared {
				p.Failings = append(p.Failings, fmt.Sprintf("%s: %d records carry no deal_type", portal, missing))
			}
		}
		// The priced counts ride the report: the CLI prints them beside the
		// deal_type kinds, and a trend that can see "sale=0 for a week" is
		// already halfway to knowing why.
		p.SalePriced = salePriced
		p.RentPriced = rentPriced
		// The empty "" kind (records with no deal_type) is reported by the
		// missing-deal_type finding above; it does not get a deal column.
		delete(p.Deals, "")

		rep.Portals = append(rep.Portals, p)
	}
	return rep
}

// Records flattens a walk's parsed listings into grading records. The store
// keeps the payload as JSON text exactly because callers flatten differently;
// a listing whose payload fails to decode has already passed parse, so the
// payload degrades to empty and the grader's own empty-payload finding says
// so — nothing here invents a field.
func Records(listings []store.Listing) []Record {
	out := make([]Record, 0, len(listings))
	for _, l := range listings {
		var payload map[string]any
		if l.Payload != "" {
			_ = json.Unmarshal([]byte(l.Payload), &payload)
		}
		out = append(out, Record{
			Portal:    l.Portal,
			ListingID: l.ListingID,
			URL:       l.URL,
			Price:     l.Price,
			Currency:  l.Currency,
			MediaN:    len(l.Media),
			Payload:   payload,
		})
	}
	return out
}
