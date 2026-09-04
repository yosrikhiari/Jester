package realestate

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"jester/internal/siteprofile"
	"jester/internal/store"
)

// DetailStats counts what a detail pass did and which fields it bought,
// because a second request per listing has to justify itself.
type DetailStats struct {
	Fetched  int
	Enriched int
	Failed   int
	Skipped  int
	Fields   map[string]int
	// RateLimited records that the portal asked us to stop mid-pass.
	RateLimited bool
}

func (s DetailStats) String() string {
	names := make([]string, 0, len(s.Fields))
	for n := range s.Fields {
		names = append(names, fmt.Sprintf("%s=%d", n, s.Fields[n]))
	}
	sort.Strings(names)
	out := fmt.Sprintf("fetched=%d enriched=%d failed=%d skipped=%d",
		s.Fetched, s.Enriched, s.Failed, s.Skipped)
	if s.RateLimited {
		out += " RATE-LIMITED (pass stopped early)"
	}
	if len(names) > 0 {
		out += " [" + strings.Join(names, " ") + "]"
	}
	return out
}

// FetchDetails fills fields that only exist on a listing's own page.
//
// WHY A SECOND TIER. Some fields are simply not on the result page, and no
// amount of pattern work reaches them. Property24 publishes a street address
// only on the listing page - and it is the field that separates a genuine
// cross-portal duplicate from two different flats sharing a price and a
// suburb, which is what both confirmed false positives in the dedup validation
// turned out to be. Houni publishes no price on its tile at all: two patterns
// were tried against it and both reported fiction, because the number is not
// there to find.
//
// budget bounds the pass. This is one request per listing on top of the result
// pages, so it is opt-in and capped rather than something a routine run
// inherits. A non-positive budget enriches every listing given.
func FetchDetails(ctx context.Context, p *siteprofile.Profile, listings []store.Listing, budget int, delay time.Duration) DetailStats {
	st := DetailStats{Fields: map[string]int{}}
	if !p.Extract.Detail.Wanted() {
		return st
	}
	for i := range listings {
		if budget > 0 && st.Fetched >= budget {
			st.Skipped++
			continue
		}
		// No URL, nothing to fetch. That is a gap in the result-page profile,
		// not something a detail pass can paper over.
		if listings[i].URL == "" {
			st.Skipped++
			continue
		}
		body, err := FetchPageHTTP(ctx, listings[i].URL)
		st.Fetched++
		if err != nil {
			st.Failed++
			// A rate limit ends the whole pass, not just this listing. The
			// detail tier is the heaviest thing here - one request per listing
			// on top of the result pages - so it is the first thing that
			// should stop when a portal says it has had enough.
			var se *StatusError
			if errors.As(err, &se) && se.RateLimited() {
				st.RateLimited = true
				st.Skipped += len(listings) - i - 1
				return st
			}
			if delay > 0 {
				time.Sleep(Pace(delay))
			}
			continue
		}
		if filled := p.ApplyDetail(&listings[i], body); len(filled) > 0 {
			st.Enriched++
			for _, name := range filled {
				st.Fields[name]++
			}
		}
		if delay > 0 {
			time.Sleep(Pace(delay))
		}
	}
	return st
}
