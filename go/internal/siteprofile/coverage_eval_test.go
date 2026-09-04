package siteprofile

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"jester/internal/store"
)

// fixtureFor pairs each shipped profile with the captured page it is measured
// against.
var fixtureFor = map[string]string{
	"property24.yaml":      "property24_tiles.html",
	"privateproperty.yaml": "privateproperty_listings.html",
	"tayara.yaml":          "tayara_listings.html",
	"mubawab.yaml":         "mubawab_listings.html",
	"tunisieannonce.yaml":  "tunisieannonce_listings.html",
	"houni.yaml":           "houni_listings.html",
	"behya.yaml":          "behya_listings.html",
	"redfin.yaml":         "redfin_listings.html",
	"habitaclia.yaml":     "habitaclia_listings.html",
	"onthemarket.yaml":    "onthemarket_listings.html",
}

// coverage is the share of parsed listings on which a field arrived non-empty.
func coverageOf(t *testing.T, profile, fixture string) (int, map[string]float64) {
	t.Helper()
	p, body := load(t, profile, fixture)
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("%s: parse: %v", profile, err)
	}
	counts := map[string]int{}
	for _, l := range got {
		if l.URL != "" {
			counts["url"]++
		}
		if l.Price != nil {
			counts["price"]++
		}
		if len(l.Media) > 0 {
			counts["media"]++
		}
		var payload map[string]any
		if l.Payload != "" {
			_ = json.Unmarshal([]byte(l.Payload), &payload)
		}
		for k, v := range payload {
			if s, ok := v.(string); ok && s == "" {
				continue
			}
			if v != nil {
				counts["payload."+k]++
			}
		}
	}
	out := map[string]float64{}
	if len(got) > 0 {
		for k, c := range counts {
			out[k] = float64(c) / float64(len(got))
		}
	}
	return len(got), out
}

// TestReportFieldCoverage prints what each profile actually extracts. It never
// fails: it is the instrument the thresholds in TestFieldCoverageFloor are read
// off, and it stays in the suite so those numbers can be re-derived rather than
// re-guessed. Run with -v.
func TestReportFieldCoverage(t *testing.T) {
	names := make([]string, 0, len(fixtureFor))
	for n := range fixtureFor {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, profile := range names {
		n, cov := coverageOf(t, profile, fixtureFor[profile])
		keys := make([]string, 0, len(cov))
		for k := range cov {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var b string
		for _, k := range keys {
			b += fmt.Sprintf("      %-28s %.0f%%\n", k, cov[k]*100)
		}
		t.Logf("\n  %s -> %d listings\n%s", profile, n, b)
	}
}

// TestEveryShippedProfileHasAFixture. A profile with nothing to measure it
// against is a profile whose rot is invisible: it keeps loading, keeps
// parsing, and quietly returns less every time the portal is restyled.
func TestEveryShippedProfileHasAFixture(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "..", "..", "config", "profiles", "*.yaml"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no profiles: %v", err)
	}
	for _, f := range files {
		base := filepath.Base(f)
		fx, ok := fixtureFor[base]
		if !ok {
			t.Errorf("%s has no fixture in fixtureFor: its extraction is unmeasured", base)
			continue
		}
		if _, err := os.Stat(filepath.Join("testdata", fx)); err != nil {
			t.Errorf("%s: fixture %s missing: %v", base, fx, err)
		}
	}
}

// Property24 randomises the ORDER of the class list on its result tiles and
// injects a rotating junk class into it. Both tiles below are real, taken off
// two live pages on 2026-09-04 - note p24_tileContainer first in one and last
// in the other, and the throwaway "MealsVowel" / "DrawlsQuaintly".
//
// This is a regression test with a scar. An anchor written as
// class="p24_tileContainer[^"]*" matched 21 tiles on page 1 and ZERO on
// page 3; the harvest read the empty page as the end of the results and
// stopped, reporting a portal with 25 listings instead of thousands. Anything
// that pins this pattern to the START of the class attribute breaks the same
// way, silently, on whichever page the order happens to flip.
func TestProperty24AnchorSurvivesClassReordering(t *testing.T) {
	re := regexp.MustCompile(shipped(t, "property24.yaml").Extract.Anchor)
	for _, tile := range []string{
		`<div class="p24_tileContainer js_resultTile MealsVowel" data-listing-number="117435541">`,
		`<div class="DrawlsQuaintly js_resultTile p24_tileContainer " data-listing-number="117589313">`,
		`<div class="js_resultTile p24_tileContainer p24_tileHoverWrap" data-listing-number="117588912">`,
	} {
		if !re.MatchString(tile) {
			t.Errorf("anchor missed a real tile: %s", tile)
		}
	}
	// A new-build DEVELOPMENT promo carries the same attribute and is not a
	// listing: it has no price, so it used to arrive as a listing with a
	// missing one.
	dev := `<div class="p24_development p24_pointer js_resultTile" itemscope data-listing-number="5428">`
	if re.MatchString(dev) {
		t.Error("anchor matched a development promo; those are adverts, not listings")
	}
}

// Every profile that declares pagination must express it in a form buildPageURL
// can actually produce, and every profile that does NOT declare it must not
// pretend: an unset page_param with max_pages > 1 used to walk the same URL
// max_pages times.
func TestPaginationIsDeclaredHonestly(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("..", "..", "..", "config", "profiles", "*.yaml"))
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		p, err := Load(b)
		if err != nil {
			t.Fatalf("%s: %v", filepath.Base(f), err)
		}
		if p.List.URL == "" {
			t.Errorf("%s: no list URL", filepath.Base(f))
		}
		if p.List.MaxPages < 1 {
			t.Errorf("%s: max_pages %d", filepath.Base(f), p.List.MaxPages)
		}
	}
}

