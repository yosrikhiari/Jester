package realestate

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"jester/internal/siteprofile"
)

// UserAgent is what the HTTP fetcher identifies as. Portals serve a different
// document to a client they do not recognise as a browser - often a consent
// interstitial with no listings in it - so a bare Go default would parse to
// zero results and look like an empty portal.
const UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

// httpClient follows redirects, which is not incidental. The shipped
// privateproperty URL answers 301 and the body at the original address is a
// 182-byte "Object moved" stub: parsed without following, it yields nothing,
// and the portal reads as having no listings.
var httpClient = &http.Client{Timeout: 45 * time.Second}

// StatusError is a non-200 response. It is a distinct type because the
// difference between codes is operational, not cosmetic: 404 means the walk
// has run off the end of the results and the portal is finished, while 5xx is
// usually nothing at all and is worth asking again.
type StatusError struct {
	URL  string
	Code int
	// RetryAfter is the portal's own instruction, when it sends one.
	RetryAfter time.Duration
}

func (e *StatusError) Error() string { return fmt.Sprintf("get %s: status %d", e.URL, e.Code) }

// Exhausted reports whether the status means "there is no such page", which is
// how a paginated portal says stop.
func (e *StatusError) Exhausted() bool {
	return e.Code == http.StatusNotFound || e.Code == http.StatusGone
}

// RateLimited reports whether the portal is asking us to slow down or stop.
//
// This is not a transient flap to be retried away. Property24 began answering
// 503 partway through a session that had pulled twenty result pages, some two
// thousand four hundred photographs and a run of detail pages from it - the
// portal was right and the client was wrong. Retrying a rate limit at the same
// pace is how a scraper turns a warning into a ban.
func (e *StatusError) RateLimited() bool {
	// 202 belongs here even though it is a 2xx. Redfin answers "202 Accepted"
	// with no listing content when it decides a client is walking too deep -
	// seven of ten page-2 requests during this build - which is a soft block
	// wearing a success code. Treating it as anything else means walking into
	// it repeatedly.
	return e.Code == http.StatusTooManyRequests ||
		e.Code == http.StatusServiceUnavailable ||
		e.Code == http.StatusAccepted
}

// retryAttempts bounds how many times a transient failure is re-asked.
// Tayara answered one 500 during a harvest and contributed nothing for the
// whole run; three retries later, by hand, it answered 200 three times out of
// three. One flap should not cost a portal.
const retryAttempts = 3

// FetchPageHTTP reads a listing page with an ordinary GET, retrying a
// transient failure a bounded number of times.
func FetchPageHTTP(ctx context.Context, url string) (string, error) {
	var lastErr error
	for attempt := 1; attempt <= retryAttempts; attempt++ {
		body, err := fetchOnce(ctx, url)
		if err == nil {
			return body, nil
		}
		lastErr = err
		// A 4xx is an answer, not a flap: asking again just repeats the
		// request at a portal that already said no.
		var se *StatusError
		if errors.As(err, &se) && se.Code < 500 {
			return "", err
		}
		if attempt < retryAttempts {
			back := time.Duration(attempt) * 2 * time.Second
			// A portal that says "too many" gets a much longer pause than a
			// portal that merely stumbled, and its own Retry-After wins over
			// any number chosen here.
			if errors.As(err, &se) && se.RateLimited() {
				back = time.Duration(attempt) * 30 * time.Second
				if se.RetryAfter > 0 {
					back = se.RetryAfter
				}
			}
			fmt.Printf("[live]   %s failed (%v), retrying in %s\n", url, err, back)
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(back):
			}
		}
	}
	return "", lastErr
}

// fetchOnce performs a single GET.
//
// WHY THIS EXISTS. Every real-estate source used to require cloakserve, a
// licensed commercial container, because the browser requirement was attached
// to the PLATFORM. It is a property of the portal: Property24, Private
// Property and Tayara all serve their listings - Tayara's whole __NEXT_DATA__
// included - to a plain GET. needsBrowser in the worker already argues the
// principle ("driving a browser is the expensive, legally-loaded,
// fingerprint-visible path, it should have to be asked for by name"); this
// lets a profile decline it by name.
func fetchOnce(ctx context.Context, url string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", fmt.Errorf("build request for %s: %w", url, err)
	}
	req.Header.Set("User-Agent", UserAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
	req.Header.Set("Accept-Language", "en-GB,en;q=0.9,fr;q=0.8")

	resp, err := httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("get %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		se := &StatusError{URL: url, Code: resp.StatusCode}
		// Retry-After is the portal stating its own terms; honouring it is
		// both the polite and the effective reading.
		if v := resp.Header.Get("Retry-After"); v != "" {
			if secs, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && secs > 0 {
				se.RetryAfter = time.Duration(secs) * time.Second
			}
		}
		return "", se
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("read %s: %w", url, err)
	}
	if len(body) == 0 {
		return "", fmt.Errorf("empty body for %s", url)
	}
	body = toUTF8(body)
	// A redirect that lands somewhere else is worth saying out loud rather
	// than silently scraping: the shipped privateproperty search claimed Cape
	// Town and resolved to Durban.
	if final := resp.Request.URL.String(); final != url {
		fmt.Printf("[live]   %s redirected to %s\n", url, final)
	}
	return string(body), nil
}

// toUTF8 converts a response body that is not valid UTF-8.
//
// WHY. Tunisie Annonce answers `Content-Type: text/html` with NO charset -
// legacy ASP, so the bytes are Windows-1252 - while every other portal here
// declares UTF-8. Stored raw, those bytes are not text: SQLite accepted them
// and then Python's driver refused the column outright
// ("Could not decode to UTF-8 column 'url'"), which took the ENTIRE listings
// export to zero rows. One portal's accented "meublé" silently deleted five
// other portals' data from a CSV.
//
// Windows-1252 rather than plain Latin-1 because the two differ in 0x80-0x9F,
// where cp1252 puts the smart quotes and dashes that a French classifieds page
// is full of. Done by hand, with a 32-entry table, rather than by adding
// golang.org/x/text/encoding for it.
func toUTF8(body []byte) []byte {
	if utf8.Valid(body) {
		return body
	}
	var b strings.Builder
	b.Grow(len(body) * 2)
	for _, c := range body {
		switch {
		case c < 0x80:
			b.WriteByte(c)
		case c >= 0xA0:
			// Latin-1 range: the byte IS the code point.
			b.WriteRune(rune(c))
		default:
			if r := cp1252High[c-0x80]; r != 0 {
				b.WriteRune(r)
			} else {
				b.WriteRune(utf8.RuneError)
			}
		}
	}
	return []byte(b.String())
}

// cp1252High maps bytes 0x80-0x9F, the range where Windows-1252 and Latin-1
// disagree. A zero entry is undefined in cp1252 and becomes U+FFFD.
var cp1252High = [32]rune{
	0x20AC, 0, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
	0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017D, 0,
	0, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
	0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0, 0x017E, 0x0178,
}

// UsesHTTPFetch reports whether the named portal's profile declines the
// browser. A profile that cannot be read is not an HTTP profile: the caller
// keeps its existing behaviour rather than quietly downgrading the fetch.
func UsesHTTPFetch(configDir, name string) bool {
	b, err := os.ReadFile(filepath.Join(configDir, "profiles", name+".yaml"))
	if err != nil {
		return false
	}
	p, err := siteprofile.Load(b)
	if err != nil {
		return false
	}
	return strings.EqualFold(p.Fetch, "http")
}
