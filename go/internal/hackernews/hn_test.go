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
	got, err := c.FetchComments(context.Background(), "111")
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
	// R29: Algolia sends null points on comments — absent means 0, not a guess.
	if got[1].Score != 0 {
		t.Errorf("null points must be 0, got %d", got[1].Score)
	}
	if got[3].Body != `&quoted <b>bold</b>` {
		t.Errorf("entities not decoded: %q", got[3].Body)
	}
	// Ids are namespaced so an HN id can never collide with a Reddit thing id
	// in the shared fingerprint skip-list.
	if !strings.HasPrefix(got[0].ID, "hn:") {
		t.Errorf("id not namespaced: %q", got[0].ID)
	}
}

func TestFetchCommentsRejectsAnEmptyID(t *testing.T) {
	if _, err := New().FetchComments(context.Background(), "  "); err == nil {
		t.Fatal("empty story id must be refused before any request")
	}
}

func TestSourceURLPointsAtTheThread(t *testing.T) {
	if got := SourceURL("111"); got != "https://news.ycombinator.com/item?id=111" {
		t.Errorf("SourceURL=%q", got)
	}
}
