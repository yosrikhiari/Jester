package grade

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"jester/internal/siteprofile"
	"jester/internal/store"
)

// pi is an int64 pointer, because a nil price means "no price parsed" and the
// distinction is the price gate's whole point.
func pi(v int64) *int64 { return &v }

// tile builds one record for a portal, defaults all-fine: URL on the profile
// host, a parsed price, two photos, the profile currency, a payload that says
// sale. Cases poke specific holes in copies of this.
func tile(portal, host, currency string, i int) Record {
	return Record{
		Portal:    portal,
		ListingID: fmt.Sprintf("l%d", i),
		URL:       fmt.Sprintf("https://%s/listing/%d", host, i),
		Price:     pi(250000),
		Currency:  currency,
		MediaN:    2,
		Payload:   map[string]any{"deal_type": "sale"},
	}
}

// tiles builds n all-fine records for one portal.
func tiles(portal, host, currency string, n int) []Record {
	out := make([]Record, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, tile(portal, host, currency, i))
	}
	return out
}

// sample grades with the run-grade posture minus the shipped registers (the
// registers have their own tests); every test names its own floors.
func sample(rs []Record, host, currency string, mutate func(o *Options)) Report {
	o := Options{
		URLFloor:   0.95,
		PriceFloor: 0.80,
		MediaFloor: 0.90,
		MinUnique:  0,
		Quorum:     1,
		DupesFail:  false,
	}
	if mutate != nil {
		mutate(&o)
	}
	prof := &siteprofile.Profile{Portal: rs[0].Portal, BaseURL: "https://" + host, Currency: currency}
	return Grade(rs, o, map[string]*siteprofile.Profile{rs[0].Portal: prof})
}

// gateOf finds one gate in a portal's report, or fails the test.
func gateOf(t *testing.T, p Portal, name string) Gate {
	t.Helper()
	for _, g := range p.Gates {
		if g.Name == name {
			return g
		}
	}
	t.Fatalf("portal %s has no gate %s (gates: %+v)", p.Portal, name, p.Gates)
	return Gate{}
}

// share formats a gate's got share the way the trend reads it, so assertions
// never lean on float equality.
func share(g Gate) string { return fmt.Sprintf("%.1f", g.Got*100) }

func portalOf(t *testing.T, rep Report, name string) Portal {
	t.Helper()
	for _, p := range rep.Portals {
		if p.Portal == name {
			return p
		}
	}
	t.Fatalf("report has no portal %s (portals: %d)", name, len(rep.Portals))
	return Portal{}
}

// The floors themselves, exercised at 12 records: url 12/12 (100%), price
// 8/12 (66.7%), media 12/12 (100%). One gate fails, the others pass.
func TestGradeCountsSharesAgainstFloors(t *testing.T) {
	rs := tiles("zoopla", "www.zoopla.co.uk", "GBP", 12)
	for i := range rs {
		if i%3 != 0 { // 8 of 12 priced
			continue
		}
		rs[i].Price = nil
	}
	rep := sample(rs, "www.zoopla.co.uk", "GBP", nil)
	p := portalOf(t, rep, "zoopla")

	if rep.Total != 12 || rep.Unique != 12 || rep.Dupes != 0 {
		t.Errorf("got total %d unique %d dupes %d, want 12/12/0", rep.Total, rep.Unique, rep.Dupes)
	}

	url := gateOf(t, p, "url present")
	if url.Verdict != OK || share(url) != "100.0" {
		t.Errorf("url gate: got %s %.1f%%, want ok 100.0%%", url.Verdict, url.Got*100)
	}
	price := gateOf(t, p, "price parsed")
	if price.Verdict != Fail {
		t.Errorf("price gate: got %s share %s (8/12), want fail", price.Verdict, share(price))
	}
	if price.N != 8 || price.Total != 12 || price.Got >= price.Floor {
		t.Errorf("price gate counts: got n=%d total=%d got=%g floor=%g, want 8/12 below 0.80", price.N, price.Total, price.Got, price.Floor)
	}
	media := gateOf(t, p, "media present")
	if media.Verdict != OK || media.Floor != 0.90 {
		t.Errorf("media gate: got %s floor %g, want ok at 0.90", media.Verdict, media.Floor)
	}
	if p.LowPrice != 0 {
		t.Errorf("low price: got %d, want 0 (250000 is not low)", p.LowPrice)
	}
	if rep.FailureCount() != 1 || !rep.HasFailures() {
		t.Errorf("failures: got %d (HasFailures %v), want 1/true", rep.FailureCount(), rep.HasFailures())
	}
}