// Property24 publishes the price two different ways and a pattern that knows
// only one of them loses it WITHOUT FAILING: the text-only pattern still
// matched the microdata form, it just captured the whitespace after the tag.
// Measured over a 1000-listing harvest on 2026-09-04, that put a price on 25
// of 877 tiles - every one of them from pages 1 and 2 - while the parse
// reported no error at all. All three fragments below are real.
func TestProperty24PriceSurvivesBothMarkupForms(t *testing.T) {
	p := shipped(t, "property24.yaml")
	f := p.Extract.Fields["price"]
	for _, tc := range []struct{ name, html, want string }{
		{
			"page 1: bare text",
			`<div class="p24_price">R&nbsp;9&nbsp;950&nbsp;000<div class="p24_type">`,
			"9950000",
		},
		{
			"page 3+: schema.org microdata, empty text node",
			`<div class="p24_price" itemprop="price" content="2450000"> <meta itemprop="priceCurrency" content="ZAR" /></div>`,
			"2450000",
		},
		{
			"deep page: class attribute with a trailing space",
			`<div class="p24_price " itemprop="price" content="1299000"></div>`,
			"1299000",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := p.apply(f, patternValues(f, tc.html))
			if len(got) != 1 || got[0] != tc.want {
				t.Errorf("got %v, want [%s]", got, tc.want)
			}
		})
	}
}

// The same trailing space that hid the price hid the suburb. class="p24_location "
// is what deep pages actually emit.
func TestProperty24LocationSurvivesATrailingSpaceInTheClass(t *testing.T) {
	p := shipped(t, "property24.yaml")
	f := p.Extract.Fields["location"]
	for _, html := range []string{
		`<span class="p24_location">Claremont Upper</span>`,
		`<span class="p24_location ">Claremont Upper</span>`,
	} {
		got := p.apply(f, patternValues(f, html))
		if len(got) != 1 || got[0] != "Claremont Upper" {
			t.Errorf("%s -> %v, want [Claremont Upper]", html, got)
		}
	}
}

