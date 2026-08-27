package main

import "testing"

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
