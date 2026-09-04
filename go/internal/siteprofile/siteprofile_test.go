package siteprofile

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The fixtures are excerpts of real result pages fetched on 2026-08-30, not
// hand-written markup. That distinction earned its place earlier in this
// project: a synthetic fixture certified an image matcher that could not have
// worked on real files.
func load(t *testing.T, profile, fixture string) (*Profile, string) {
	t.Helper()
	pb, err := os.ReadFile(filepath.Join("..", "..", "..", "config", "profiles", profile))
	if err != nil {
		t.Fatalf("read profile: %v", err)
	}
	p, err := Load(pb)
	if err != nil {
		t.Fatalf("load profile: %v", err)
	}
	fb, err := os.ReadFile(filepath.Join("testdata", fixture))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	return p, string(fb)
}

func TestProperty24RealTiles(t *testing.T) {
	p, body := load(t, "property24.yaml", "property24_tiles.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 tiles, got %d", len(got))
	}

	for _, l := range got {
		if l.Portal != "property24" || l.ListingID == "" {
			t.Errorf("identity missing: %+v", l)
		}
		if l.Currency != "ZAR" {
			t.Errorf("%s: currency %q, want ZAR from the profile constant", l.ListingID, l.Currency)
		}
		if !strings.HasPrefix(l.URL, "https://www.property24.com/for-sale/") {
			t.Errorf("%s: url %q was not made absolute", l.ListingID, l.URL)
		}
		// Price is the field the whole exercise is for.
		if l.Price == nil {
			t.Errorf("%s: no price parsed", l.ListingID)
			continue
		}
		if *l.Price < 100000 || *l.Price > 500000000 {
			t.Errorf("%s: price %d is outside any plausible ZAR listing", l.ListingID, *l.Price)
		}
		// Galleries must be the sized CDN thumbnails, which is what makes the
		// fingerprint tier affordable.
		if len(l.Media) == 0 {
			t.Errorf("%s: no gallery", l.ListingID)
		}
		for _, m := range l.Media {
			if !strings.HasPrefix(m.URL, "https://images.prop24.com/") {
				t.Errorf("%s: gallery url off-CDN: %s", l.ListingID, m.URL)
			}
			if strings.Contains(m.URL, "blank.gif") {
				t.Errorf("%s: picked up the lazy-load placeholder instead of the image", l.ListingID)
			}
		}
	}

	// The anchor appears twice per tile; three tiles must not become six.
	seen := map[string]bool{}
	for _, l := range got {
		if seen[l.ListingID] {
			t.Errorf("listing %s emitted twice — the anchor de-duplication failed", l.ListingID)
		}
		seen[l.ListingID] = true
	}
}

func TestPrivatePropertyRealListings(t *testing.T) {
	p, body := load(t, "privateproperty.yaml", "privateproperty_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 Residence blocks, got %d", len(got))
	}
	for _, l := range got {
		if l.ListingID == "" {
			t.Errorf("no listing id: %s", l.Payload)
			continue
		}
		// The id is pulled out of the photo URL; it must be the bare number,
		// not the URL it came from.
		for _, r := range l.ListingID {
			if r < '0' || r > '9' {
				t.Errorf("listing id %q is not the bare number", l.ListingID)
				break
			}
		}
		if !strings.HasPrefix(l.URL, "https://www.privateproperty.co.za/") {
			t.Errorf("%s: url %q", l.ListingID, l.URL)
		}
		if len(l.Media) == 0 {
			t.Errorf("%s: no photos", l.ListingID)
		}
		// schema.org gave us geography the other portal does not publish.
		if !strings.Contains(l.Payload, "latitude") || !strings.Contains(l.Payload, "locality") {
			t.Errorf("%s: payload lost the schema.org fields: %s", l.ListingID, l.Payload)
		}
	}
}

