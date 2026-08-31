// Package siteprobe classifies a candidate portal before anyone writes an
// adapter for it.
//
// WHY. `forumprobe` already made this argument for Discourse forums: "adding a
// forum is one config line, which makes it tempting to add forty from memory.
// Half of them would be dead." The same trap with property portals is more
// expensive, because the wrong guess is not a dead source — it is a
// hand-written adapter against a site that was never reachable this way.
//
// WHAT IT DECIDES. One plain HTTP GET per portal answers the only question
// that changes the cost of an adapter by an order of magnitude: does this site
// answer an ordinary client at all, or does it need the stealth browser? There
// is exactly one CloakBrowser session for every portal on the roster, so every
// portal that can skip it is throughput nobody has to ration.
//
// WHAT IT DELIBERATELY DOES NOT DO. No crawling, no pagination, no listing
// extraction, no images. One request to one URL, with a real Accept header and
// a normal timeout. That is the lightest touch that still answers the
// question, and it keeps the probe itself from being the thing that gets an
// unproxied residential IP noticed.
package siteprobe

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// Tier is the fetch mechanism a portal will need, which is the axis that
// actually predicts adapter cost — unlike the region it happens to serve.
type Tier string

const (
	// TierJSON: a machine-readable endpoint is reachable. Cheapest by far.
	TierJSON Tier = "json"
	// TierHTML: ordinary HTTP returns the markup. Needs selectors, no browser.
	TierHTML Tier = "html"
	// TierWalled: a bot wall answered. Competes for the single browser session.
	TierWalled Tier = "walled"
	// TierAuth: reachable but gated behind a login or paywall.
	TierAuth Tier = "auth"
	// TierDead: no useful response at all.
	TierDead Tier = "dead"
)

// Result is one portal's classification plus the evidence for it.
type Result struct {
	Name       string
	URL        string
	Tier       Tier
	Status     int
	Server     string
	Wall       string // named bot-detection vendor, when one identified itself
	Bytes      int
	Elapsed    time.Duration
	NextJSON   bool   // a __NEXT_DATA__/embedded JSON blob is present
	ImageHosts []string
	Note       string
	Err        error
}

// wallSignatures maps a marker in the response to the vendor it belongs to.
//
// Naming the wall matters more than detecting one: Cloudflare's managed
// challenge and a DataDome block call for different responses, and "blocked"
// on its own tells the operator nothing they can act on.
var wallSignatures = []struct {
	marker string
	vendor string
}{
	{"cf-mitigated", "Cloudflare"},
	{"cf-chl-", "Cloudflare challenge"},
	{"just a moment", "Cloudflare challenge"},
	{"datadome", "DataDome"},
	{"px-captcha", "PerimeterX/HUMAN"},
	{"_px", "PerimeterX/HUMAN"},
	{"perimeterx", "PerimeterX/HUMAN"},
	{"kasada", "Kasada"},
	{"kpsdk", "Kasada"},
	{"incapsula", "Imperva/Incapsula"},
	{"_imp_apg_r_", "Imperva/Incapsula"},
	{"access denied", "generic block page"},
	{"are you a robot", "generic bot check"},
	{"unusual traffic", "generic rate block"},
}

var (
	// Image URLs in markup, enough to name the CDN host rather than to build a
	// gallery extractor.
	imgSrcRe = regexp.MustCompile(`(?i)(?:src|data-src|content)=["'](https?://[^"']+\.(?:jpe?g|png|webp|avif)[^"']*)["']`)
	hostRe   = regexp.MustCompile(`^https?://([^/]+)`)
	nextRe   = regexp.MustCompile(`(?i)(__NEXT_DATA__|application/ld\+json|window\.__INITIAL_STATE__|__NUXT__)`)
	authRe   = regexp.MustCompile(`(?i)(sign in to continue|please log ?in to view|subscribe to continue|create a free account to)`)
)

// Probe issues one GET and classifies what comes back.
//
// The Accept header asks for JPEG ahead of WebP deliberately. Go's standard
// library decodes JPEG and PNG but not WebP (see internal/mediahash), and many
// CDNs content-negotiate — so the header a fingerprinting fetcher would send in
// production is the header the probe should test with, or the probe reports a
// capability the real fetcher will not have.
func Probe(ctx context.Context, client *http.Client, name, rawURL string) Result {
	res := Result{Name: name, URL: rawURL}
	start := time.Now()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		res.Tier, res.Err = TierDead, err
		return res
	}
	// A plausible desktop browser. Not stealth — the point is to learn what an
	// ordinary client is told, and a deliberately odd agent would answer a
	// different question.
	req.Header.Set("User-Agent",
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/json;q=0.9,image/jpeg;q=0.8,*/*;q=0.5")
	req.Header.Set("Accept-Language", "en-GB,en;q=0.9")

	resp, err := client.Do(req)
	if err != nil {
		res.Tier, res.Err, res.Elapsed = TierDead, err, time.Since(start)
		return res
	}
	defer resp.Body.Close()

	// Bounded read: enough markup to classify, never a full page download.
	// A probe that pulls megabytes from 26 hosts is no longer a light touch.
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 512*1024))
	res.Elapsed = time.Since(start)
	res.Status = resp.StatusCode
	res.Bytes = len(body)
	res.Server = resp.Header.Get("Server")

	lower := strings.ToLower(string(body))
	headers := strings.ToLower(fmt.Sprint(resp.Header))
	res.Wall = detectWall(lower, headers)
	res.NextJSON = nextRe.MatchString(string(body))
	res.ImageHosts = imageHosts(string(body))

	ct := strings.ToLower(resp.Header.Get("Content-Type"))
	switch {
	case res.Wall != "" || res.Status == 403 || res.Status == 429 || res.Status == 503:
		res.Tier = TierWalled
		if res.Wall == "" {
			res.Wall = fmt.Sprintf("unnamed, HTTP %d", res.Status)
		}
	case res.Status >= 400:
		res.Tier = TierDead
		res.Note = fmt.Sprintf("HTTP %d", res.Status)
	case strings.Contains(ct, "json"):
		res.Tier = TierJSON
	case authRe.MatchString(lower):
		res.Tier = TierAuth
	case res.Bytes < 2048:
		// A 200 with almost nothing in it is a shell that renders client-side,
		// which for our purposes is the same problem as a wall: an ordinary
		// GET does not produce listings.
		res.Tier = TierWalled
		res.Note = "200 but near-empty body — client-rendered shell"
	default:
		res.Tier = TierHTML
		if res.NextJSON {
			// Still HTML tier, but the note is the single most useful thing the
			// probe can hand an adapter author: the data is already structured
			// inside the page, so no selector archaeology is needed.
			res.Note = "embedded JSON blob present"
		}
	}
	return res
}

func detectWall(body, headers string) string {
	for _, s := range wallSignatures {
		if strings.Contains(headers, s.marker) || strings.Contains(body, s.marker) {
			return s.vendor
		}
	}
	return ""
}

// imageHosts returns the distinct hosts serving images, most useful first.
//
// This is what decides whether photographs can come down a fast parallel lane
// while the page itself goes through the browser — the single biggest lever on
// the one-session ceiling.
func imageHosts(body string) []string {
	seen := map[string]bool{}
	var out []string
	for _, m := range imgSrcRe.FindAllStringSubmatch(body, 200) {
		h := hostRe.FindStringSubmatch(m[1])
		if h == nil || seen[h[1]] {
			continue
		}
		seen[h[1]] = true
		out = append(out, h[1])
		if len(out) == 4 {
			break
		}
	}
	return out
}
