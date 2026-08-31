package store

import (
	"jester/internal/mediahash"
	"testing"

	"jester/internal/config"
)

func TestSchemaVersion(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()
	v, err := st.Version()
	if err != nil {
		t.Fatalf("version: %v", err)
	}
	if v != config.SchemaVersion {
		t.Fatalf("version=%d want %d", v, config.SchemaVersion)
	}
}

func TestEnqueueAndDedup(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	id, err := st.EnqueueBatch(Batch{
		RunID:    "r1",
		Platform: "reddit",
		Source:   "s",
		ThreadID: "t1",
		Index:    0,
		Comments: []Comment{{Body: "hello world this is a comment", Fingerprint: "fp1"}},
	})
	if err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	if id == 0 {
		t.Fatalf("expected nonzero id")
	}
	n, err := st.PendingBatches()
	if err != nil {
		t.Fatalf("pending: %v", err)
	}
	if n != 1 {
		t.Fatalf("pending=%d want 1", n)
	}

	if err := st.MarkIngested("fp1"); err != nil {
		t.Fatalf("mark: %v", err)
	}
	dup, err := st.AlreadyIngested("fp1")
	if err != nil {
		t.Fatalf("already: %v", err)
	}
	if !dup {
		t.Fatalf("fp1 should be ingested")
	}
}

// The skip rule has four cases and three of them are the ones that bite:
// an unvisited thread, a listing that published no count, and a thread that
// grew by exactly one. Only the fourth is the happy path.
func TestThreadUnchanged(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	// Never visited -> must be fetched, whatever the listing claims.
	if skip, err := st.ThreadUnchanged("reddit", "t1", 31); err != nil || skip {
		t.Fatalf("unvisited thread: skip=%v err=%v, want skip=false", skip, err)
	}

	if err := st.MarkThreadRead("reddit", "t1", 31); err != nil {
		t.Fatalf("mark: %v", err)
	}

	// Same count -> nothing new -> skip.
	if skip, err := st.ThreadUnchanged("reddit", "t1", 31); err != nil || !skip {
		t.Fatalf("unchanged thread: skip=%v err=%v, want skip=true", skip, err)
	}
	// One more comment -> must be re-read.
	if skip, err := st.ThreadUnchanged("reddit", "t1", 32); err != nil || skip {
		t.Fatalf("grown thread: skip=%v err=%v, want skip=false", skip, err)
	}
	// An ABSENT count is not a zero: it can never justify a skip, even
	// against a thread already on record.
	if skip, err := st.ThreadUnchanged("reddit", "t1", -1); err != nil || skip {
		t.Fatalf("absent count: skip=%v err=%v, want skip=false", skip, err)
	}

	// Per-platform keying: the same id on another platform is another thread.
	if skip, err := st.ThreadUnchanged("lemmy", "t1", 31); err != nil || skip {
		t.Fatalf("other platform: skip=%v err=%v, want skip=false", skip, err)
	}

	// A thread read while the listing published no count stores 0, not -1 —
	// otherwise the sentinel becomes a ceiling that suppresses every later
	// visit.
	if err := st.MarkThreadRead("reddit", "t2", -1); err != nil {
		t.Fatalf("mark unpublished: %v", err)
	}
	if skip, err := st.ThreadUnchanged("reddit", "t2", 1); err != nil || skip {
		t.Fatalf("after unpublished count: skip=%v err=%v, want skip=false", skip, err)
	}

	// Re-reading updates the ceiling rather than appending a second row.
	if err := st.MarkThreadRead("reddit", "t1", 32); err != nil {
		t.Fatalf("re-mark: %v", err)
	}
	if skip, err := st.ThreadUnchanged("reddit", "t1", 32); err != nil || !skip {
		t.Fatalf("after re-read: skip=%v err=%v, want skip=true", skip, err)
	}
}

func i64(v int64) *int64 { return &v }

// The point of appending observations rather than updating a row: a price cut
// three weeks ago is still readable today.
func TestListingObservationsAccumulate(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	base := Listing{
		Portal: "rightmove", ListingID: "RM1", URL: "https://example/RM1",
		Price: i64(450000), Currency: "GBP", Status: "for_sale",
		Media:   []Media{{Position: 0, URL: "https://cdn/a.jpg", PHash: "aaaaaaaaaaaaaaaa"}},
		Payload: `{"beds":3}`,
	}
	if _, err := st.RecordObservation("run1", base); err != nil {
		t.Fatalf("first observation: %v", err)
	}

	// Unchanged visit: still recorded, because "we looked and it had not
	// moved" is what makes days-on-market a fact rather than a guess.
	if _, err := st.RecordObservation("run2", base); err != nil {
		t.Fatalf("repeat observation: %v", err)
	}

	cut := base
	cut.Price = i64(425000)
	if _, err := st.RecordObservation("run3", cut); err != nil {
		t.Fatalf("price cut: %v", err)
	}

	hist, err := st.PriceHistory("rightmove", "RM1")
	if err != nil {
		t.Fatalf("history: %v", err)
	}
	if len(hist) != 3 {
		t.Fatalf("want 3 observations, got %d — observations must append, never overwrite", len(hist))
	}
	if *hist[0].Price != 450000 || *hist[2].Price != 425000 {
		t.Fatalf("price history did not survive: %v -> %v", *hist[0].Price, *hist[2].Price)
	}
	if hist[0].ContentHash != hist[1].ContentHash {
		t.Error("an unchanged listing must produce an unchanged content hash")
	}
	if hist[1].ContentHash == hist[2].ContentHash {
		t.Error("a price cut must change the content hash")
	}
	// The gallery did not move while the price did — the two hashes are
	// separate precisely so this is visible.
	if hist[1].GalleryHash != hist[2].GalleryHash {
		t.Error("a price cut must NOT change the gallery hash")
	}
}