// A price pattern must not pick up the search filter's own price widgets,
// which sit outside any listing block but share the class name.
func TestProperty24PriceIgnoresTheSearchFilterWidgets(t *testing.T) {
	p := shipped(t, "property24.yaml")
	f := p.Extract.Fields["price"]
	for _, html := range []string{
		`<div class="p24_advanceSearchDropdownGroup btn-group p24_price P24_option">Min</div>`,
		`<div class="p24_price p24_priceRange">Any</div>`,
	} {
		if got := p.apply(f, patternValues(f, html)); len(got) != 0 {
			t.Errorf("filter widget produced a price: %v from %s", got, html)
		}
	}
}

// A DEEP page, which is where every extraction failure in this profile has
// lived. Page 1 is not representative of Property24: from page 3 the price
// moves into schema.org microdata with an empty text node and the class
// attributes gain a trailing space. Measuring only page-1 excerpts is how a
// price that had silently fallen to 25 tiles in 877 still scored 100%.
func TestProperty24DeepPageKeepsPriceAndLocation(t *testing.T) {
	p, body := load(t, "property24.yaml", "property24_deep_tiles.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 deep tiles, got %d", len(got))
	}
	for _, l := range got {
		if l.Price == nil {
			t.Errorf("%s: no price on a deep tile - the microdata form was missed", l.ListingID)
		} else if *l.Price < 1000 {
			t.Errorf("%s: price %d looks like captured whitespace", l.ListingID, *l.Price)
		}
		if !strings.Contains(l.Payload, `"location"`) {
			t.Errorf("%s: no location - the trailing-space class was missed: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"deal_type":"sale"`) {
			t.Errorf("%s: deal_type not derived: %s", l.ListingID, l.Payload)
		}
	}
}

// Page TWO of Tayara, which proves two things at once: the page parameter
// advances (these hits come from offset 30), and the feed mixes lettings,
// sales and things that are not property at all.
func TestTayaraPageTwoSeparatesLettingsFromSales(t *testing.T) {
	p, body := load(t, "tayara.yaml", "tayara_page2_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	// Six hits in, two of them a spa and a massage service.
	if len(got) != 4 {
		t.Fatalf("want 4 property listings out of 6 hits, got %d", len(got))
	}
	deals := map[string]int{}
	for _, l := range got {
		var payload map[string]any
		if err := json.Unmarshal([]byte(l.Payload), &payload); err != nil {
			t.Fatalf("payload: %v", err)
		}
		dt, _ := payload["deal_type"].(string)
		if dt == "" {
			t.Errorf("%s: no deal_type", l.ListingID)
		}
		deals[dt]++
		// The whole point: a 1550 TND letting and a 285000 TND sale must not
		// look like the same kind of number to anything downstream.
		if dt == "sale" && l.Price != nil && *l.Price < 1000 {
			t.Errorf("%s: priced %d and labelled a sale", l.ListingID, *l.Price)
		}
		if dt == "rental" && l.Price != nil && *l.Price > 100000 {
			t.Errorf("%s: priced %d and labelled a letting", l.ListingID, *l.Price)
		}
	}
	if deals["rental"] != 2 || deals["sale"] != 2 {
		t.Errorf("want 2 lettings and 2 sales, got %v", deals)
	}
}

// classify must not invent a value for a field the profile never extracts, and
// must be rejected at load rather than returning "" for every listing.
func TestValidateRejectsAnUnusableClassify(t *testing.T) {
	cases := []struct{ name, yaml, want string }{
		{"reads a field nobody extracts", `portal: p
extract:
  mode: nextdata
  data_path: a.b
  fields:
    listing_id:
      path: id
  classify:
    field: deal_type
    from: title
    default: sale
`, "does not extract"},
		{"no rules and no default", `portal: p
extract:
  mode: nextdata
  data_path: a.b
  fields:
    listing_id:
      path: id
  classify:
    field: deal_type
    from: listing_id
`, "neither rules nor a default"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := Load([]byte(c.yaml))
			if err == nil {
				t.Fatal("want an error")
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("error %q does not mention %q", err, c.want)
			}
		})
	}
}