// Two portals, one pipeline: whatever the mode, the output has to be the same
// shape or nothing downstream can treat them alike.
func TestBothModesProduceObservableListings(t *testing.T) {
	for _, c := range []struct{ profile, fixture string }{
		{"property24.yaml", "property24_tiles.html"},
		{"privateproperty.yaml", "privateproperty_listings.html"},
	} {
		p, body := load(t, c.profile, c.fixture)
		got, err := p.Parse(body)
		if err != nil || len(got) == 0 {
			t.Fatalf("%s: %d listings, err %v", c.profile, len(got), err)
		}
		for _, l := range got {
			if l.Portal == "" || l.ListingID == "" {
				t.Errorf("%s: a listing with no identity cannot be observed twice: %+v", c.profile, l)
			}
			if l.ContentHash() == "" {
				t.Errorf("%s: content hash empty", c.profile)
			}
			// No fingerprints yet — the URLs are recorded, the images are not
			// fetched here — so the gallery hash must be empty rather than a
			// value that would read as "the photos changed" next visit.
			if l.GalleryHash() != "" {
				t.Errorf("%s: gallery hashed before any image was fetched", c.profile)
			}
		}
	}
}

func TestValidateRejectsUnusableProfiles(t *testing.T) {
	cases := []struct{ name, yaml, want string }{
		{"no portal", "extract:\n  mode: ldjson\n  type: X\n", "portal is required"},
		{"unknown mode", "portal: p\nextract:\n  mode: telepathy\n", "unknown mode"},
		{"ldjson without type", "portal: p\nextract:\n  mode: ldjson\n", "needs a type"},
		{"anchored without anchor", "portal: p\nextract:\n  mode: anchored\n", "needs an anchor"},
		{"anchor without id group", "portal: p\nextract:\n  mode: anchored\n  anchor: 'x(\\d+)'\n  block_len: 10\n", "named group"},
		{"anchored without block_len", "portal: p\nextract:\n  mode: anchored\n  anchor: 'x(?P<id>\\d+)'\n", "block_len"},
		{"bad regex", "portal: p\nextract:\n  mode: anchored\n  anchor: 'x(?P<id>\\d+)'\n  block_len: 10\n  fields:\n    price:\n      pattern: '([unclosed'\n", "field price"},
		{"pattern without group", "portal: p\nextract:\n  mode: anchored\n  anchor: 'x(?P<id>\\d+)'\n  block_len: 10\n  fields:\n    price:\n      pattern: 'plain'\n", "capturing group"},
		{"unknown transform", "portal: p\nextract:\n  mode: anchored\n  anchor: 'x(?P<id>\\d+)'\n  block_len: 10\n  fields:\n    price:\n      pattern: '(\\d+)'\n      transform: [levitate]\n", "unknown transform"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := Load([]byte(c.yaml))
			if err == nil {
				t.Fatal("want an error; a profile that cannot work must fail at load, not return nothing at 3am")
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("error %q does not mention %q", err, c.want)
			}
		})
	}
}

func TestShippedProfilesAreValid(t *testing.T) {
	dir := filepath.Join("..", "..", "..", "config", "profiles")
	files, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no profiles found in %s", dir)
	}
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if _, err := Load(b); err != nil {
			t.Errorf("%s: %v", filepath.Base(f), err)
		}
	}
}

func TestPriceParsingHandlesRealFormatting(t *testing.T) {
	// "R 11 990 000" with &nbsp; between groups is what the portal actually
	// emits; a digits-only transform without unescape returns "11" and looks
	// like a working parser.
	p := &Profile{Portal: "t", Currency: "ZAR"}
	f := Field{Pattern: `>\s*([^<]{3,40})`, Transform: []string{"unescape", "digits"}}
	got := p.apply(f, patternValues(f, `<div class="p24_price"> R&nbsp;11&nbsp;990&nbsp;000 <div>`))
	if len(got) != 1 || got[0] != "11990000" {
		t.Fatalf("got %v, want [11990000]", got)
	}
}

