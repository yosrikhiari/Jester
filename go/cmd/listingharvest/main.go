// Command listingharvest walks real-estate portals over plain HTTP and writes
// unique listings as JSONL.
//
// WHY IT IS SEPARATE FROM THE WORKER. A routine run visits a source once at
// its profile's max_pages and moves on; a harvest walks one portal deep until
// it has the volume asked for. The repo already draws that line for forums
// (-backfill "is a live fetch" and is refused without -live), and the same
// split applies here: bulk depth is a deliberate operation, not a default.
//
// It reuses the shipped profiles and siteprofile.Parse, so what it extracts is
// what the worker would extract - which is the point. A harvest that used its
// own parser would prove nothing about the pipeline.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"jester/internal/realestate"
	"jester/internal/siteprofile"
	"jester/internal/store"
)

// record is one listing, flattened for a JSONL line.
type record struct {
	Portal    string         `json:"portal"`
	Market    string         `json:"market"`
	ListingID string         `json:"listing_id"`
	URL       string         `json:"url"`
	Price     *int64         `json:"price"`
	Currency  string         `json:"currency"`
	Status    string         `json:"status,omitempty"`
	MediaN    int            `json:"media_count"`
	Media     []string       `json:"media,omitempty"`
	PHashes   []string       `json:"phashes,omitempty"`
	Payload   map[string]any `json:"payload"`
	Page      int            `json:"page"`
	FetchedAt string         `json:"fetched_at"`
}

type portalStat struct {
	Name       string
	Pages      int
	Raw        int
	Unique     int
	DupInPage  int
	Errors     int
	NoIDs      int
	FirstError string
	emptyRun   int
	// StoppedWhy records why the walk left this portal, so a short harvest
	// reports "ran out of results at page 15" rather than looking like a bug.
	StoppedWhy string
}

