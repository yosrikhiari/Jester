// Command renderprobe fetches ONE url through cloakserve and writes the
// rendered DOM to a file.
//
//	go run ./cmd/renderprobe -url https://example.test/search -out page.html
//
// WHY THIS EXISTS ALONGSIDE siteprobe. siteprobe makes one plain GET per
// portal and says so in its own doc: it names the bot wall, the image CDN and
// whether the markup already carries structured data, and explicitly does not
// answer what a page looks like once its JavaScript has run. For a portal that
// renders client-side that is the only question that matters - Fotocasa,
// OnTheMarket and Bien'ici all answer 200 to a plain GET and hand back a shell
// with no listings in it, so there is nothing to write a profile against until
// something executes the page.
//
// It is a probe, not a scraper: one URL, one fetch, no pagination, no parsing.
// Point it at a search page, read the DOM it writes, then write the profile.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	"jester/internal/cloakbrowser"
	"jester/internal/config"
	"jester/internal/realestate"
)

func main() {
	url := flag.String("url", "", "the page to render (required)")
	out := flag.String("out", "rendered.html", "where to write the DOM")
	configDir := flag.String("config", "../config", "config directory, for the CDP endpoint")
	delay := flag.Duration("delay", 2*time.Second, "settle time after load")
	scroll := flag.Int("scroll", 0, "scroll rounds for a lazy-loading list")
	flag.Parse()

	if *url == "" {
		fmt.Fprintln(os.Stderr, "renderprobe: -url is required")
		os.Exit(2)
	}
	cfg, err := config.Load(*configDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "renderprobe: config: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("cdp %s\n", cfg.Scraper.CDPURL)

	// Opened exactly the way the worker opens one - same seed scheme, same
	// fingerprint options - so what this writes is what a real run would see
	// rather than what a bare Chrome would.
	cb := cloakbrowser.NewWithOptions(cfg.Scraper.CDPURL, cloakbrowser.Options{
		Fingerprint: cloakbrowser.SeedFor("realestate", "renderprobe"),
		Timezone:    cfg.Scraper.Timezone,
		Locale:      cfg.Scraper.Locale,
		Proxy:       cfg.Scraper.Proxy,
		GeoIP:       cfg.Scraper.GeoIPEnabled(),
	})
	sess, err := cb.NewSession(context.Background())
	if err != nil {
		fmt.Fprintf(os.Stderr, "renderprobe: cdp session: %v\n", err)
		os.Exit(1)
	}
	defer sess.Cancel()
	ctx := sess.Ctx

	body, err := realestate.FetchRenderedPage(ctx, *url, *delay, *scroll)
	if err != nil {
		fmt.Fprintf(os.Stderr, "renderprobe: %v\n", err)
		os.Exit(1)
	}
	if err := os.WriteFile(*out, []byte(body), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "renderprobe: write: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("wrote %d bytes to %s\n", len(body), *out)
}
