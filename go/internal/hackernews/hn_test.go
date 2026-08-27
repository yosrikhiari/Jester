package hackernews

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func fixtureClient(t *testing.T, byURL map[string]string) *Client {
	t.Helper()
	return &Client{
		MinComments: 10,
		Get: func(ctx context.Context, u string) ([]byte, error) {
			for needle, body := range byURL {
				if strings.Contains(u, needle) {
					return []byte(body), nil
				}
			}
			return nil, fmt.Errorf("unexpected url %s", u)
		},
	}
}

func TestSearchURLCarriesTagLimitAndCommentFloor(t *testing.T) {
	u := New().SearchURL("ask_hn", 5)
	// search_by_date, not search: a scheduled read of a feed must be ordered
	// by date, or every tick re-fetches the same all-time-popular threads and
	// dedup drops the lot.
	if !strings.Contains(u, "/search_by_date") {
		t.Errorf("SearchURL must use the date-ordered endpoint: %s", u)
	}
	for _, want := range []string{"tags=ask_hn", "hitsPerPage=5", "num_comments%3E10"} {
		if !strings.Contains(u, want) {
			t.Errorf("SearchURL missing %q: %s", want, u)
		}
	}
	// A zero floor must not send an empty numericFilters that matches nothing.
	c := &Client{MinComments: 0}
	if strings.Contains(c.SearchURL("story", 3), "numericFilters") {
		t.Error("no comment floor should mean no numericFilters")
	}
}

func TestListStoriesReadsHits(t *testing.T) {
	c := fixtureClient(t, map[string]string{"search": `{"hits":[
		{"objectID":"111","title":"Ask HN: how do you back up configs","num_comments":42},
		{"objectID":"222","title":"Ask HN: worst on-call story","num_comments":13},
		{"objectID":"","title":"malformed","num_comments":99}]}`})
	got, err := c.ListStories(context.Background(), "ask_hn", 5)
	if err != nil {
		t.Fatalf("ListStories: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 usable stories, got %d", len(got))
	}
	if got[0].ID != "111" || got[0].NumComments != 42 {
		t.Errorf("first story mapped wrong: %+v", got[0])
	}
}

func TestListStoriesSaysSoWhenTheFeedIsEmpty(t *testing.T) {
	c := fixtureClient(t, map[string]string{"search": `{"hits":[]}`})
	if _, err := c.ListStories(context.Background(), "ask_hn", 5); err == nil {
		t.Fatal("an empty feed must be an error, not a silent zero-comment run")
	}
}

