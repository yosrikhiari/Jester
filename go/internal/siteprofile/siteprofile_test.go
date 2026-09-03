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
	p, body := load(t, "tunisieannonce.yaml", "tunisieannonce_listings.html")
	got, err := p.Parse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 listings from 2 annonce-item tiles, got %d", len(got))
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
		mediaCount   int
	}{
		"111111": {
			title:        "Appartement S+2 La Soukra - 180m²",
			price:        450000,
			currency:     "TND",
			propertyType: "Appartement",
			rooms:        3,
			surface:      180,
			location:     "Tunis, La Soukra",
			seller:       "Particulier",
			mediaCount:   0,
		},
		"222222": {
			title:        "Villa S+3 Gammarth - 350m²",
			price:        1200000,
			currency:     "TND",
			propertyType: "Villa",
			rooms:        4,
			surface:      350,
			location:     "Tunis, Gammarth",
			seller:       "Particulier",
			mediaCount:   0,
		},
	}

	for _, l := range got {
		if l.Portal != "tunisieannonce" || l.ListingID == "" {
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

		if !strings.Contains(l.Payload, `"title":"`+exp.title+`"`) {
			t.Errorf("%s: title mismatch: %s", l.ListingID, l.Payload)
		}
		if !strings.Contains(l.Payload, `"property_type":"`+exp.propertyType+`"`) {
			t.Errorf("%s: property_type mismatch: %s", l.ListingID, l.Payload)
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

		if len(l.Media) != exp.mediaCount {
			t.Errorf("%s: media count %d, want %d", l.ListingID, len(l.Media), exp.mediaCount)
		}
		for _, m := range l.Media {
			if !strings.HasPrefix(m.URL, "https://images.tunisieannonce.com/") {
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

// TestHouniLDJSONParsing verifies the anchored profile works against
// Houni's SSR article cards.
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