// A floor met exactly is met: 11/15 = 73.3% clears nothing interesting, so
// the boundary check uses the url floor: 19/20 = 95.0% == floor 0.95 -> ok.
func TestGradeFloorBoundaryIsMetNotMissed(t *testing.T) {
	rs := tiles("zoopla", "www.zoopla.co.uk", "GBP", 20)
	rs[19].URL = ""
	if rep := sample(rs, "www.zoopla.co.uk", "GBP", nil); gateOf(t, portalOf(t, rep, "zoopla"), "url present").Verdict != OK {
		t.Errorf("19/20 at floor 0.95 should still be ok, got fail")
	}
}

// The knownAbsent register: a declared gap renders none with the register's
// own reason, and never counts as a failure.
func TestGradeDeclaresKnownAbsentGapsAsNone(t *testing.T) {
	o := Options{
		URLFloor:     0.95,
		PriceFloor:   0.80,
		MediaFloor:   0.90,
		MinUnique:    0,
		Quorum:       1,
		DupesFail:    false,
		PortalFloors: nil, // the shipped registers ride by zero value
		KnownAbsent:  nil,
	}

	cases := []struct {
		portal   string
		currency string
		host     string
		gap      string
	}{
		{
			portal: "houni", currency: "TND", host: "houni.tn",
			gap: "price parsed", // houni tiles carry no price at all
		},
		{
			portal: "redfin", currency: "USD", host: "www.redfin.com",
			gap: "media present", // redfin list blocks carry no image
		},
	}

	for _, tc := range cases {
		rs := tiles(tc.portal, tc.host, tc.currency, 12)
		for i := range rs {
			if tc.portal == "houni" {
				rs[i].Price = nil
			} else {
				rs[i].MediaN = 0
			}
		}
		o2 := o
		o2.Quorum = 12 // the sample is real; thins have their own test
		prof := &siteprofile.Profile{Portal: tc.portal, BaseURL: "https://" + tc.host, Currency: tc.currency}
		rep := Grade(rs, o2, map[string]*siteprofile.Profile{tc.portal: prof})
		p := portalOf(t, rep, tc.portal)
		g := gateOf(t, p, tc.gap)
		if g.Verdict != None {
			t.Errorf("%s gate %s: got %s, want none (declared in shipped register)", tc.portal, tc.gap, g.Verdict)
		}
		if g.Note == "" {
			t.Errorf("%s gate %s: note is empty, want the register's reason", tc.portal, tc.gap)
		}
		if rep.FailureCount() != 0 {
			t.Errorf("%s: declared gap counted as a failure (%d)", tc.portal, rep.FailureCount())
		}
	}
}

// The tunisieannonce register covers two fields at once: no media in the list
// table, no deal_type column. The missing-deal_type finding must also stay
// silent, or a declared gap would still fail structurally.
func TestGradeTunisieAnnonceDealTypeDeclared(t *testing.T) {
	rs := tiles("tunisieannonce", "www.tunisieannonce.tn", "TND", 12)
	for i := range rs {
		rs[i].MediaN = 0
		// The table rows carry a real payload (everything else the parser
		// read); the deal_type column is the only thing the register can't
		// get. An EMPTY payload would trip the payload finding, not the
		// register.
		rs[i].Payload = map[string]any{"market": "Tunis"}
	}
	prof := &siteprofile.Profile{Portal: "tunisieannonce", BaseURL: "https://www.tunisieannonce.tn", Currency: "TND"}
	rep := Grade(rs, RunOptions(), map[string]*siteprofile.Profile{"tunisieannonce": prof})
	p := portalOf(t, rep, "tunisieannonce")

	if g := gateOf(t, p, "media present"); g.Verdict != None {
		t.Errorf("media gate: got %s, want none", g.Verdict)
	}
	for _, f := range p.Failings {
		if strings.Contains(f, "deal_type") {
			t.Errorf("declared deal_type gap still failed structurally: %q", f)
		}
	}
	if len(p.Deals) != 0 {
		t.Errorf("deals map: got %v, want empty (the empty kind is deleted)", p.Deals)
	}
	if rep.FailureCount() != 0 {
		t.Errorf("declared-only portal should pass clean, got %d failure(s)", rep.FailureCount())
	}
}

