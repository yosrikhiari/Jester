package main

import "testing"

// Hacker News and Discourse are read over plain HTTP. Routing them through a
// stealth browser would spawn a Chrome process per source for no benefit.
func TestNeedsBrowserOnlyForScrapedPlatforms(t *testing.T) {
	for _, p := range []string{"hackernews", "discourse"} {
		if needsBrowser(p) {
			t.Errorf("%s is an API read, it must not need a browser", p)
		}
	}
	for _, p := range []string{"reddit", "youtube", "tiktok", "something-new"} {
		if !needsBrowser(p) {
			t.Errorf("%s is scraped, it must go through cloakserve", p)
		}
	}
}

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