// Private Property moved to CSS-module class names, which are build hashes.
// The profile's block_start keyed on the old 'listing-result' class and now
// matches NOTHING, which does not fail loudly: the block simply begins at the
// anchor - which sits inside the Residence script - so the script's opening
// tag falls outside it, ldjsonIn returns nothing, and every listing arrives
// with its id and no other field. Measured on Cape Town page 1: 20 of 20.
func TestPrivatePropertySurvivesCSSModuleClassNames(t *testing.T) {
	p, body := load(t, "privateproperty.yaml", "privateproperty_cssmodule_tiles.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings, got %d", len(got))
	}
	for _, l := range got {
		if l.URL == "" {
			t.Errorf("%s: no URL - the block lost its ld+json", l.ListingID)
		}
		if len(l.Media) == 0 {
			t.Errorf("%s: no media - the block lost its ld+json", l.ListingID)
		}
		if !strings.Contains(l.Payload, `"locality"`) {
			t.Errorf("%s: thin payload: %s", l.ListingID, l.Payload)
		}
	}
}

// Zero is not a price, it is a missing one. Tayara publishes price 0 for
// "contact for price" - a terrain à vendre with no figure, a villa whose
// asking price is only in the title - and nothing on these portals is free.
// Recorded as 0 it enters every average, median and price-history series.
func TestZeroPriceIsRecordedAsAbsentNotAsFree(t *testing.T) {
	p := &Profile{Portal: "t", Currency: "TND"}
	var l store.Listing
	payload := map[string]any{}

	p.assign(&l, payload, "price", []string{"0"})
	if l.Price != nil {
		t.Errorf("price 0 recorded as %d; it means 'not published', not 'free'", *l.Price)
	}
	p.assign(&l, payload, "price", []string{"450000"})
	if l.Price == nil || *l.Price != 450000 {
		t.Errorf("a real price was dropped: %v", l.Price)
	}
}

// A portal publishes one of three URL shapes and `absolute` has to handle all
// three. Tunisie Annonce emits "Details_Annonces_Immobilier.asp?cod_ann=..."
// with no leading slash; prefixing only paths beginning "/" left all 25 of its
// listings carrying a URL that resolved nowhere, which the export flagged as
// off-host.
func TestAbsoluteHandlesEveryURLShapeAPortalPublishes(t *testing.T) {
	p := &Profile{Portal: "t", BaseURL: "http://www.tunisie-annonce.com/"}
	f := Field{Transform: []string{"absolute"}}
	cases := []struct{ in, want string }{
		{"Details_Annonces_Immobilier.asp?cod_ann=3430219",
			"http://www.tunisie-annonce.com/Details_Annonces_Immobilier.asp?cod_ann=3430219"},
		{"/annonce/vente/appartement/x",
			"http://www.tunisie-annonce.com/annonce/vente/appartement/x"},
		// An absolute URL must be left exactly as published: Mubawab's tiles
		// link to https://www.mubawab.tn/fr/a/... in full, and prefixing those
		// would produce a doubled host.
		{"https://www.mubawab.tn/fr/a/8409575/x", "https://www.mubawab.tn/fr/a/8409575/x"},
		{"//cdn.example.test/a.jpg", "https://cdn.example.test/a.jpg"},
	}
	for _, c := range cases {
		got := p.apply(f, []string{c.in})
		if len(got) != 1 || got[0] != c.want {
			t.Errorf("%q -> %v, want [%s]", c.in, got, c.want)
		}
	}
}

