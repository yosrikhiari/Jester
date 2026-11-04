// The run_outcome ledger's write side, at the layer that does the visiting:
// runLive writes one row per visited source — clean visits, refused walks,
// browsers that never opened and adapters that do not exist all leave a row —
// so a silently broken portal reads as a trend in the console, not as a log
// line that flashes by. These tests pin that promise offline: every failure
// here happens before any real network use (a dead CDP port refused on
// loopback, a profile file that is not there), and the one walk that succeeds
// does so against an httptest portal.
package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"jester/internal/config"
	"jester/internal/prefilter"
	"jester/internal/store"
)

func workerStore(t *testing.T) *store.Store {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "j.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	return st
}

// recordRunOutcome stamps the identity columns the caller does not have to
// repeat, copies the outcome counters, and keeps a failure's first error —
// the row must say what the visit was, that it failed, and why.
func TestRecordRunOutcomeHelperCarriesTheVisit(t *testing.T) {
	st := workerStore(t)
	src := config.Source{Name: "zoopla", Platform: "realestate"}
	recordRunOutcome(st, store.RunOutcome{RunID: "r-1", Source: src.Name},
		src, "listing",
		outcome{items: 30, fresh: 28, dup: 2,
			extended: `{"pages":3,"pages_ok":2,"parse":{"kept":2}}`},
		nil)

	failing := config.Source{Name: "hn-ask", Platform: "hackernews"}
	recordRunOutcome(st, store.RunOutcome{RunID: "r-1", Source: failing.Name},
		failing, "feed", outcome{}, contextDeadlineRefused())

	rows, err := st.RunOutcomes("r-1")
	if err != nil {
		t.Fatalf("RunOutcomes: %v", err)
	}
	if len(rows) != 2 || rows[0].Source != "hn-ask" || rows[1].Source != "zoopla" {
		t.Fatalf("want hn-ask then zoopla, got %v", rows)
	}
	hn, z := rows[0], rows[1]
	if hn.Platform != "hackernews" || hn.Kind != "feed" {
		t.Errorf("identity from the source: %+v", hn)
	}
	if hn.FirstErr == "" {
		t.Error("a failed visit must keep its first error")
	}
	if hn.StatsJSON != "" || hn.Items != 0 {
		t.Errorf("a community failure carries no walk stats: %+v", hn)
	}
	if z.Platform != "realestate" || z.Kind != "listing" || z.FirstErr != "" {
		t.Errorf("identity on the clean visit: %+v", z)
	}
	if z.Items != 30 || z.Fresh != 28 || z.Dup != 2 || z.StatsJSON == "" {
		t.Errorf("counters on the clean visit: %+v", z)
	}
}

type cannedErr struct{ msg string }

func (e cannedErr) Error() string { return e.msg }

func contextDeadlineRefused() error {
	return cannedErr{"no adapter for platform=\"tumblr\" kind=\"\" yet"}
}

// One row per source per run: a source re-walked inside one run replaces its
// row instead of duplicating it — the ledger is a trend of visits, not a log.
func TestRecordRunOutcomeReplacesARewalkedRow(t *testing.T) {
	st := workerStore(t)
	src := config.Source{Name: "zoopla", Platform: "realestate"}
	recordRunOutcome(st, store.RunOutcome{RunID: "r-1", Source: src.Name},
		src, "listing", outcome{items: 30, fresh: 30}, nil)
	recordRunOutcome(st, store.RunOutcome{RunID: "r-1", Source: src.Name},
		src, "listing", outcome{items: 30, fresh: 0, dup: 30}, nil)

	rows, err := st.RunOutcomes("r-1")
	if err != nil {
		t.Fatalf("RunOutcomes: %v", err)
	}
	if len(rows) != 1 {
		t.Fatalf("want exactly one row for a re-walked source, got %d", len(rows))
	}
	if rows[0].Fresh != 0 || rows[0].Dup != 30 {
		t.Errorf("the second visit must win: %+v", rows[0])
	}
}

// The ledger is telemetry, never a job stopper: a store that cannot record
// (closed) must be reported in the log and tolerated, not panic the run.
func TestRecordRunOutcomeSurvivesAStoreThatCannotWrite(t *testing.T) {
	st := workerStore(t)
	st.Close()
	src := config.Source{Name: "zoopla", Platform: "realestate"}

	// A failed db.Exec returns immediately; the helper prints and moves on.
	recordRunOutcome(st, store.RunOutcome{RunID: "r-1", Source: src.Name},
		src, "listing", outcome{items: 30}, nil)
}

// A row per failure mode, from the loop itself: a source whose stealth
// browser never opens (CDP to a port nobody serves, refused on loopback, so
// no real network happens) and a source with no adapter both still write
// their row for the same run id.
func TestRunLiveWritesARowForEachVisitedSourceEvenWhenItFails(t *testing.T) {
	st := workerStore(t)
	cfg := &config.Config{}
	cfg.Scraper.CDPURL = "http://127.0.0.1:1"
	sources := []config.Source{
		{Name: "guarded", Platform: "reddit", URL: "https://www.reddit.com/r/devforhire/new/"},
		{Name: "forme", Platform: "tumblr", URL: "https://tumblr.example/ask"},
	}
	runLive(cfg, st, "r-live", prefilter.Params{}, sources, 0, 0, t.TempDir())

	rows, err := st.RunOutcomes("r-live")
	if err != nil {
		t.Fatalf("RunOutcomes: %v", err)
	}
	if len(rows) != 2 || rows[0].Source != "forme" || rows[1].Source != "guarded" {
		t.Fatalf("want guarded and forme recorded, got %v", rows)
	}
	for _, r := range rows {
		if r.Items != 0 || r.FirstErr == "" {
			t.Errorf("%s: a failed visit is a row with a reason: %+v", r.Source, r)
		}
	}
	guarded := rows[1]
	if !strings.Contains(guarded.FirstErr, "cdp probe") {
		t.Errorf("want the CDP probe failure in the row, got %q", guarded.FirstErr)
	}
	if guarded.Platform != "reddit" {
		t.Errorf("identity columns: %+v", guarded)
	}
	if !strings.Contains(rows[0].FirstErr, "no adapter") {
		t.Errorf("want the missing adapter in the row, got %q", rows[0].FirstErr)
	}
}

