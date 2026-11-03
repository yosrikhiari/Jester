package realestate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"jester/internal/config"
	"jester/internal/siteprofile"
	"jester/internal/store"
)

// FetchListingList loads the siteprofile for src, walks the pages named by
// pagePlan via cloakserve (FetchRenderedPage), and returns the parsed
// listings. Mirrors reddit.ListThreads.
// detailPerPage bounds the optional second fetch per result page. Zero leaves
// it off, which is the default: it is one request per listing on top of the
// result page.
// Stats is what one portal walk produced and cost: the per-run facts the
// drift ledger records beside the source's row in run_outcome. Pages are
// counted per attempted URL; latency wraps the fetch call whole, so a page
// that stumbles into its bounded retry lands the retry's backoff inside its
// own clock — that is a fair cost of that page, but it is not one request's
// latency, and the trend cards should read it that way.
type Stats struct {
	Pages     int                    `json:"pages"`      // URLs attempted (planned pages × seeds)
	PagesOK   int                    `json:"pages_ok"`   // fetched and parsed
	PagesErr  int                    `json:"pages_err"`  // fetch or parse failed
	Bytes     int                    `json:"bytes"`      // successful response bodies
	LatencyMS int64                  `json:"latency_ms"` // time inside fetch calls only, pacing excluded
	FetchMode string                 `json:"fetch_mode"` // "http" | "browser"
	Parse     siteprofile.ParseStats `json:"parse"`      // siteprofile counters, summed over pages
}

func FetchListingList(ctx context.Context, src config.Source, delay time.Duration, perSource int, configDir string, detailPerPage int) ([]store.Listing, Stats, error) {
	var st Stats
	profilePath := filepath.Join(configDir, "profiles", src.Name+".yaml")
	profileBytes, err := os.ReadFile(profilePath)
	if err != nil {
		return nil, st, fmt.Errorf("load profile %s: %w", profilePath, err)
	}
	profile, err := siteprofile.Load(profileBytes)
	if err != nil {
		return nil, st, fmt.Errorf("parse profile %s: %w", src.Name, err)
	}

	pages := pagePlan(profile.List, perSource)
	overHTTP := strings.EqualFold(profile.Fetch, "http")
	st.FetchMode = "browser"
	if overHTTP {
		st.FetchMode = "http"
	}
	st.Pages = len(pages)

	var allListings []store.Listing
	// The first failure is kept so that a run which fetches nothing can say
	// WHY. "no listings found" is the one message that fits both "the portal
	// published nothing today" and "every page 403'd", and those need
	// different responses from whoever reads the log.
	var firstErr error
	for i, pageURL := range pages {
		var body string
		var err error
		startAt := time.Now()
		if overHTTP {
			body, err = FetchPageHTTP(ctx, pageURL)
		} else {
			body, err = FetchRenderedPage(ctx, pageURL, delay, 0)
		}
		st.LatencyMS += time.Since(startAt).Milliseconds()
		if err != nil {
			st.PagesErr++
			fmt.Printf("[live]   realestate page %d failed: %v\n", i+1, err)
			if firstErr == nil {
				firstErr = fmt.Errorf("fetch page %d: %w", i+1, err)
			}
			continue
		}
		st.Bytes += len(body)

		listings, ps, err := profile.Parse(body)
		st.Parse.Add(ps)
		if err != nil {
			st.PagesErr++
			fmt.Printf("[live]   realestate page %d parse failed: %v\n", i+1, err)
			if firstErr == nil {
				firstErr = fmt.Errorf("parse page %d: %w", i+1, err)
			}
			continue
		}
		st.PagesOK++

		// Fields that only exist on a listing's own page, when asked for. Houni
		// publishes no price on its tiles at all, so without this its listings
		// arrive priced nil on every scheduled run.
		if detailPerPage != 0 && profile.Extract.Detail.Wanted() {
			ds := FetchDetails(ctx, profile, listings, detailPerPage, delay)
			fmt.Printf("[live]   realestate detail pages: %s\n", ds)
		}

		allListings = append(allListings, listings...)
		if i < len(pages)-1 {
			time.Sleep(Pace(delay))
		}
	}

	if len(allListings) == 0 {
		if firstErr != nil {
			return nil, st, fmt.Errorf("no listings for %s, every page failed: %w", src.Name, firstErr)
		}
		return nil, st, fmt.Errorf("no listings found for %s: %d page(s) parsed clean and held nothing", src.Name, len(pages))
	}
	return allListings, st, nil
}