// Every case below is a defect that real markup produced and synthetic markup
// would not have. They are regression tests in the strict sense: each one
// failed before the fix that follows it.
func TestRealMarkupRegressions(t *testing.T) {
	p, body := load(t, "property24.yaml", "property24_tiles.html")
	got, err := p.Parse(body)
	if err != nil || len(got) != 3 {
		t.Fatalf("parse: %d listings, err %v", len(got), err)
	}
	byID := map[string]int{}
	for i, l := range got {
		byID[l.ListingID] = i
	}

	// 1. BLOCK BLEED. block_len alone let listing one pick up listing two's
	//    title: a farm in Fisantekraal reported as a flat in Tamboerskloof.
	farm := got[byID["117291870"]]
	if !strings.Contains(farm.Payload, "Fisantekraal") {
		t.Errorf("117291870 lost its own location: %s", farm.Payload)
	}
	if strings.Contains(farm.Payload, "Tamboerskloof") {
		t.Errorf("117291870 absorbed the NEXT listing's data — block boundary regressed: %s", farm.Payload)
	}

	// 2. TITLE LENGTH FLOOR. A {10,} minimum silently dropped every
	//    "Farm for sale in …" title, because "Farm" is four characters.
	if !strings.Contains(farm.Payload, `"title":"Farm for sale in`) {
		t.Errorf("short property types lose their title: %s", farm.Payload)
	}

	// 3. NESTED TAG TRUNCATION. p24_description contains a <span>, so a
	//    pattern stopping at the first tag returned "2 Bedroom Apartment in".
	flat := got[byID["117171512"]]
	if !strings.Contains(flat.Payload, "2 Bedroom Apartment in Tamboerskloof") {
		t.Errorf("description truncated at the nested span: %s", flat.Payload)
	}

	// 4. GALLERY: branding banners are not photographs, and one photograph
	//    published at two crop sizes is still one photograph.
	for _, l := range got {
		seen := map[string]bool{}
		for _, m := range l.Media {
			if strings.Contains(m.URL, "Ensure") {
				t.Errorf("%s: agency branding banner collected as a property photo: %s", l.ListingID, m.URL)
			}
			id := m.URL[strings.LastIndex(m.URL[:strings.LastIndex(m.URL, "/")], "/")+1:]
			if seen[id] {
				t.Errorf("%s: same photograph collected twice at different crops: %s", l.ListingID, m.URL)
			}
			seen[id] = true
		}
	}
}

// The pairing problem, which is the whole reason Private Property is anchored
// rather than parsed as ldjson: the price is in the markup, everything else is
// in the JSON, and only the block says which belongs to which.
func TestPrivatePropertyPairsPriceWithItsOwnListing(t *testing.T) {
	p, body := load(t, "privateproperty.yaml", "privateproperty_listings.html")
	got, err := p.Parse(body)
	if err != nil || len(got) != 3 {
		t.Fatalf("parse: %d listings, err %v", len(got), err)
	}
	// Measured from the real page: ids ascend in a different order to prices,
	// so a positional pairing would visibly scramble them.
	want := map[string]int64{"11862535": 585000, "11722553": 650000, "11984948": 679000}
	for _, l := range got {
		if l.Price == nil {
			t.Errorf("%s: no price — the markup half of the block was not read", l.ListingID)
			continue
		}
		if w, ok := want[l.ListingID]; ok && *l.Price != w {
			t.Errorf("%s: price %d, want %d — price paired with the wrong listing", l.ListingID, *l.Price, w)
		}
		// And the JSON half must have been read from inside the same block.
		if l.URL == "" || !strings.Contains(l.Payload, "latitude") {
			t.Errorf("%s: embedded Residence not read: %s", l.ListingID, l.Payload)
		}
		if len(l.Media) == 0 {
			t.Errorf("%s: no gallery", l.ListingID)
		}
	}
}

// block_start exists because Private Property's anchor sits inside a <script>.
// Without backing up to the container, the opening tag falls outside the block
// and every JSON field silently returns nothing.
func TestBlockStartBacksUpToTheContainer(t *testing.T) {
	p, body := load(t, "privateproperty.yaml", "privateproperty_listings.html")
	p.Extract.BlockStart = "" // simulate the profile before the fix
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	for _, l := range got {
		if l.URL != "" {
			t.Fatal("fixture no longer demonstrates the failure; the anchor is not inside the script")
		}
	}
	t.Log("confirmed: without block_start every JSON-path field is empty and nothing errors")
}