// Mubawab's lowered floors, at the boundary: 7 priced of 10 is exactly the
// 0.70 floor -> ok, not fail. One less, and the same gate fails.
func TestGradeMubawabLoweredFloorBoundary(t *testing.T) {
	prof := &siteprofile.Profile{Portal: "mubawab", BaseURL: "https://www.mubawab.tn", Currency: "TND"}
	profiles := map[string]*siteprofile.Profile{"mubawab": prof}

	build := func(priced, withMedia int) []Record {
		rs := tiles("mubawab", "www.mubawab.tn", "TND", 10)
		for i := range rs {
			if i >= priced {
				rs[i].Price = nil
			}
			if i >= withMedia {
				rs[i].MediaN = 0
			}
		}
		return rs
	}

	rep := Grade(build(7, 8), RunOptions(), profiles)
	p := portalOf(t, rep, "mubawab")
	price := gateOf(t, p, "price parsed")
	if price.Verdict != OK || price.Floor != 0.70 {
		t.Errorf("7/10 at lowered floor: got %s floor %g, want ok at 0.70", price.Verdict, price.Floor)
	}
	if !strings.Contains(price.Note, "Prix a consulter") {
		t.Errorf("lowered floor note: got %q, want the register's reason", price.Note)
	}
	media := gateOf(t, p, "media present")
	if media.Verdict != OK || media.Floor != 0.78 {
		t.Errorf("8/10 media at lowered floor: got %s floor %g, want ok at 0.78", media.Verdict, media.Floor)
	}
	if rep.FailureCount() != 0 {
		t.Errorf("boundary sample should pass, got %d failure(s)", rep.FailureCount())
	}

	rep = Grade(build(6, 8), RunOptions(), profiles)
	if g := gateOf(t, portalOf(t, rep, "mubawab"), "price parsed"); g.Verdict != Fail {
		t.Errorf("6/10 at 0.70: got %s, want fail", g.Verdict)
	}
}

// Below the quorum a portal reports shares but makes no verdict, and the
// note says so in numbers - the alarm channel stays readable when a walk
// came back with three tiles.
func TestGradeThinBelowQuorum(t *testing.T) {
	rep := sample(tiles("zoopla", "www.zoopla.co.uk", "GBP", 3), "www.zoopla.co.uk", "GBP", func(o *Options) { o.Quorum = 10 })
	p := portalOf(t, rep, "zoopla")
	for _, g := range p.Gates {
		if g.Verdict != Thin {
			t.Errorf("gate %s: got %s, want thin (3 records below quorum 10)", g.Name, g.Verdict)
		}
		if !strings.Contains(g.Note, "below quorum 10") {
			t.Errorf("gate %s note: got %q, want the quorum note", g.Name, g.Note)
		}
	}
	if share(gateOf(t, p, "price parsed")) != "100.0" {
		t.Errorf("thin gates still carry their share, got %s", share(gateOf(t, p, "price parsed")))
	}
	if rep.HasFailures() {
		t.Errorf("thin is not fail: report claims %d failure(s)", rep.FailureCount())
	}
	// Exactly at the quorum the gates speak.
	rep = sample(tiles("zoopla", "www.zoopla.co.uk", "GBP", 10), "www.zoopla.co.uk", "GBP", func(o *Options) { o.Quorum = 10 })
	for _, g := range portalOf(t, rep, "zoopla").Gates {
		if g.Verdict == Thin {
			t.Errorf("gate %s: got thin at quorum exactly, want a verdict", g.Name)
		}
	}
	// Quorum off (zero) grades any size.
	rep = sample(tiles("zoopla", "www.zoopla.co.uk", "GBP", 2), "www.zoopla.co.uk", "GBP", func(o *Options) { o.Quorum = 0 })
	if g := gateOf(t, portalOf(t, rep, "zoopla"), "url present"); g.Verdict == Thin {
		t.Errorf("quorum 0 must never thin a portal, got thin")
	}
}