// pagePlan returns the page URLs to walk for one run, in order.
//
// WHY THIS IS NOT max_pages. A profile with an empty page_param has not had
// its pagination worked out, and the profiles say so in their own words —
// property24.yaml carries the comment "an unset page_param means one page".
// buildPageURL agrees: with no parameter to advance there is no page 2, so it
// returns the base URL for every page number. Looping max_pages times anyway
// does not fetch more listings, it fetches the FIRST page max_pages times:
// same URL, same results appended again, each one a real headless-browser
// navigation spent against the portal's rate limit. Six of the seven shipped
// profiles are in exactly that state, so honouring the documented meaning here
// is the difference between one page fetch per source per run and five.
func pagePlan(list siteprofile.List, perSource int) []string {
	pages := list.MaxPages
	if pages <= 0 {
		pages = 1
	}
	if list.PageParam == "" && list.PageTemplate == "" {
		pages = 1
	}
	// A non-positive perSource is "no ceiling given", not "no pages": clamping
	// to it would walk zero pages and report the source as empty.
	if perSource > 0 && pages > perSource {
		pages = perSource
	}
	// Every seed gets the same page walk. A portal with no pagination and six
	// seeds is six fetches, not one - which is the whole point for the portals
	// that cannot be paged at all.
	seeds := list.Seeds()
	urls := make([]string, 0, pages*len(seeds))
	for _, seed := range seeds {
		for page := 1; page <= pages; page++ {
			urls = append(urls, PageURL(seed, list, page))
		}
	}
	return urls
}

// trimURLExt drops a trailing file extension from a URL path, for the
// {url_base} placeholder.
//
// Habitaclia pages by rewriting the filename: viviendas-madrid.htm becomes
// viviendas-madrid-2.htm, and ?pagina=2 re-serves page one. {url} alone cannot
// express that - it would produce "viviendas-madrid.htm-2.htm" - so a template
// needs the stem.
func trimURLExt(u string) string {
	// The scheme's own "//" is not a path separator. Counting it as one turned
	// https://www.example.co.uk into https://www.example.co - stripping a TLD
	// off a URL that has no path at all.
	rest := u
	if i := strings.Index(u, "://"); i >= 0 {
		rest = u[i+3:]
	}
	rel := strings.Index(rest, "/")
	if rel < 0 {
		return u // host only: every dot in it belongs to the hostname
	}
	pathStart := len(u) - len(rest) + rel
	dot := strings.LastIndex(u, ".")
	// Only a dot in the LAST path segment, inside the path, is an extension.
	if dot > strings.LastIndex(u, "/") && dot > pathStart && len(u)-dot <= 6 {
		return u[:dot]
	}
	return u
}

// PageURL constructs the page-N URL for one seed. Exported so a bulk harvest
// can walk past a profile's max_pages without duplicating the rule for what
// "page 2" means.
//
// Two shapes, because portals use two. Most append a query parameter; Mubawab
// appends a path suffix (":p:2"), which no query parameter can express.
func PageURL(seed string, list siteprofile.List, page int) string {
	if page <= 1 {
		return seed
	}
	if list.PageTemplate != "" {
		u := strings.ReplaceAll(list.PageTemplate, "{url_base}", trimURLExt(seed))
		u = strings.ReplaceAll(u, "{url}", seed)
		return strings.ReplaceAll(u, "{page}", strconv.Itoa(page))
	}
	if list.PageParam == "" {
		return seed
	}
	sep := "?"
	if strings.Contains(seed, "?") {
		sep = "&"
	}
	return fmt.Sprintf("%s%s%s=%d", seed, sep, list.PageParam, page)
}