// Comments nest arbitrarily deep; a reply three levels down is still someone
// describing a problem, so the whole tree is walked.
func TestFetchCommentsFlattensTheWholeTree(t *testing.T) {
	c := fixtureClient(t, map[string]string{"items/111": `{
	  "id":111,"type":"story","title":"Ask HN",
	  "children":[
	    {"id":1,"text":"<p>top level pain</p>","author":"a","points":7,
	      "children":[{"id":2,"text":"nested reply","author":"b","points":null,
	        "children":[{"id":3,"text":"third level","author":"c"}]}]},
	    {"id":4,"text":"","author":"d"},
	    {"id":5,"text":"&amp;quoted &lt;b&gt;bold&lt;/b&gt;","author":"e"}
	  ]}`})
	got, story, err := c.FetchComments(context.Background(), "111")
	if err != nil {
		t.Fatalf("FetchComments: %v", err)
	}
	if len(got) != 4 {
		t.Fatalf("want 4 non-empty comments across the tree, got %d: %+v", len(got), got)
	}
	if got[0].Body != "top level pain" || got[0].Score != 7 {
		t.Errorf("html not stripped or score lost: %+v", got[0])
	}
	if got[2].Body != "third level" {
		t.Errorf("deep reply lost: %+v", got[2])
	}
	// R29: Algolia sends null points on EVERY comment — HN does not publish
	// per-comment scores. Score stays 0 as a neutral sort key, but Upvotes
	// must stay NIL: recording 0 there would assert "nobody upvoted this",
	// which the API never said.
	if got[1].Score != 0 {
		t.Errorf("null points must be 0, got %d", got[1].Score)
	}
	if got[1].Upvotes != nil {
		t.Errorf("null points must leave Upvotes unset, got %d", *got[1].Upvotes)
	}
	if got[0].Upvotes == nil || *got[0].Upvotes != 7 {
		t.Errorf("a published score must be recorded as upvotes: %+v", got[0].Upvotes)
	}
	if got[3].Body != `&quoted <b>bold</b>` {
		t.Errorf("entities not decoded: %q", got[3].Body)
	}
	// Ids are namespaced so an HN id can never collide with a Reddit thing id
	// in the shared fingerprint skip-list.
	if !strings.HasPrefix(got[0].ID, "hn:") {
		t.Errorf("id not namespaced: %q", got[0].ID)
	}

	// Everything the API publishes about WHO and WHERE, which the adapter
	// used to drop on the floor.
	if got[0].Author != "a" || got[2].Author != "c" {
		t.Errorf("author lost: %q / %q", got[0].Author, got[2].Author)
	}
	if got[0].Permalink != "https://news.ycombinator.com/item?id=1" {
		t.Errorf("permalink wrong: %q", got[0].Permalink)
	}
	// Depth comes from the walk, not a payload field: HN publishes the tree,
	// so position in it IS the depth.
	if got[0].Depth != 0 || got[1].Depth != 1 || got[2].Depth != 2 {
		t.Errorf("tree depth not derived: %d/%d/%d",
			got[0].Depth, got[1].Depth, got[2].Depth)
	}
	if got[0].Replies == nil || *got[0].Replies != 1 {
		t.Errorf("reply count lost: %+v", got[0].Replies)
	}

	// The story itself: previously discarded entirely, so a batch reached the
	// archive without the question its comments were answering.
	if story == nil {
		t.Fatal("the story must be captured, not dropped")
	}
	if story.Title != "Ask HN" {
		t.Errorf("story title lost: %q", story.Title)
	}
	if story.CommentCount == nil || *story.CommentCount != 5 {
		t.Errorf("story comment count wrong: %+v", story.CommentCount)
	}
	// No downvote figure exists anywhere in this payload, so none is invented.
	if story.Downvotes != nil {
		t.Errorf("HN publishes no downvotes; got %d", *story.Downvotes)
	}
}

func TestFetchCommentsRejectsAnEmptyID(t *testing.T) {
	if _, _, err := New().FetchComments(context.Background(), "  "); err == nil {
		t.Fatal("empty story id must be refused before any request")
	}
}

func TestSourceURLPointsAtTheThread(t *testing.T) {
	if got := SourceURL("111"); got != "https://news.ycombinator.com/item?id=111" {
		t.Errorf("SourceURL=%q", got)
	}
}

// Hacker News has no subreddits, so without a feed name every story it yields
// lands in one bucket named after the whole site — the grouped archive then
// says "2,923 nuggets, from Hacker News" and stops. Ask HN, Show HN and the
// front page are three different rooms.
func TestFeedNameAndURL(t *testing.T) {
	cases := map[string]struct{ name, url string }{
		"ask_hn":     {"Ask HN", "https://news.ycombinator.com/ask"},
		"show_hn":    {"Show HN", "https://news.ycombinator.com/show"},
		"front_page": {"HN front page", "https://news.ycombinator.com/news"},
	}
	for tag, want := range cases {
		if got := FeedName(tag); got != want.name {
			t.Errorf("FeedName(%q) = %q, want %q", tag, got, want.name)
		}
		if got := FeedURL(want.name); got != want.url {
			t.Errorf("FeedURL(%q) = %q, want %q", want.name, got, want.url)
		}
	}
	// A tag nobody mapped must produce nothing, not a plausible-looking label.
	// The caller then leaves the community alone rather than writing a guess
	// into the archive.
	if got := FeedName("who_knows"); got != "" {
		t.Errorf("an unmapped tag must not be named, got %q", got)
	}
	if got := FeedURL(""); got != "https://news.ycombinator.com/" {
		t.Errorf("unknown feed should fall back to the site root, got %q", got)
	}
}