// Structural findings: records that carry a value but a wrong one. One portal,
// five records, one defect each - every finding named, once, with its count.
func TestGradeStructuralFindings(t *testing.T) {
	rs := tiles("zoopla", "www.zoopla.co.uk", "GBP", 5)
	rs[0].URL = "https://evil.example/listing/0"          // off-host URL
	rs[1].Currency = "USD"                                // wrong currency
	rs[2] = tile("zoopla", "www.zoopla.co.uk", "GBP", 2)  // rebuilt clean
	rs[2].Payload = map[string]any{}                      // ...but empty payload
	rs[3].Price = pi(0)                                   // zero price
	rs[4].Price = pi(1 << 41)                             // 2.2e12 absurd price
	rs[4].Payload = map[string]any{"deal_type": "rental"} // priced rental counts separately

	rep := sample(rs, "www.zoopla.co.uk", "GBP", nil)
	p := portalOf(t, rep, "zoopla")
	joined := strings.Join(p.Failings, "\n")

	if !strings.Contains(p.Failings[0], "1 URLs are not on www.zoopla.co.uk") {
		t.Errorf("first finding: got %q, want the off-host URL", p.Failings[0])
	}
	if !strings.Contains(p.Failings[1], `disagree with the profile currency "GBP"`) {
		t.Errorf("second finding: got %q, want the currency disagreement", p.Failings[1])
	}
	if !strings.Contains(p.Failings[2], "1 records carry an empty payload") {
		t.Errorf("third finding: got %q, want the empty payload", p.Failings[2])
	}
	if !strings.Contains(p.Failings[3], "2 prices are zero, negative or absurd") {
		t.Errorf("fourth finding: got %q, want both broken prices counted together", p.Failings[3])
	}
	if !strings.Contains(p.Failings[4], "1 records carry no deal_type") {
		t.Errorf("fifth finding: got %q, want the missing deal_type", p.Failings[4])
	}
	if len(p.Failings) != 5 {
		t.Errorf("findings: got %d (%s), want 5", len(p.Failings), joined)
	}
	if p.SalePriced != 3 || p.RentPriced != 1 {
		t.Errorf("priced kinds: got sale %d rental %d, want 3/1", p.SalePriced, p.RentPriced)
	}
	if p.LowPrice != 0 {
		t.Errorf("low price: got %d, want 0", p.LowPrice)
	}

	// Low price is counted, not failed - lettings beside sales are real data.
	// Rents routinely sit below the sales cutoff's unit, so a cheap monthly
	// figure is the watchdog's number, not a parse error.
	rs = tiles("tayara", "www.tayara.tn", "TND", 3)
	rs[0].Price = pi(900)
	rep = sample(rs, "www.tayara.tn", "TND", nil)
	if p := portalOf(t, rep, "tayara"); p.LowPrice != 1 || rep.FailureCount() != 0 {
		t.Errorf("low price: got count %d failures %d, want 1/0", p.LowPrice, rep.FailureCount())
	}
}

// The profile is the spec: a portal that arrives without one is a finding,
// never a silent skip.
func TestGradeMissingProfileIsAFinding(t *testing.T) {
	rs := tiles("mystery", "mystery.example", "USD", 4)
	// Grade with an empty register of profiles; sample() always supplies
	// one, which would defeat the case.
	rep := Grade(rs, Options{URLFloor: 0.95, PriceFloor: 0.80, MediaFloor: 0.90, Quorum: 1}, nil)
	p := portalOf(t, rep, "mystery")
	if len(p.Failings) != 1 || !strings.Contains(p.Failings[0], "no profile to grade against") {
		t.Errorf("failings: got %v, want exactly the no-profile finding", p.Failings)
	}
	if len(p.Gates) != 0 {
		t.Errorf("gates: got %d, want none without a profile", len(p.Gates))
	}
	if !rep.HasFailures() {
		t.Errorf("no-profile portal must fail the report")
	}
}