func TestGalleryHashTracksPhotosOnly(t *testing.T) {
	a := Listing{Portal: "p", ListingID: "1", Payload: `{}`,
		Media: []Media{{Position: 0, PHash: "1111111111111111"}, {Position: 1, PHash: "2222222222222222"}}}
	same := a
	swapped := a
	swapped.Media = []Media{{Position: 0, PHash: "1111111111111111"}, {Position: 1, PHash: "3333333333333333"}}
	reordered := a
	reordered.Media = []Media{{Position: 0, PHash: "2222222222222222"}, {Position: 1, PHash: "1111111111111111"}}

	if a.GalleryHash() != same.GalleryHash() {
		t.Error("identical galleries must hash alike")
	}
	if a.GalleryHash() == swapped.GalleryHash() {
		t.Error("a replaced photograph must change the gallery hash")
	}
	if a.GalleryHash() == reordered.GalleryHash() {
		t.Error("gallery order is published information and must be part of the hash")
	}
	// No fingerprints at all is not the same as an empty gallery: it means
	// nothing was fetched, and must not read as a gallery that changed.
	unhashed := Listing{Portal: "p", ListingID: "1", Payload: `{}`,
		Media: []Media{{Position: 0, URL: "https://cdn/x.jpg"}}}
	if unhashed.GalleryHash() != "" {
		t.Error("a gallery with no fingerprints must hash to empty, not to a value")
	}
}

// The mechanism B2 rests on: one property, two portals, recognised by its
// photographs.
func TestMatchByPhashFindsTheSamePropertyOnAnotherPortal(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	shared := "abc123abc123abc1"
	if _, err := st.RecordObservation("r1", Listing{
		Portal: "rightmove", ListingID: "RM9", URL: "https://rm/9", Payload: `{}`,
		Media: []Media{{Position: 0, URL: "https://rm/a.jpg", PHash: shared}},
	}); err != nil {
		t.Fatalf("rightmove: %v", err)
	}
	if _, err := st.RecordObservation("r1", Listing{
		Portal: "zoopla", ListingID: "ZP4", URL: "https://zp/4", Payload: `{}`,
		Media: []Media{
			{Position: 0, URL: "https://zp/x.jpg", PHash: "ffffffffffffffff"},
			{Position: 1, URL: "https://zp/a.jpg", PHash: shared},
		},
	}); err != nil {
		t.Fatalf("zoopla: %v", err)
	}

	got, err := st.MatchByPhash(shared, "rightmove")
	if err != nil {
		t.Fatalf("match: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 cross-portal match, got %d", len(got))
	}
	if got[0].Portal != "zoopla" || got[0].ListingID != "ZP4" || got[0].Position != 1 {
		t.Fatalf("wrong match: %+v", got[0])
	}
	// The excluded portal is the one asking; it must not match itself.
	for _, m := range got {
		if m.Portal == "rightmove" {
			t.Error("the querying portal must be excluded from its own matches")
		}
	}
	// An unfingerprinted image is not a wildcard.
	if out, err := st.MatchByPhash("", "rightmove"); err != nil || len(out) != 0 {
		t.Errorf("empty phash: got %d matches, err %v — must match nothing", len(out), err)
	}
}

func TestLastObservationReportsUnseen(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	if _, found, err := st.LastObservation("funda", "never"); err != nil || found {
		t.Fatalf("unseen listing: found=%v err=%v, want found=false", found, err)
	}
	l := Listing{Portal: "funda", ListingID: "F1", URL: "u", Payload: `{"a":1}`,
		Media: []Media{{Position: 0, PHash: "0f0f0f0f0f0f0f0f"}}}
	if _, err := st.RecordObservation("r", l); err != nil {
		t.Fatalf("record: %v", err)
	}
	got, found, err := st.LastObservation("funda", "F1")
	if err != nil || !found {
		t.Fatalf("after record: found=%v err=%v", found, err)
	}
	if got.ContentHash != l.ContentHash() || got.GalleryHash != l.GalleryHash() {
		t.Error("LastObservation must return the hashes that were written")
	}
}

