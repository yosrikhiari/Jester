package realestate

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"jester/internal/siteprofile"
	"jester/internal/store"
)

func detailProfile(t *testing.T, yaml string) *siteprofile.Profile {
	t.Helper()
	p, err := siteprofile.Load([]byte(yaml))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	return p
}

const streetProfile = `portal: p
base_url: https://x.test
extract:
  mode: anchored
  anchor: 'id="(?P<id>\d+)"'
  block_len: 500
  detail:
    fields:
      street:
        pattern: '"streetAddress"\s*:\s*"([^"]{3,70})"'
`

// The whole reason the tier exists: an address that only the listing's own
// page carries, and that decides whether two listings are one property.
func TestFetchDetailsFillsAFieldTheResultPageLacks(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`<script type="application/ld+json">{"@type":"Residence",` +
			`"address":{"streetAddress":"48 Buitenkant St"}}</script>`))
	}))
	defer srv.Close()

	p := detailProfile(t, streetProfile)
	ls := []store.Listing{{ListingID: "1", URL: srv.URL + "/a"}}
	st := FetchDetails(context.Background(), p, ls, 0, 0)

	if st.Fetched != 1 || st.Enriched != 1 {
		t.Fatalf("got %s, want one fetch and one enrichment", st)
	}
	if !strings.Contains(ls[0].Payload, "48 Buitenkant St") {
		t.Errorf("street not merged: %s", ls[0].Payload)
	}
	if st.Fields["street"] != 1 {
		t.Errorf("field not counted: %v", st.Fields)
	}
}

// The result page's own answer wins. Its block boundaries were checked; a
// detail page must not quietly overwrite a field that already parsed.
func TestFetchDetailsOnlyAddsNeverOverwrites(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"streetAddress":"WRONG STREET"}`))
	}))
	defer srv.Close()

	p := detailProfile(t, streetProfile)
	ls := []store.Listing{{ListingID: "1", URL: srv.URL + "/a",
		Payload: `{"street":"1 Real Road"}`}}
	FetchDetails(context.Background(), p, ls, 0, 0)
	if strings.Contains(ls[0].Payload, "WRONG STREET") {
		t.Errorf("detail page overwrote the result page: %s", ls[0].Payload)
	}
	if !strings.Contains(ls[0].Payload, "1 Real Road") {
		t.Errorf("existing value lost: %s", ls[0].Payload)
	}
}

// One request per listing is the heaviest thing here, so the budget has to
// hold and a listing with no URL must not be fetched at all.
func TestFetchDetailsRespectsBudgetAndSkipsListingsWithNoURL(t *testing.T) {
	var served int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		served++
		w.Write([]byte(`{"streetAddress":"1 Somewhere"}`))
	}))
	defer srv.Close()

	p := detailProfile(t, streetProfile)
	ls := []store.Listing{
		{ListingID: "1", URL: srv.URL + "/a"},
		{ListingID: "2", URL: srv.URL + "/b"},
		{ListingID: "3", URL: ""},
		{ListingID: "4", URL: srv.URL + "/d"},
	}
	st := FetchDetails(context.Background(), p, ls, 2, 0)
	if served != 2 {
		t.Errorf("fetched %d pages, budget was 2", served)
	}
	if st.Skipped != 2 {
		t.Errorf("got %s, want 2 skipped (one over budget, one with no URL)", st)
	}
}

// A portal that says "too many" must end the pass, not be retried through it.
// Property24 started answering 503 during this build after a session that had
// pulled twenty result pages and some two thousand four hundred photographs
// from it; the portal was right.
func TestFetchDetailsStopsDeadOnARateLimit(t *testing.T) {
	var served int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		served++
		w.Header().Set("Retry-After", "1")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	p := detailProfile(t, streetProfile)
	ls := make([]store.Listing, 5)
	for i := range ls {
		ls[i] = store.Listing{ListingID: "x", URL: srv.URL + "/a"}
	}
	st := FetchDetails(context.Background(), p, ls, 0, 0)
	if !st.RateLimited {
		t.Errorf("rate limit not recorded: %s", st)
	}
	if st.Fetched != 1 {
		t.Errorf("kept going after a 503: fetched %d", st.Fetched)
	}
	if st.Skipped != 4 {
		t.Errorf("got %s, want the remaining 4 skipped", st)
	}
}

func TestStatusErrorClassifiesRateLimits(t *testing.T) {
	for _, c := range []struct {
		code    int
		limited bool
	}{{429, true}, {503, true}, {404, false}, {500, false}} {
		e := &StatusError{Code: c.code}
		if e.RateLimited() != c.limited {
			t.Errorf("status %d: RateLimited()=%v, want %v", c.code, e.RateLimited(), c.limited)
		}
	}
}