// fetchSource refuses an unknown platform out loud (R29 spirit), instead of
// silently ingesting nothing.
func TestFetchSourceRefusesAnUnknownAdapter(t *testing.T) {
	src := config.Source{Name: "forme", Platform: "tumblr", URL: "https://tumblr.example/ask"}
	_, err := fetchSource(context.Background(), &config.Config{}, workerStore(t),
		"r-1", prefilter.Params{}, src, "", 0, 0, &budget{}, t.TempDir())
	if err == nil {
		t.Fatal("a platform with no adapter must fail loudly")
	}
	want := `no adapter for platform="tumblr" kind="" yet`
	if err.Error() != want {
		t.Errorf("got %q, want %q", err.Error(), want)
	}
}

// A realestate walk that cannot even load its profile still hands the ledger
// its (empty) walk stats, so the console trend cannot confuse "no portal
// data" with "the worker never tried".
func TestFetchSourceRealestateCarriesStatsOnAFailedWalk(t *testing.T) {
	src := config.Source{Name: "missingprofile", Platform: "realestate",
		URL: "https://portal.example/s"}
	oc, err := fetchSource(context.Background(), &config.Config{}, workerStore(t),
		"r-1", prefilter.Params{}, src, "listing", 0, 0, &budget{}, t.TempDir())
	if err == nil {
		t.Fatal("a source without its profile must fail loudly")
	}
	if !strings.Contains(err.Error(), "load profile") {
		t.Errorf("want the profile load failure, got %v", err)
	}
	if oc.items != 0 || oc.fresh != 0 || oc.dup != 0 {
		t.Errorf("nothing was ingested: %+v", oc)
	}
	if !strings.HasPrefix(oc.extended, "{") {
		t.Fatalf("the walk stats must still ride along, got %q", oc.extended)
	}
	var stats map[string]any
	if err := json.Unmarshal([]byte(oc.extended), &stats); err != nil {
		t.Fatalf("stats_json is not JSON: %v", err)
	}
}

// The full healthy path, offline: a real HTTP realestate source (profile in
// the temp config dir, portal on httptest) is walked, its listing recorded
// and enqueued, and BOTH runs' rows tell the fresh/dup story — a row telling
// only "1 item" would hide "all of them were repeats".
func TestRunLiveRealestateOverHTTPRecordsTheFreshDupSplit(t *testing.T) {
	const page = `<html><body>` +
		`<div data-id="31"><div class="priceTag">31</div></div>` +
		`</body></html>`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(page))
	}))
	defer srv.Close()

	configDir := t.TempDir()
	profiles := filepath.Join(configDir, "profiles")
	if err := os.MkdirAll(profiles, 0o755); err != nil {
		t.Fatalf("mkdir profiles: %v", err)
	}
	yaml := `portal: fakezoo
market: zz
base_url: ` + srv.URL + `
currency: EUR
fetch: http

list:
  url: ` + srv.URL + `/l/
  max_pages: 1

extract:
  mode: anchored
  anchor: 'data-id="(?P<id>\d+)"'
  block_len: 300
  url_template: '/listing/{id}'
  fields:
    price:
      pattern: 'priceTag">(\d+)'
`
	if err := os.WriteFile(filepath.Join(profiles, "fakezoo.yaml"), []byte(yaml), 0o644); err != nil {
		t.Fatalf("write profile: %v", err)
	}

	st := workerStore(t)
	cfg := &config.Config{}
	sources := []config.Source{{Name: "fakezoo", Platform: "realestate",
		Kind: "listing", URL: srv.URL + "/l/"}}

	runLive(cfg, st, "r-fresh", prefilter.Params{}, sources, 0, 0, configDir)
	runLive(cfg, st, "r-dup", prefilter.Params{}, sources, 0, 0, configDir)

	fresh, err := st.RunOutcomes("r-fresh")
	if err != nil || len(fresh) != 1 {
		t.Fatalf("fresh run rows: %d rows, err %v", len(fresh), err)
	}
	dup, err := st.RunOutcomes("r-dup")
	if err != nil || len(dup) != 1 {
		t.Fatalf("dup run rows: %d rows, err %v", len(dup), err)
	}
	f, d := fresh[0], dup[0]
	if f.FirstErr != "" || f.Items != 1 || f.Fresh != 1 || f.Dup != 0 {
		t.Errorf("first visit is fresh: %+v", f)
	}
	if d.Items != 1 || d.Fresh != 0 || d.Dup != 1 {
		t.Errorf("second visit is a repeat: %+v", d)
	}
	// HTTP mode is what the walk stats must say: a trend card blaming the
	// identity that never existed (browser) would send the operator astray.
	for _, row := range []store.RunOutcome{f, d} {
		if !strings.Contains(row.StatsJSON, `"fetch_mode":"http"`) ||
			!strings.Contains(row.StatsJSON, `"pages_ok":1`) ||
			!strings.Contains(row.StatsJSON, `"kept":1`) {
			t.Errorf("stats_json: %s", row.StatsJSON)
		}
	}
}