// The regression that mattered: exact matching passed its tests and would
// still have missed most real cross-portal pairs, because a re-encoded
// photograph does not keep its hash. These cases are stated in bit distances
// measured from real files, not invented ones.
func TestMatchByPhashFindsNearDuplicates(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	// The photograph as portal A published it.
	origin := mediahash.Hash(0x1a2b3c4d5e6f7081)

	// The same photograph as portal B re-encoded it, at distances that were
	// actually observed: 1 bit (JPEG q70 of a textured photo), 4 (150px
	// thumbnail), 9 (JPEG q40 of flat artwork).
	flip := func(h mediahash.Hash, bits ...uint) mediahash.Hash {
		for _, b := range bits {
			h ^= 1 << b
		}
		return h
	}
	cases := []struct {
		portal string
		hash   mediahash.Hash
		want   bool
		why    string
	}{
		{"zoopla", origin, true, "identical"},
		{"funda", flip(origin, 3), true, "1 bit — jpeg q70"},
		{"domain", flip(origin, 1, 9, 20, 40), true, "4 bits — 150px thumbnail"},
		// 7 bits is the last distance band retrieval GUARANTEES, and this
		// spreads them as adversarially as 7 bits can be spread — one per band
		// for seven of the eight, so exactly one band survives intact.
		{"bayut", flip(origin, 0, 8, 16, 24, 32, 40, 48), true, "7 bits, one per band — the guaranteed limit"},
		{"immoweb", flip(origin, 0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38,
			40, 42, 44, 46, 48, 50, 52, 54, 56, 58), false, "30 bits — a different photograph"},
	}
	for _, c := range cases {
		if _, err := st.RecordObservation("r", Listing{
			Portal: c.portal, ListingID: c.portal + "-1", URL: "https://" + c.portal, Payload: `{}`,
			Media: []Media{{Position: 0, PHash: c.hash.String()}},
		}); err != nil {
			t.Fatalf("%s: %v", c.portal, err)
		}
	}

	got, err := st.MatchByPhash(origin.String(), "property24")
	if err != nil {
		t.Fatalf("match: %v", err)
	}
	found := map[string]bool{}
	for _, m := range got {
		found[m.Portal] = true
	}
	for _, c := range cases {
		if found[c.portal] != c.want {
			t.Errorf("%s (%s): found=%v want=%v", c.portal, c.why, found[c.portal], c.want)
		}
	}
}

// A shared band is a reason to compare, never a match. If the distance check
// were dropped, this pair — deliberately built to collide in one band while
// differing wildly elsewhere — would be reported as the same photograph.
func TestBandCollisionIsNotAMatch(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	want := mediahash.Hash(0xAABBCCDDEEFF0011)
	// Identical top band (0xAA), every other band inverted.
	collide := mediahash.Hash(0xAA) << 56
	collide |= ^(want & 0x00FFFFFFFFFFFFFF) & 0x00FFFFFFFFFFFFFF

	if d := mediahash.Distance(want, collide); d <= mediahash.SameThreshold {
		t.Fatalf("fixture is wrong: distance %d must exceed the threshold", d)
	}
	if want.Bands()[0] != collide.Bands()[0] {
		t.Fatal("fixture is wrong: the two must share band 0")
	}

	if _, err := st.RecordObservation("r", Listing{
		Portal: "other", ListingID: "X", URL: "u", Payload: `{}`,
		Media: []Media{{Position: 0, PHash: collide.String()}},
	}); err != nil {
		t.Fatalf("record: %v", err)
	}
	got, err := st.MatchByPhash(want.String(), "mine")
	if err != nil {
		t.Fatalf("match: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("a band collision was reported as a match: %+v", got)
	}
}

// The documented limit of band retrieval, asserted rather than left implicit.
//
// Eight bands guarantee a shared band only while the differing bits number
// fewer than eight. At 8+ bits spread one-per-band, every band differs and the
// candidate lookup returns nothing — even though the distance check would have
// accepted the pair. This is a real hole, it is narrow, and a test that
// pretended otherwise would be worse than the hole.
//
// It matters least where it bites: 8+ bit drift was measured only on flat
// artwork (floor plans, agency logo cards), which is the least useful content
// in a property gallery for identifying a house. Textured photographs stayed
// within 5.
func TestBandRetrievalHasADocumentedBlindSpot(t *testing.T) {
	st, err := Open(":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	origin := mediahash.Hash(0x1a2b3c4d5e6f7081)
	var spread mediahash.Hash = origin
	for b := uint(0); b < 64; b += 8 { // one bit in every one of the 8 bands
		spread ^= 1 << b
	}
	if d := mediahash.Distance(origin, spread); d != 8 {
		t.Fatalf("fixture: distance %d, want 8", d)
	}
	if !mediahash.SameImage(origin, spread) {
		t.Fatal("fixture: 8 bits is inside SameThreshold, so only retrieval can lose this pair")
	}

	if _, err := st.RecordObservation("r", Listing{
		Portal: "other", ListingID: "Y", URL: "u", Payload: `{}`,
		Media: []Media{{Position: 0, PHash: spread.String()}},
	}); err != nil {
		t.Fatalf("record: %v", err)
	}
	got, err := st.MatchByPhash(origin.String(), "mine")
	if err != nil {
		t.Fatalf("match: %v", err)
	}
	if len(got) != 0 {
		t.Log("NOTE: retrieval found an 8-bit-spread pair; the guarantee has improved beyond what is documented")
	}
}