// The sample carries its own facts: duplicates between two run pages are
// reported under run rules but do not fail it, while a bulk harvest with the
// same duplicate fails outright. MinUnique is the bulk-only bar.
func TestGradeGlobalPoliciesDifferByPosture(t *testing.T) {
	rs := tiles("zoopla", "www.zoopla.co.uk", "GBP", 12)
	rs = append(rs, rs[0]) // one record emitted twice
	rs = append(rs, rs[1])

	bulk := Grade(rs, DefaultOptions(), map[string]*siteprofile.Profile{
		"zoopla": {Portal: "zoopla", BaseURL: "https://www.zoopla.co.uk", Currency: "GBP"},
	})
	if bulk.Dupes != 2 {
		t.Errorf("dupes: got %d, want 2 (two repeats over base set)", bulk.Dupes)
	}
	if bulk.Unique != 12 {
		t.Errorf("unique: got %d, want 12", bulk.Unique)
	}
	if len(bulk.GlobalFailures) != 2 {
		t.Errorf("global failures: got %v, want the duplicate line and the min-uniques line", bulk.GlobalFailures)
	}
	if !strings.Contains(bulk.GlobalFailures[0], "2 duplicate (portal,listing_id) pairs") {
		t.Errorf("first global failure: got %q", bulk.GlobalFailures[0])
	}
	if !strings.Contains(bulk.GlobalFailures[1], "12 unique records, wanted at least 1000") {
		t.Errorf("second global failure: got %q", bulk.GlobalFailures[1])
	}
	if bulk.MinUnique != 1000 {
		t.Errorf("MinUnique carried: got %d, want 1000", bulk.MinUnique)
	}

	run := Grade(rs, RunOptions(), map[string]*siteprofile.Profile{
		"zoopla": {Portal: "zoopla", BaseURL: "https://www.zoopla.co.uk", Currency: "GBP"},
	})
	if run.Dupes != 2 || len(run.GlobalFailures) != 0 {
		t.Errorf("run posture: got dupes %d global %v, want the counts with no failures", run.Dupes, run.GlobalFailures)
	}
	if run.MinUnique != 0 {
		t.Errorf("run posture MinUnique: got %d, want 0 (off)", run.MinUnique)
	}
}

// Zero records is not a grade and must not fabricate one: no portals, no NaN,
// no failures.
func TestGradeZeroRecordsIsSafe(t *testing.T) {
	rep := Grade(nil, RunOptions(), nil)
	if rep.Total != 0 || rep.Unique != 0 || len(rep.Portals) != 0 {
		t.Errorf("empty grade: got total %d unique %d portals %d, want 0/0/0", rep.Total, rep.Unique, len(rep.Portals))
	}
	if rep.HasFailures() {
		t.Errorf("empty grade with min-unique off must not fail")
	}
	b, err := json.Marshal(rep)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if s := string(b); strings.Contains(s, "NaN") {
		t.Errorf("empty grade marshaled %q; NaN would poison every JSON consumer", s)
	}
}