// Behya was the only reachable site of the four "later" Tunisian portals in
// the plan: Menzili disallows ClaudeBot by name, ImmoTunisie's domain is parked
// for sale on GoDaddy, and Lyanimmo does not resolve.
//
// Its theme puts the whole record in data- attributes on the tile, which is
// why this profile reads them instead of the markup around them - the lesson
// Property24 taught about presentational hooks, applied before it cost
// anything.
func TestBehyaParsesTheDataAttributeTiles(t *testing.T) {
	p, body := load(t, "behya.yaml", "behya_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 tiles, got %d", len(got))
	}
	for _, l := range got {
		if l.URL == "" || !strings.HasPrefix(l.URL, "https://www.behya.tn/") {
			t.Errorf("%s: bad url %q", l.ListingID, l.URL)
		}
		if l.Price == nil || *l.Price < 1000 {
			t.Errorf("%s: price %v", l.ListingID, l.Price)
		}
		if l.Currency != "TND" {
			t.Errorf("%s: currency %q", l.ListingID, l.Currency)
		}
		for _, want := range []string{`"title"`, `"location"`, `"property_type"`, `"deal_type"`} {
			if !strings.Contains(l.Payload, want) {
				t.Errorf("%s: missing %s in %s", l.ListingID, want, l.Payload)
			}
		}
		if len(l.Media) == 0 {
			t.Errorf("%s: no media", l.ListingID)
		}
	}
}

// The anchor must survive the class list moving, exactly as Property24's had
// to: ad_listing sits in the middle of a long ordered list of theme classes.
func TestBehyaAnchorIsNotPinnedToClassOrder(t *testing.T) {
	re := regexp.MustCompile(shipped(t, "behya.yaml").Extract.Anchor)
	for _, tile := range []string{
		`<div class="item-wrap display-list post-45504 ad_listing type-ad_listing status-publish hentry" data-id="45504">`,
		`<div class="ad_listing post-1 hentry" data-id="1">`,
		`<article class="hentry type-ad_listing ad_listing" data-id="99">`,
	} {
		if !re.MatchString(tile) {
			t.Errorf("anchor missed a real tile: %s", tile)
		}
	}
}

// Redfin is the plan's USA region, reached through priority 3 because
// priorities 1 and 2 cannot be: Zillow disallows /homes/ and permits only
// $-anchored landing pages, and Realtor.com returns 403 for robots.txt itself.
// Redfin publishes a schema.org Product per listing, which is a public
// standard rather than a class name.
func TestRedfinParsesProductOffers(t *testing.T) {
	p, body := load(t, "redfin.yaml", "redfin_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings, got %d", len(got))
	}
	for _, l := range got {
		// The id exists ONLY inside the URL, so it is pulled out of it. An id
		// that moved with the address would not be an id.
		if l.ListingID == "" || strings.ContainsAny(l.ListingID, "/:") {
			t.Errorf("bad listing_id %q", l.ListingID)
		}
		if l.Price == nil || *l.Price < 1000 {
			t.Errorf("%s: price %v", l.ListingID, l.Price)
		}
		if l.Currency != "USD" {
			t.Errorf("%s: currency %q", l.ListingID, l.Currency)
		}
		if !strings.Contains(l.Payload, `"street"`) {
			t.Errorf("%s: no street parsed from the address: %s", l.ListingID, l.Payload)
		}
	}
}

// Every profile declares a market and no page ever states one, so it has to be
// stamped in at parse time. It was not: the value reached the JSONL export
// (the harvester read it off the profile in memory) but never the archive, so
// the database-driven CSV had an empty market column on all 995 rows - the
// column that says whether a price is dinars, rand or dollars.
func TestEveryParseModeStampsTheMarket(t *testing.T) {
	for _, c := range []struct{ profile, fixture, want string }{
		{"tayara.yaml", "tayara_listings.html", "tn"},              // nextdata
		{"privateproperty.yaml", "privateproperty_listings.html", "za"}, // anchored + ldjson
		{"redfin.yaml", "redfin_listings.html", "us"},              // ldjson
		{"behya.yaml", "behya_listings.html", "tn"},                // anchored
	} {
		t.Run(c.profile, func(t *testing.T) {
			p, body := load(t, c.profile, c.fixture)
			got, err := p.Parse(body)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			if len(got) == 0 {
				t.Fatal("no listings")
			}
			want := `"market":"` + c.want + `"`
			for _, l := range got {
				if !strings.Contains(l.Payload, want) {
					t.Errorf("%s: payload has no %s: %s", l.ListingID, want, l.Payload)
				}
			}
		})
	}
}