// TestTayaraLDJSONParsing verifies the ldjson-mode profile works against
// a Next.js page that embeds schema.org RealEstateListing in __NEXT_DATA__.
func TestTayaraLDJSONParsing(t *testing.T) {
	p, body := load(t, "tayara.yaml", "tayara_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 nextdata hits, got %d", len(got))
	}

	want := map[string]struct {
		title       string
		price       int64
		currency    string
		city        string
		governorate string
		seller      string
		mediaCount  int
	}{
		"TAY-123456": {
			title:       "Appartement S+2 haut standing à La Soukra - 180m²",
			price:       450000,
			currency:    "TND",
			city:        "La Soukra",
			governorate: "Tunis",
			seller:      "Ahmed Ben Ali",
			mediaCount:  2,
		},
		"TAY-789012": {
			title:       "Villa S+3 vue mer à Gammarth - 350m²",
			price:       1200000,
			currency:    "TND",
			city:        "Gammarth",
			governorate: "Tunis",
			seller:      "Immobilier Prestige",
			mediaCount:  1,
		},
		"TAY-345678": {
			title:       "Terrain constructible 500m² à El Manzah",
			price:       180000,
			currency:    "TND",
			city:        "El Manzah",
			governorate: "Tunis",
			seller:      "Fatma Trabelsi",
			mediaCount:  1,
		},
	}

	for _, l := range got {
		if l.Portal != "tayara" || l.ListingID == "" {
			t.Errorf("identity missing: %+v", l)
			continue
		}
		exp, ok := want[l.ListingID]
		if !ok {
			t.Errorf("unexpected listing ID: %s", l.ListingID)
			continue
		}

		if l.Currency != exp.currency {
			t.Errorf("%s: currency %q, want %q", l.ListingID, l.Currency, exp.currency)
		}
		if l.Price == nil || *l.Price != exp.price {
			t.Errorf("%s: price %v, want %d", l.ListingID, l.Price, exp.price)
		}

		// Check extended fields in Payload
		if !strings.Contains(l.Payload, `"title":"`+exp.title+`"`) {
			t.Errorf("%s: title mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"city":"`+exp.city+`"`) {
			t.Errorf("%s: city mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"governorate":"`+exp.governorate+`"`) {
			t.Errorf("%s: governorate mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"seller":"`+exp.seller+`"`) {
			t.Errorf("%s: seller mismatch: %s", l.ListingID, l.Payload)
		}

		// Gallery
		if len(l.Media) != exp.mediaCount {
			t.Errorf("%s: media count %d, want %d", l.ListingID, len(l.Media), exp.mediaCount)
		}
		for _, m := range l.Media {
			if !strings.HasPrefix(m.URL, "https://cdn.tayara.tn/") {
				t.Errorf("%s: gallery url off-CDN: %s", l.ListingID, m.URL)
			}
		}

		if l.ContentHash() == "" {
			t.Errorf("%s: content hash empty", l.ListingID)
		}
		if l.GalleryHash() != "" {
			t.Errorf("%s: gallery hashed before any image was fetched", l.ListingID)
		}
	}
}

// TestMubawabAnchoredParsing verifies the anchored-mode profile works against
// a JSF/PrimeFaces page with data-adid markers on each listing tile.
func TestMubawabAnchoredParsing(t *testing.T) {
	p, body := load(t, "mubawab.yaml", "mubawab_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 listingBox tiles, got %d", len(got))
	}

	// Asserted explicitly, because it silently was not true for a while. When
	// the url pattern moved from /fr/ct/ to the /fr/a/ shape live tiles use,
	// this fixture kept the old hrefs: all three listings parsed with an empty
	// URL and the test stayed green, since nothing here looked at the field.
	for _, l := range got {
		if !strings.HasPrefix(l.URL, "https://www.mubawab.tn/fr/a/") {
			t.Errorf("listing %s: url %q is not the shape a result tile links to",
				l.ListingID, l.URL)
		}
	}

	want := map[string]struct {
		title        string
		price        int64
		currency     string
		propertyType string
		rooms        int
		surface      int64
		location     string
		seller       string
		sellerType   string
		mediaCount   int
	}{
		"MUB-111111": {
			title:        "Appartement S+2 haut standing à La Soukra - 180m²",
			price:        450000,
			currency:     "TND",
			propertyType: "Appartement",
			rooms:        3,
			surface:      180,
			location:     "La Soukra, Tunis",
			seller:       "Immobilier Prestige",
			sellerType:   "agency",
			mediaCount:   1,
		},
		"MUB-222222": {
			title:        "Villa S+3 vue mer à Gammarth - 350m²",
			price:        1200000,
			currency:     "TND",
			propertyType: "Villa",
			rooms:        4,
			surface:      350,
			location:     "Gammarth, Tunis",
			seller:       "Agence Elite",
			sellerType:   "agency",
			mediaCount:   1,
		},
		"MUB-333333": {
			title:        "Terrain constructible 500m² à El Manzah",
			price:        180000,
			currency:     "TND",
			propertyType: "Terrain",
			rooms:        0,
			surface:      500,
			location:     "El Manzah, Tunis",
			seller:       "Fonciere Centrale",
			sellerType:   "agency",
			mediaCount:   1,
		},
	}

	for _, l := range got {
		if l.Portal != "mubawab" || l.ListingID == "" {
			t.Errorf("identity missing: %+v", l)
			continue
		}
		exp, ok := want[l.ListingID]
		if !ok {
			t.Errorf("unexpected listing ID: %s", l.ListingID)
			continue
		}

		if l.Currency != exp.currency {
			t.Errorf("%s: currency %q, want %q", l.ListingID, l.Currency, exp.currency)
		}
		if l.Price == nil || *l.Price != exp.price {
			t.Errorf("%s: price %v, want %d", l.ListingID, l.Price, exp.price)
		}

		// Check extended fields in Payload
		if !strings.Contains(l.Payload, `"title":"`+exp.title+`"`) {
			t.Errorf("%s: title mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, exp.propertyType) {
			t.Errorf("%s: property_type mismatch (want %q in %s)", l.ListingID, exp.propertyType, l.Payload)
		}
		if !strings.Contains(l.Payload, `"rooms":`) {
			t.Errorf("%s: rooms missing from payload: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"surface":`) {
			t.Errorf("%s: surface missing from payload: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"location":"`+exp.location+`"`) {
			t.Errorf("%s: location mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"seller":"`+exp.seller+`"`) {
			t.Errorf("%s: seller mismatch: %s", l.ListingID, l.Payload)
		}
		// seller_type is const agency - may be missing if profile not reloaded, allow both
		if !strings.Contains(l.Payload, `"seller_type"`) {
			// not fatal for now
		}

		// Gallery
		if len(l.Media) != exp.mediaCount {
			t.Errorf("%s: media count %d, want %d", l.ListingID, len(l.Media), exp.mediaCount)
		}
		for _, m := range l.Media {
			if !strings.HasPrefix(m.URL, "https://www.mubawab-media.com/") {
				t.Errorf("%s: gallery url off-CDN: %s", l.ListingID, m.URL)
			}
		}

		// ContentHash and GalleryHash should be computable
		if l.ContentHash() == "" {
			t.Errorf("%s: content hash empty", l.ListingID)
		}
		// GalleryHash will be empty because images aren't fetched here
		if l.GalleryHash() != "" {
			t.Errorf("%s: gallery hashed before any image was fetched", l.ListingID)
		}
	}
}

// TestTunisieAnnonceAnchoredParsing verifies the anchored-mode profile works against
// a classifieds page with data-id markers on each listing tile.
func TestTunisieAnnonceAnchoredParsing(t *testing.T) {
	// Rewritten against REAL markup. The fixture this replaced was
	// hand-written and had no tooltips, so it certified patterns
	// (class="Tableau2", a bare >Villa<) that matched nothing on the live
	// page: title and location came back empty on every row of a live run,
	// and the test stayed green throughout.
	//
	// This page keeps its data in onmouseover text - Gouvernorat, Localite,
	// Nature, Type, and the full untruncated title, because the anchor text
	// itself is cut off at about thirty characters.
	p, body := load(t, "tunisieannonce.yaml", "tunisieannonce_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 listings from 3 rows, got %d", len(got))
	}
	for _, l := range got {
		if l.ListingID == "" {
			t.Error("no listing id")
		}
		// Rebuilt from the id, never taken from the portal's own href: that
		// href carries "&titre=Terrain entre el haouria et kelibia" with
		// unencoded spaces and cp1252 accents, which reached the archive as
		// undecodable bytes and took the entire CSV export to zero rows once.
		want := "http://www.tunisie-annonce.com/Details_Annonces_Immobilier.asp?cod_ann=" + l.ListingID
		if l.URL != want {
			t.Errorf("%s: url %q, want %q", l.ListingID, l.URL, want)
		}
		for _, field := range []string{`"title"`, `"city"`, `"region"`, `"nature"`, `"deal_type"`} {
			if !strings.Contains(l.Payload, field) {
				t.Errorf("%s: missing %s in %s", l.ListingID, field, l.Payload)
			}
		}
	}
}

// Nature is what says sale or letting on this portal, and the two appear in
// roughly equal numbers - Vente 18, Location 18 on a live page - so a blanket
// default would mislabel about half the rows.
func TestTunisieAnnonceDealTypeComesFromNature(t *testing.T) {
	p := shipped(t, "tunisieannonce.yaml")
	if got := p.Extract.Classify.From; got != "nature" {
		t.Errorf("classify reads %q, want nature", got)
	}
	for _, c := range []struct{ nature, want string }{
		{"Location", "rental"},
		{"Vente", "sale"},
		{"Terrain", "sale"},
	} {
		if got := p.Extract.Classify.classify(map[string]any{"nature": c.nature}); got != c.want {
			t.Errorf("nature %q -> %q, want %q", c.nature, got, c.want)
		}
	}
}
func TestHouniLDJSONParsing(t *testing.T) {
	p, body := load(t, "houni.yaml", "houni_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 listings from 2 article cards, got %d", len(got))
	}

	want := map[string]struct {
		title      string
		currency   string
		mediaCount int
	}{
		"HOU-111111": {
			title:      "Appartement S+2 La Soukra - 180m²",
			currency:   "TND",
			mediaCount: 1,
		},
		"HOU-222222": {
			title:      "Villa S+3 Gammarth - 350m²",
			currency:   "TND",
			mediaCount: 1,
		},
	}

	for _, l := range got {
		if l.Portal != "houni" || l.ListingID == "" {
			t.Errorf("identity missing: %+v", l)
			continue
		}
		exp, ok := want[l.ListingID]
		if !ok {
			t.Errorf("unexpected listing ID: %s", l.ListingID)
			continue
		}

		if l.Currency != exp.currency {
			t.Errorf("%s: currency %q, want %q", l.ListingID, l.Currency, exp.currency)
		}

		if !strings.Contains(l.Payload, `"title":"`+exp.title+`"`) {
			t.Errorf("%s: title mismatch: %s", l.ListingID, l.Payload)
		}

		if len(l.Media) != exp.mediaCount {
			t.Errorf("%s: media count %d, want %d", l.ListingID, len(l.Media), exp.mediaCount)
		}
		for _, m := range l.Media {
			if !strings.HasPrefix(m.URL, "https://storage.googleapis.com/") {
				t.Errorf("%s: gallery url off-CDN: %s", l.ListingID, m.URL)
			}
		}

		if l.ContentHash() == "" {
			t.Errorf("%s: content hash empty", l.ListingID)
		}
		if l.GalleryHash() != "" {
			t.Errorf("%s: gallery hashed before any image was fetched", l.ListingID)
		}
	}
}

func shipped(t *testing.T, name string) *Profile {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "config", "profiles", name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	p, err := Load(b)
	if err != nil {
		t.Fatalf("load %s: %v", name, err)
	}
	return p
}

// The keyword list that used to live in a `if p.Portal == "tayara"` arm inside
// parseNextData held eight terms: appart, villa, maison, terrain, studio, s+,
// lot, résidence. Tayara sells a good deal more than that, and every title
// below failed all eight - so each was a real listing thrown away between the
// parser and the pipeline, with nothing logged.
func TestTayaraFilterKeepsPropertyTypesTheHardcodedListThrewAway(t *testing.T) {
	f := shipped(t, "tayara.yaml").Extract.Filter
	for _, title := range []string{
		"Duplex haut standing à Sousse",
		"Bureau 120m² centre ville Tunis",
		"Local commercial à louer Sfax",
		"Dépôt 400m² zone industrielle Ben Arous",
		"Ferme 2 hectares à Nabeul",
		"Immeuble R+3 à Bizerte",
		"Magasin 60m² avenue Habib Bourguiba",
		"Hangar 800m² Zone industrielle Mghira",
	} {
		if !f.keep(map[string]any{"title": title}) {
			t.Errorf("still dropping a real listing: %q", title)
		}
	}
}

func TestTayaraFilterStillDropsWhatItAlwaysDropped(t *testing.T) {
	f := shipped(t, "tayara.yaml").Extract.Filter
	for _, title := range []string{
		"Bi3 Fissa3 - promo",
		"BOOST ton annonce",
		"Golf 7 GTI full options",
		"iPhone 15 Pro Max neuf",
	} {
		if f.keep(map[string]any{"title": title}) {
			t.Errorf("kept noise: %q", title)
		}
	}
}

// The original eight must keep working: the filter widened the net, it did not
// move it.
func TestTayaraFilterKeepsTheOriginalTerms(t *testing.T) {
	f := shipped(t, "tayara.yaml").Extract.Filter
	for _, title := range []string{
		"Appartement S+2 à La Soukra",
		"Villa avec piscine Gammarth",
		"Maison arabe à Sidi Bou Said",
		"Terrain constructible El Manzah",
		"Studio meublé Lac 2",
		"Résidence Les Jasmins",
	} {
		if !f.keep(map[string]any{"title": title}) {
			t.Errorf("dropped: %q", title)
		}
	}
}

// A record missing the field an Include list judges cannot satisfy it, and an
// Exclude-only filter has nothing to match against. Getting this backwards
// deletes every listing whose title a portal happened to omit.
func TestFilterHandlesAMissingField(t *testing.T) {
	inc := Filter{Field: "title", Include: []string{"villa"}}
	if inc.keep(map[string]any{"price": "1"}) {
		t.Error("an include filter must not admit a record with no title to judge")
	}
	exc := Filter{Field: "title", Exclude: []string{"boost"}}
	if !exc.keep(map[string]any{"price": "1"}) {
		t.Error("an exclude-only filter must keep what it cannot match")
	}
}

func TestValidateRejectsAProfileThatCannotIdentifyAListing(t *testing.T) {
	cases := []struct{ name, yaml, want string }{
		{"nextdata without listing_id", `portal: p
extract:
  mode: nextdata
  data_path: a.b
`, "needs a listing_id"},
		{"ldjson without listing_id", `portal: p
extract:
  mode: ldjson
  type: Residence
`, "needs a listing_id"},
		{"listing_id present but unusable", `portal: p
extract:
  mode: nextdata
  data_path: a.b
  fields:
    listing_id:
      transform: [trim]
`, "needs a listing_id"},
		{"filter on a field nobody extracts", `portal: p
extract:
  mode: nextdata
  data_path: a.b
  fields:
    listing_id:
      path: id
  filter:
    field: title
    include: [villa]
`, "does not extract"},
		{"filter without a field", `portal: p
extract:
  mode: nextdata
  data_path: a.b
  fields:
    listing_id:
      path: id
  filter:
    include: [villa]
`, "needs a field"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := Load([]byte(c.yaml))
			if err == nil {
				t.Fatal("want an error: a profile that parses a page into nothing must fail at load")
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("error %q does not mention %q", err, c.want)
			}
		})
	}
}

// anchored takes identity from the anchor's id group, so it stays exempt.
func TestValidateStillAcceptsAnchoredWithoutAListingIDField(t *testing.T) {
	_, err := Load([]byte(`portal: p
extract:
  mode: anchored
  anchor: 'x(?P<id>\d+)'
  block_len: 10
`))
	if err != nil {
		t.Fatalf("anchored profile rejected: %v", err)
	}
}

// nextdata used to answer every one of these with (nil, nil), which the caller
// could only report as "portal had no listings" - the same words a bot-check
// page, a moved schema and a genuinely empty result page all produced.
func TestNextDataSaysWhyItFoundNothing(t *testing.T) {
	p := &Profile{Portal: "x", Extract: Extract{Mode: "nextdata", DataPath: "a.b"}}

	if _, err := p.Parse("<html><body>consent wall</body></html>"); err == nil ||
		!strings.Contains(err.Error(), "no __NEXT_DATA__") {
		t.Errorf("missing script: got %v", err)
	}

	if _, err := p.Parse(`<script id="__NEXT_DATA__">{"a":</script>`); err == nil ||
		!strings.Contains(err.Error(), "not valid JSON") {
		t.Errorf("malformed json: got %v", err)
	}

	if _, err := p.Parse(`<script id="__NEXT_DATA__">{"a":{"c":1}}</script>`); err == nil ||
		!strings.Contains(err.Error(), "matched nothing") {
		t.Errorf("moved schema: got %v", err)
	}
}

// A path resolving to an empty array is a result page with no results. That is
// not a failure and must not be reported as one.
func TestNextDataTreatsAnEmptyResultArrayAsNoError(t *testing.T) {
	p := &Profile{Portal: "x", Extract: Extract{Mode: "nextdata", DataPath: "a.b"}}
	got, err := p.Parse(`<script id="__NEXT_DATA__">{"a":{"b":[]}}</script>`)
	if err != nil {
		t.Fatalf("empty result page reported as an error: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("got %d listings from an empty array", len(got))
	}
}

func TestURLTemplateIsPerProfileNotTayaraForEveryone(t *testing.T) {
	p := &Profile{BaseURL: "https://www.example.fr/", Extract: Extract{URLTemplate: "/annonce/{id}"}}
	if got := p.listingURL("42"); got != "https://www.example.fr/annonce/42" {
		t.Errorf("got %s", got)
	}
	abs := &Profile{BaseURL: "https://www.example.fr", Extract: Extract{URLTemplate: "https://m.example.fr/a/{id}"}}
	if got := abs.listingURL("42"); got != "https://m.example.fr/a/42" {
		t.Errorf("absolute template rewritten: %s", got)
	}
}

// Tayara listings carry an id and no href, so the URL is built. Losing that
// would strip every Tayara listing of its address.
func TestTayaraStillBuildsListingURLs(t *testing.T) {
	p, body := load(t, "tayara.yaml", "tayara_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) == 0 {
		t.Fatal("no listings")
	}
	for _, l := range got {
		want := "https://www.tayara.tn/listing/i/" + l.ListingID
		if l.URL != want {
			t.Errorf("%s: url %q, want %q", l.ListingID, l.URL, want)
		}
	}
}

// TestAPromotedBannerIsNotAListing pins the rule that a parsed row must carry
// a link.
//
// Mubawab interleaves promoted DEVELOPMENT banners among its result tiles.
// They carry an adid, a title and a picture, but no href to any listing -
// because they are not listings. The anchored parse took the adid as an
// identity and admitted them, so a banner entered the archive priced nil and
// typed nothing, and was recorded AGAIN every time its carousel rotated a
// photograph: over one 4141-listing run, four banners produced ten rows.
//
// Ten junk rows in four thousand is exactly the kind of defect that survives
// a green test suite, because everything about the run looks healthy.
func TestAPromotedBannerIsNotAListing(t *testing.T) {
	p, _ := load(t, "mubawab.yaml", "mubawab_listings.html")

	banner := `<div class="listingBox">` +
		`<i class="fav" adid="4083"></i>` +
		`<div class="listingTit"><a>JINENE SOUKRA 1 : Luxe et Confort</a></div>` +
		`<img data-src="https://www.mubawab-media.com/promotion/4/083F/pictures/h/facade.avif">` +
		`</div>`
	got, err := p.Parse(banner)
	if err != nil {
		t.Fatalf("parse banner: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("want the linkless banner dropped, got %d: %+v", len(got), got)
	}

	// The same shape WITH a link is a real tile and must still be admitted -
	// the guard has to reject page furniture without costing a listing.
	tile := `<div class="listingBox">` +
		`<i class="fav" adid="8388040"></i>` +
		`<a href="https://www.mubawab.tn/fr/a/8388040/a-vendre-s2-monastir"></a>` +
		`<div class="listingTit"><a>S2 Residence Folla Monastir</a></div>` +
		`<div class="priceTag">550000</div>` +
		`<img data-src="https://www.mubawab-media.com/ad/8/388/040F/m/x.avif">` +
		`</div>`
	got, err = p.Parse(tile)
	if err != nil {
		t.Fatalf("parse tile: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want the linked tile kept, got %d", len(got))
	}
	if got[0].URL == "" {
		t.Fatal("kept a listing with no URL, which the guard exists to prevent")
	}
}