// The report is the wire format: the failing row's JSON says enough that the
// console can name the gate without re-deriving the math.
func TestReportMarshalShape(t *testing.T) {
	rs := tiles("zoopla", "www.zoopla.co.uk", "GBP", 10)
	for i := range rs {
		if i < 2 { // 8/10 priced = 80% == floor, url all fine
			rs[i].Price = nil
			continue
		}
		if i == 3 {
			rs[i].MediaN = 0 // 9/10 media = 90% == floor
		}
	}
	rep := Grade(rs, RunOptions(), map[string]*siteprofile.Profile{
		"zoopla": {Portal: "zoopla", BaseURL: "https://www.zoopla.co.uk", Currency: "GBP"},
	})
	b, err := json.Marshal(rep)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var shape struct {
		Total   int `json:"total"`
		Unique  int `json:"unique"`
		MinUnique int `json:"min_unique"`
		Portals []struct {
			Portal  string `json:"portal"`
			Records int    `json:"records"`
			Gates   []struct {
				Name    string  `json:"name"`
				Verdict string  `json:"verdict"`
				Got     float64 `json:"got"`
				Floor   float64 `json:"floor"`
				N       int     `json:"n"`
				Total   int     `json:"total"`
				Note    string  `json:"note,omitempty"`
			} `json:"gates"`
			Deals map[string]int `json:"deals"`
		} `json:"portals"`
	}
	if err := json.Unmarshal(b, &shape); err != nil {
		t.Fatalf("unmarshal own shape: %v", err)
	}
	if shape.Total != 10 || shape.Unique != 10 || shape.MinUnique != 0 {
		t.Errorf("top level: got %d/%d/min%d, want 10/10/min0", shape.Total, shape.Unique, shape.MinUnique)
	}
	if len(shape.Portals) != 1 || shape.Portals[0].Portal != "zoopla" || shape.Portals[0].Records != 10 {
		t.Fatalf("portals: got %+v, want zoopla with 10 records", shape.Portals)
	}
	var prices, medias int
	for _, g := range shape.Portals[0].Gates {
		switch g.Name {
		case "price parsed":
			prices++
			if g.Verdict != "ok" || g.Floor != 0.80 || g.N != 8 || g.Total != 10 {
				t.Errorf("price gate json: got %+v, want ok 0.80 8/10", g)
			}
		case "media present":
			medias++
			if g.Verdict != "ok" {
				t.Errorf("media gate json: got %+v, want ok", g)
			}
		}
	}
	if prices != 1 || medias != 1 {
		t.Errorf("gate json: got %d price %d media entries, want one each", prices, medias)
	}
	if shape.Portals[0].Deals["sale"] != 10 {
		t.Errorf("deals json: got %v, want sale 10 (deals counts every record carrying the kind, priced or not)", shape.Portals[0].Deals)
	}

	// And a failing gate wears its name in the wire Verdict.
	for i := range rs {
		rs[i].Price = nil
	}
	rep = Grade(rs, RunOptions(), map[string]*siteprofile.Profile{
		"zoopla": {Portal: "zoopla", BaseURL: "https://www.zoopla.co.uk", Currency: "GBP"},
	})
	b, _ = json.Marshal(rep)
	if !strings.Contains(string(b), `"verdict":"fail"`) {
		t.Errorf("failing grade json must carry the raw %q verdict: %s", "fail", b)
	}
}

// Records flattens the store's listings: the payload text becomes the graded
// payload, media becomes a count, and a payload the store kept but that no
// longer decodes degrades to empty (the grader's own finding says so).
func TestRecordsFlattensListings(t *testing.T) {
	priced := pi(250000)
	ls := []store.Listing{
		{
			Portal:    "zoopla",
			ListingID: "l1",
			URL:       "https://www.zoopla.co.uk/l1",
			Price:     priced,
			Currency:  "GBP",
			Media:     []store.Media{{Position: 1}, {Position: 2}},
			Payload:   `{"deal_type":"sale","market":"rent"}`,
		},
		{Portal: "zoopla", ListingID: "l2", URL: "https://www.zoopla.co.uk/l2", Payload: "not json at all"},
		{Portal: "zoopla", ListingID: "l3", URL: "https://www.zoopla.co.uk/l3", Payload: ``},
	}
	rs := Records(ls)
	if len(rs) != 3 {
		t.Fatalf("records: got %d, want 3", len(rs))
	}
	if rs[0].Price != priced {
		t.Errorf("price: payload passthrough lost the pointer")
	}
	if rs[0].MediaN != 2 {
		t.Errorf("media count: got %d, want 2", rs[0].MediaN)
	}
	if rs[0].Payload["deal_type"] != "sale" {
		t.Errorf("payload: got %v, want the decoded deal_type", rs[0].Payload)
	}
	if len(rs[1].Payload) != 0 {
		t.Errorf("undecodable payload: got %v, want degraded to empty", rs[1].Payload)
	}
	if len(rs[2].Payload) != 0 || rs[2].Price != nil {
		t.Errorf("no payload, no price: got %v / %v, want empty / nil", rs[2].Payload, rs[2].Price)
	}
}
