package main

import (
	"strings"
	"testing"

	"jester/internal/config"
)

func TestHackerNewsTagFromURL(t *testing.T) {
	cases := map[string]string{
		"https://news.ycombinator.com/ask":  "ask_hn",
		"https://news.ycombinator.com/show": "show_hn",
		"https://news.ycombinator.com/news": "front_page",
		// Anything unrecognised falls back to the densest pain-signal feed
		// rather than erroring a curated source out of the run.
		"":         "ask_hn",
		"whatever": "ask_hn",
	}
	for in, want := range cases {
		if got := hackernewsTagFromURL(in); got != want {
			t.Errorf("hackernewsTagFromURL(%q)=%q want %q", in, got, want)
		}
	}
}

func TestHackerNewsIDFromURL(t *testing.T) {
	cases := map[string]string{
		"https://news.ycombinator.com/item?id=37392676": "37392676",
		"https://news.ycombinator.com/item?id=123&x=1":  "123",
		"https://news.ycombinator.com/ask":              "",
	}
	for in, want := range cases {
		if got := hackernewsIDFromURL(in); got != want {
			t.Errorf("hackernewsIDFromURL(%q)=%q want %q", in, got, want)
		}
	}
}

func TestDiscourseTopicFromURL(t *testing.T) {
	id, slug := discourseTopicFromURL("https://meta.discourse.org/t/some-slug/410718")
	if id != 410718 || slug != "some-slug" {
		t.Errorf("got id=%d slug=%q", id, slug)
	}
	// Slug is optional in Discourse's own routing.
	if id, _ := discourseTopicFromURL("https://forum.test/t/99"); id != 99 {
		t.Errorf("slugless topic id=%d want 99", id)
	}
	if id, _ := discourseTopicFromURL("https://forum.test/latest"); id != 0 {
		t.Errorf("a non-topic URL must yield 0, got %d", id)
	}
}

func TestDiscourseBaseFromTopicURL(t *testing.T) {
	got := discourseBaseFromTopicURL("https://meta.discourse.org/t/some-slug/410718")
	if got != "https://meta.discourse.org" {
		t.Errorf("base=%q", got)
	}
	// A root URL is already the base.
	if got := discourseBaseFromTopicURL("https://forum.test/"); got != "https://forum.test" {
		t.Errorf("base=%q", got)
	}
}

// needsBrowser used to default to TRUE and name the API-backed platforms as
// exceptions, so every platform added after that line inherited a
// stealth-browser session it had no use for. Steam is a plain keyless HTTP
// endpoint and still got a cloakserve Chrome spun up per source — one licence
// slot and several seconds each — before making an ordinary GET. Driving a
// browser is the expensive, legally-loaded, fingerprint-visible path, so it
// has to be asked for by name.
func TestOnlyScrapedPlatformsGetABrowser(t *testing.T) {
	for _, p := range []string{"reddit", "youtube", "tiktok"} {
		if !needsBrowser(p) {
			t.Errorf("%s is a DOM scrape and needs the browser", p)
		}
	}
	for _, p := range []string{
		"hackernews", "discourse", "stackexchange", "github", "lemmy", "steam",
	} {
		if needsBrowser(p) {
			t.Errorf("%s is a plain HTTP API and must not open a browser session", p)
		}
	}
	// The DEFAULT is what actually matters, and it was deliberately the other
	// way round: an unclassified platform used to get a browser on the theory
	// that assuming "scraped" is the safe assumption. It is not, because a
	// platform with no entry here has no adapter either — fetchSource rejects
	// it and the browser was opened for nothing. The only platforms the
	// default ever reaches are ones that HAVE an adapter and were left off the
	// list, which is exactly how Steam ended up driving Chrome to make a
	// keyless GET.
	if needsBrowser("some-future-api") {
		t.Error("an unclassified platform must default to no browser")
	}
}

// ── backfill (A12) ───────────────────────────────────────────────────────────

func TestParseUntilRefusesAJunkDate(t *testing.T) {
	// Zero means "no boundary at all", so defaulting a typo to zero would
	// silently walk the entire archive instead of stopping where asked.
	if _, err := parseUntil("2024-13-99"); err == nil {
		t.Error("an impossible date must be refused")
	}
	if _, err := parseUntil("last tuesday"); err == nil {
		t.Error("prose must be refused")
	}
	got, err := parseUntil("2024-01-01")
	if err != nil || got != 1704067200 {
		t.Errorf("2024-01-01 = %d (%v), want 1704067200", got, err)
	}
	if got, err := parseUntil("  "); err != nil || got != 0 {
		t.Errorf("an empty boundary means no boundary: %d %v", got, err)
	}
}

func TestBackfillRefusesPlatformsItCannotWalk(t *testing.T) {
	sources := []config.Source{
		{Name: "hn-ask", Platform: "hackernews", Kind: "feed", URL: "https://news.ycombinator.com/ask"},
		{Name: "aseprite", Platform: "steam", Kind: "app", URL: "https://store.steampowered.com/app/431730/"},
		{Name: "hn-thread", Platform: "hackernews", Kind: "story", URL: "https://news.ycombinator.com/item?id=1"},
	}
	// Naming the wrong platform must say so, not walk it badly.
	if _, err := findSource(sources, "nope"); err == nil {
		t.Error("an unknown source must be refused")
	} else if !strings.Contains(err.Error(), "hn-ask") {
		// A bare "not found" against a 93-entry list is a guessing game.
		t.Errorf("the error should list what IS backfillable: %v", err)
	}
	if s, err := findSource(sources, "aseprite"); err != nil || s.Platform != "steam" {
		t.Errorf("findSource should still resolve it; the platform check is separate: %v", err)
	}
}

func TestStampNeverClaims1970(t *testing.T) {
	// 0 is the "no timestamp published" value, and printing 1970-01-01 for it
	// would be a date the page never gave.
	if got := stamp(0); got != "unknown" {
		t.Errorf("stamp(0) = %q, want unknown", got)
	}
	if got := stamp(-5); got != "unknown" {
		t.Errorf("stamp(-5) = %q, want unknown", got)
	}
	if got := stamp(1704067200); got != "2024-01-01" {
		t.Errorf("stamp = %q", got)
	}
}