// Spain, reached through the plan's priority 2 because Idealista (priority 1)
// answers 403. Habitaclia states the record outright on the tile rather than
// burying it in prose, which is what makes it cheap to parse.
func TestHabitacliaParsesTheTileAttributes(t *testing.T) {
	p, body := load(t, "habitaclia.yaml", "habitaclia_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings, got %d", len(got))
	}
	for _, l := range got {
		if l.Currency != "EUR" {
			t.Errorf("%s: currency %q", l.ListingID, l.Currency)
		}
		// "1.295.000 &#x20AC;" - dots as thousands separators and an entity
		// for the euro. A price that survives both is a price.
		if l.Price == nil || *l.Price < 10000 {
			t.Errorf("%s: price %v", l.ListingID, l.Price)
		}
		for _, want := range []string{`"title"`, `"city"`, `"property_type"`,
			`"bedrooms"`, `"surface"`, `"deal_type"`} {
			if !strings.Contains(l.Payload, want) {
				t.Errorf("%s: missing %s", l.ListingID, want)
			}
		}
		if len(l.Media) == 0 {
			t.Errorf("%s: no media", l.ListingID)
		}
	}
}

// A tile runs about 6.1 KB to the next anchor. At block_len 4000 the price,
// bedroom and surface attributes fell outside the block and came back empty
// on every row while title and property type parsed fine - a partial parse
// that looks like a working one.
func TestHabitacliaBlockIsLongEnoughForTheWholeTile(t *testing.T) {
	if got := shipped(t, "habitaclia.yaml").Extract.BlockLen; got < 6200 {
		t.Errorf("block_len %d is shorter than a tile (~6.1 KB)", got)
	}
}

// The UK, reached through priority 2 because Rightmove gives Disallow: / to
// GPTBot and CCbot by name. This is the FIRST profile that needs the browser:
// a plain GET returns a Next.js shell with an empty props.pageProps and no
// listings, and the fixture is therefore the rendered DOM.
func TestOnTheMarketParsesRenderedCards(t *testing.T) {
	p, body := load(t, "onthemarket.yaml", "onthemarket_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) < 2 {
		t.Fatalf("want at least 2 cards, got %d", len(got))
	}
	for _, l := range got {
		if l.Currency != "GBP" {
			t.Errorf("%s: currency %q", l.ListingID, l.Currency)
		}
		if !strings.HasPrefix(l.URL, "https://www.onthemarket.com/details/") {
			t.Errorf("%s: url %q", l.ListingID, l.URL)
		}
		if !strings.Contains(l.Payload, `"deal_type"`) {
			t.Errorf("%s: no deal_type", l.ListingID)
		}
	}
	// The price is a literal pound sign in the pattern, not an escape: Go's
	// RE2 has no \uXXXX, and a pattern that fails to compile returns no values
	// rather than an error - it would have looked like a portal with no prices.
	priced := 0
	for _, l := range got {
		if l.Price != nil && *l.Price > 1000 {
			priced++
		}
	}
	if priced == 0 {
		t.Error("no card yielded a price")
	}
}

// A card runs well past 3000 characters. At that length the block ended before
// the price and address, and city came back on 0 of 32 cards while title and
// url parsed fine - a partial parse that reads like a working one.
func TestOnTheMarketBlockCoversTheWholeCard(t *testing.T) {
	if got := shipped(t, "onthemarket.yaml").Extract.BlockLen; got < 9000 {
		t.Errorf("block_len %d is too short for a card (measured: 3000 gives 0/32 cities, 9000 gives 28/32)", got)
	}
}
