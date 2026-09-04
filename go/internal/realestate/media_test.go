package realestate

import (
	"context"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"testing"
	"unicode/utf8"

	"jester/internal/store"
)

// gradient builds a deterministic image so the hash is stable and two
// different pictures are actually different.
func gradient(t *testing.T, seed int) []byte {
	t.Helper()
	img := image.NewGray(image.Rect(0, 0, 64, 64))
	for y := 0; y < 64; y++ {
		for x := 0; x < 64; x++ {
			img.SetGray(x, y, color.Gray{Y: uint8((x*3 + y*seed) % 256)})
		}
	}
	var buf writerTo
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode: %v", err)
	}
	return buf.b
}

type writerTo struct{ b []byte }

func (w *writerTo) Write(p []byte) (int, error) { w.b = append(w.b, p...); return len(p), nil }

// The gap this closes: listing_media.phash was documented and never written,
// so the dedup harness found candidates for nothing.
func TestHashMediaFingerprintsAGallery(t *testing.T) {
	one, two := gradient(t, 1), gradient(t, 7)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/1.png":
			w.Header().Set("Content-Type", "image/png")
			w.Write(one)
		case "/2.png":
			w.Header().Set("Content-Type", "image/png")
			w.Write(two)
		case "/broken.webp":
			// Not a format any standard-library decoder claims. This is the
			// real Tayara case: its CDN serves WebP whatever Accept says.
			w.Header().Set("Content-Type", "image/webp")
			w.Write([]byte("RIFF????WEBPVP8 not-really"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	l := store.Listing{Media: []store.Media{
		{Position: 0, URL: srv.URL + "/1.png"},
		{Position: 1, URL: srv.URL + "/2.png"},
		{Position: 2, URL: srv.URL + "/broken.webp"},
		{Position: 3, URL: srv.URL + "/missing.png"},
	}}
	st := HashMedia(context.Background(), &l, 0, 0)

	if st.Hashed != 2 {
		t.Errorf("hashed %d, want 2 (%s)", st.Hashed, st)
	}
	// An undecodable format and a dead URL are different problems and must not
	// be counted together: one is a dependency decision, the other is rot.
	if st.Unsupported != 1 {
		t.Errorf("unsupported %d, want 1 (%s)", st.Unsupported, st)
	}
	if st.Failed != 1 {
		t.Errorf("failed %d, want 1 (%s)", st.Failed, st)
	}
	if len(l.Media[0].PHash) != 16 || len(l.Media[1].PHash) != 16 {
		t.Fatalf("want 16-char hashes, got %q and %q", l.Media[0].PHash, l.Media[1].PHash)
	}
	if l.Media[0].PHash == l.Media[1].PHash {
		t.Error("two different pictures hashed the same")
	}
	if l.Media[2].PHash != "" || l.Media[3].PHash != "" {
		t.Error("a photo that was not hashed must stay empty, not carry a fake hash")
	}
	// GalleryHash stays empty until something is fingerprinted, and must not
	// once something is - that is the whole point of writing these.
	if l.GalleryHash() == "" {
		t.Error("gallery still unhashed after two photos were fingerprinted")
	}
}

// perListing is a budget, and a listing with forty photographs must not spend
// forty downloads to answer a question the first few answer.
func TestHashMediaRespectsThePerListingBudget(t *testing.T) {
	img := gradient(t, 3)
	var served int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		served++
		w.Header().Set("Content-Type", "image/png")
		w.Write(img)
	}))
	defer srv.Close()

	l := store.Listing{}
	for i := 0; i < 10; i++ {
		l.Media = append(l.Media, store.Media{Position: i, URL: srv.URL + "/x.png"})
	}
	st := HashMedia(context.Background(), &l, 3, 0)
	if served != 3 {
		t.Errorf("downloaded %d photos, want 3", served)
	}
	if st.Hashed != 3 || st.Skipped != 7 {
		t.Errorf("got %s, want hashed=3 skipped=7", st)
	}
}

// A hash already present is not re-fetched: re-running a harvest over an
// archive must not re-download every gallery it has already fingerprinted.
func TestHashMediaSkipsWhatIsAlreadyHashed(t *testing.T) {
	var served int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		served++
		http.NotFound(w, r)
	}))
	defer srv.Close()

	l := store.Listing{Media: []store.Media{{URL: srv.URL + "/a.png", PHash: "84636cac6ec8093b"}}}
	st := HashMedia(context.Background(), &l, 0, 0)
	if served != 0 {
		t.Errorf("re-downloaded an already-hashed photo")
	}
	if st.Skipped != 1 || st.Hashed != 0 {
		t.Errorf("got %s, want skipped=1", st)
	}
}

// Tunisie Annonce answers Content-Type: text/html with NO charset - legacy
// ASP, so the bytes are Windows-1252 - while every other portal declares
// UTF-8. Stored raw, those bytes are not text: SQLite took them and Python's
// driver then refused the whole column, which silently took the listings
// export to zero rows over an archive holding 920, and took five other
// portals' data down with one portal's accented "meublé".
func TestToUTF8TranscodesWindows1252(t *testing.T) {
	// "meublé par nuité" as cp1252: é is a single byte 0xE9.
	latin1 := []byte{'m', 'e', 'u', 'b', 'l', 0xE9, ' ', 'n', 'u', 'i', 't', 0xE9}
	got := string(toUTF8(latin1))
	if got != "meublé nuité" {
		t.Errorf("got %q, want %q", got, "meublé nuité")
	}
	if !utf8.ValidString(got) {
		t.Error("output is still not valid UTF-8")
	}
}

// The 0x80-0x9F range is where Windows-1252 and Latin-1 disagree, and it holds
// the smart quotes a French classifieds page is full of.
func TestToUTF8UsesTheWindowsRangeNotLatin1(t *testing.T) {
	got := string(toUTF8([]byte{0x93, 'a', 0x94, ' ', 0x80}))
	want := "\u201Ca\u201D \u20AC" // curly quotes and a euro sign
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

// Valid UTF-8 must pass through untouched - every other portal serves it, and
// re-encoding correct text would corrupt Arabic and French alike.
func TestToUTF8LeavesValidUTF8Alone(t *testing.T) {
	for _, in := range []string{
		"Appartement S+2 à La Soukra",
		"شقة للبيع",
		"plain ascii",
		"",
	} {
		if got := string(toUTF8([]byte(in))); got != in {
			t.Errorf("%q was rewritten to %q", in, got)
		}
	}
}