func main() {
	configDir := flag.String("config", "../config", "directory holding profiles/")
	portalsCSV := flag.String("portals", "property24,privateproperty,tayara", "comma-separated profile names")
	target := flag.Int("target", 1000, "stop once this many UNIQUE listings are collected")
	maxPages := flag.Int("max-pages", 80, "hard ceiling on pages per portal")
	delay := flag.Duration("delay", 1500*time.Millisecond, "base pause between page fetches (jittered)")
	out := flag.String("out", "listings.jsonl", "JSONL output path")
	// Optional, because a harvest and an ingest are different acts. Writing to
	// a database is the one side effect worth asking for by name.
	dbPath := flag.String("db", "", "also record every listing into this SQLite archive (empty = JSONL only)")
	runID := flag.String("run", "harvest", "run identifier for recorded observations")
	// Off by default. Hashing downloads every photograph it hashes, which is a
	// different order of traffic from reading result pages, and should be
	// asked for rather than inherited.
	hashMedia := flag.Int("hash-media", 0, "download and dHash up to N photos per listing (0 = off, negative = whole gallery)")
	mediaDelay := flag.Duration("media-delay", 250*time.Millisecond, "pause between photo downloads")
	// One extra request per listing, so it is asked for by name and capped.
	detail := flag.Int("detail", 0, "fetch each listing's own page to fill fields the result page lacks (0 = off, per page cap)")
	detailDelay := flag.Duration("detail-delay", 900*time.Millisecond, "pause between detail-page fetches")
	flag.Parse()

	portals := strings.Split(*portalsCSV, ",")
	profiles := make([]*siteprofile.Profile, 0, len(portals))
	for _, name := range portals {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		b, err := os.ReadFile(filepath.Join(*configDir, "profiles", name+".yaml"))
		if err != nil {
			fatal("read profile %s: %v", name, err)
		}
		p, err := siteprofile.Load(b)
		if err != nil {
			fatal("load profile %s: %v", name, err)
		}
		if !strings.EqualFold(p.Fetch, "http") {
			fatal("profile %s is fetch=%q; this harvester is the plain-HTTP path only", name, p.Fetch)
		}
		profiles = append(profiles, p)
	}
	if len(profiles) == 0 {
		fatal("no profiles selected")
	}

	var archive *store.Store
	if *dbPath != "" {
		var err error
		archive, err = store.Open(*dbPath)
		if err != nil {
			fatal("open db %s: %v", *dbPath, err)
		}
		fmt.Printf("recording observations into %s (run %s)\n", *dbPath, *runID)
	}

	f, err := os.Create(*out)
	if err != nil {
		fatal("create %s: %v", *out, err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)

	ctx := context.Background()
	// Identity is (portal, listing_id): the same id at two portals is two
	// listings of one property, not one record, and collapsing them here would
	// destroy the cross-portal duplicate the pipeline exists to find.
	seen := map[string]bool{}
	stats := map[string]*portalStat{}
	for _, p := range profiles {
		stats[p.Portal] = &portalStat{Name: p.Portal}
	}

	// A portal that has run out of results must be left alone. Without this the
	// walk kept asking a finished portal for pages it had already 404'd -
	// Point Waterfront holds about 280 listings, so a target of 1000 aimed
	// another sixty 404s at it for nothing.
	done := map[string]bool{}
	var mediaStats realestate.MediaStats
	var detailStats realestate.DetailStats
	detailStats.Fields = map[string]int{}
	total := 0
	// The ordered URL plan per portal: every seed walked to max-pages, seed
	// first then page. A portal that cannot be paged at all still has as many
	// fetches as it has seeds, which for Tunisie Annonce is the only way to
	// reach more than its first 25 rows.
	plans := map[string][]string{}
	longest := 0
	for _, p := range profiles {
		var urls []string
		pages := *maxPages
		if p.List.PageParam == "" && p.List.PageTemplate == "" {
			pages = 1 // no pagination: one fetch per seed, not maxPages of the same URL
		}
		for _, seed := range p.List.Seeds() {
			for page := 1; page <= pages; page++ {
				urls = append(urls, realestate.PageURL(seed, p.List, page))
			}
		}
		plans[p.Portal] = urls
		if len(urls) > longest {
			longest = len(urls)
		}
	}

	// Round-robin rather than draining one portal at a time: it spreads the
	// request rate over several hosts instead of pointing all of it at one.
	for step := 0; step < longest && total < *target; step++ {
		page := step + 1
		progressed := false
		for _, p := range profiles {
			if total >= *target {
				break
			}
			st := stats[p.Portal]
			if done[p.Portal] {
				continue
			}
			if step >= len(plans[p.Portal]) {
				if st.StoppedWhy == "" {
					st.StoppedWhy = fmt.Sprintf("plan exhausted (%d seed(s) walked)", len(p.List.Seeds()))
				}
				done[p.Portal] = true
				continue
			}
			progressed = true

			url := plans[p.Portal][step]
			body, err := realestate.FetchPageHTTP(ctx, url)
			if err != nil {
				var se *realestate.StatusError
				if errors.As(err, &se) && se.Exhausted() {
					st.StoppedWhy = fmt.Sprintf("out of results at page %d (%d)", page, se.Code)
					done[p.Portal] = true
					fmt.Printf("  %-16s page %2d  end of results (%d)\n", p.Portal, page, se.Code)
					continue
				}
				// A rate limit ends this portal's walk for the run. Carrying on
				// after a 429 or a 503 is how a warning becomes a ban, and no
				// listing is worth that.
				if errors.As(err, &se) && se.RateLimited() {
					st.StoppedWhy = fmt.Sprintf("rate limited at page %d (%d) - backing off", page, se.Code)
					done[p.Portal] = true
					fmt.Printf("  %-16s page %2d  RATE LIMITED (%d), stopping this portal\n", p.Portal, page, se.Code)
					continue
				}
				st.Errors++
				if st.FirstError == "" {
					st.FirstError = err.Error()
				}
				fmt.Printf("  %-16s page %2d  FETCH FAILED: %v\n", p.Portal, page, err)
				time.Sleep(realestate.Pace(*delay))
				continue
			}
			listings, err := p.Parse(body)
			if err != nil {
				st.Errors++
				if st.FirstError == "" {
					st.FirstError = err.Error()
				}
				fmt.Printf("  %-16s page %2d  PARSE FAILED: %v\n", p.Portal, page, err)
				time.Sleep(realestate.Pace(*delay))
				continue
			}
			if *detail != 0 && p.Extract.Detail.Wanted() {
				ds := realestate.FetchDetails(ctx, p, listings, *detail, *detailDelay)
				detailStats.Fetched += ds.Fetched
				detailStats.Enriched += ds.Enriched
				detailStats.Failed += ds.Failed
				detailStats.Skipped += ds.Skipped
				for k, v := range ds.Fields {
					detailStats.Fields[k] += v
				}
			}
			st.Pages++
			st.Raw += len(listings)
			// TWO empty pages, not one. A single empty page also happens when
			// an anchor has just stopped matching, and stopping on the first
			// one turns a profile regression into what looks like a portal
			// that ran out of stock - which is exactly how the broken
			// property24 anchor read during this build.
			if len(listings) == 0 {
				st.emptyRun++
				fmt.Printf("  %-16s page %2d  empty page (%d in a row)\n", p.Portal, page, st.emptyRun)
				if st.emptyRun >= 2 {
					st.StoppedWhy = fmt.Sprintf("two empty pages, last at %d", page)
					done[p.Portal] = true
				}
				time.Sleep(realestate.Pace(*delay))
				continue
			}
			st.emptyRun = 0

			fresh := 0
			for _, l := range listings {
				if l.ListingID == "" {
					st.NoIDs++
					continue
				}
				key := l.Portal + "\x1f" + l.ListingID
				if seen[key] {
					st.DupInPage++
					continue
				}
				seen[key] = true
				st.Unique++
				fresh++
				total++
				if *hashMedia != 0 {
					mediaStats.Add(realestate.HashMedia(ctx, &l, *hashMedia, *mediaDelay))
				}
				if err := enc.Encode(toRecord(p, l, page)); err != nil {
					fatal("write record: %v", err)
				}
				if archive != nil {
					if _, err := archive.RecordObservation(*runID, l); err != nil {
						st.Errors++
						if st.FirstError == "" {
							st.FirstError = "record: " + err.Error()
						}
					}
				}
				if total >= *target {
					break
				}
			}
			fmt.Printf("  %-16s page %2d  parsed %3d  new %3d  running total %d\n",
				p.Portal, page, len(listings), fresh, total)
			time.Sleep(realestate.Pace(*delay))
		}
		if !progressed || len(done) == len(profiles) {
			break
		}
	}

	fmt.Printf("\n%-16s %6s %6s %8s %8s %7s %7s\n", "PORTAL", "PAGES", "RAW", "UNIQUE", "DUPES", "NO_ID", "ERRORS")
	names := make([]string, 0, len(stats))
	for n := range stats {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		s := stats[n]
		fmt.Printf("%-16s %6d %6d %8d %8d %7d %7d\n", s.Name, s.Pages, s.Raw, s.Unique, s.DupInPage, s.NoIDs, s.Errors)
		if s.StoppedWhy != "" {
			fmt.Printf("%-16s   stopped: %s\n", "", s.StoppedWhy)
		}
		if s.FirstError != "" {
			fmt.Printf("%-16s   first error: %s\n", "", s.FirstError)
		}
	}
	if *detail != 0 {
		fmt.Printf("\ndetail pages: %s\n", detailStats)
	}
	if *hashMedia != 0 {
		fmt.Printf("\nmedia: %s\n", mediaStats)
		if mediaStats.Unsupported > 0 {
			fmt.Printf("  %d photo(s) in a format no standard-library decoder reads (almost always WebP).\n", mediaStats.Unsupported)
			fmt.Printf("  Adding golang.org/x/image/webp is the fix, and is a dependency decision.\n")
		}
	}
	fmt.Printf("\n%d unique listings written to %s\n", total, *out)
	if total < *target {
		fmt.Printf("TARGET NOT MET: wanted %d, got %d\n", *target, total)
		os.Exit(2)
	}
}

func toRecord(p *siteprofile.Profile, l store.Listing, page int) record {
	var payload map[string]any
	if l.Payload != "" {
		_ = json.Unmarshal([]byte(l.Payload), &payload)
	}
	media := make([]string, 0, len(l.Media))
	var phashes []string
	for _, m := range l.Media {
		media = append(media, m.URL)
		if m.PHash != "" {
			phashes = append(phashes, m.PHash)
		}
	}
	return record{
		Portal:    l.Portal,
		Market:    p.Market,
		ListingID: l.ListingID,
		URL:       l.URL,
		Price:     l.Price,
		Currency:  l.Currency,
		Status:    l.Status,
		MediaN:    len(media),
		Media:     media,
		PHashes:   phashes,
		Payload:   payload,
		Page:      page,
		FetchedAt: time.Now().UTC().Format(time.RFC3339),
	}
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "listingharvest: "+format+"\n", a...)
	os.Exit(1)
}
