package discourse

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func fixtureClient(t *testing.T, byURL map[string]string) *Client {
	t.Helper()
	return &Client{
		MinPosts: 3,
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

func TestBaseURLNormalises(t *testing.T) {
	cases := map[string]string{
		"community.home-assistant.io":                "https://community.home-assistant.io",
		"https://community.home-assistant.io/":       "https://community.home-assistant.io",
		"https://community.home-assistant.io/latest": "https://community.home-assistant.io",
		"https://meta.discourse.org/latest.json":     "https://meta.discourse.org",
		"http://forum.local":                         "http://forum.local",
		"":                                           "",
	}
	for in, want := range cases {
		if got := BaseURL(in); got != want {
			t.Errorf("BaseURL(%q)=%q want %q", in, got, want)
		}
	}
}

func TestListTopicsSkipsPinnedAndQuietThreads(t *testing.T) {
	c := fixtureClient(t, map[string]string{"latest.json": `{"topic_list":{"topics":[
		{"id":1,"slug":"welcome","title":"Welcome","posts_count":1,"pinned":true},
		{"id":2,"slug":"busy-thread","title":"Busy","posts_count":32},
		{"id":3,"slug":"quiet","title":"Quiet","posts_count":2},
		{"id":4,"slug":"another","title":"Another","posts_count":9}]}}`})
	got, err := c.ListTopics(context.Background(), "https://forum.test", 10)
	if err != nil {
		t.Fatalf("ListTopics: %v", err)
	}
	// Pinned boilerplate ("welcome", "how to ask") is never someone describing
	// a problem, and a 2-post thread is not a discussion.
	if len(got) != 2 {
		t.Fatalf("want 2 topics, got %d: %+v", len(got), got)
	}
	if got[0].ID != 2 || got[0].Posts != 32 {
		t.Errorf("first topic mapped wrong: %+v", got[0])
	}
}

func TestListTopicsHonoursTheLimit(t *testing.T) {
	c := fixtureClient(t, map[string]string{"latest.json": `{"topic_list":{"topics":[
		{"id":2,"posts_count":10},{"id":3,"posts_count":10},{"id":4,"posts_count":10}]}}`})
	got, _ := c.ListTopics(context.Background(), "https://forum.test", 2)
	if len(got) != 2 {
		t.Fatalf("limit ignored: got %d", len(got))
	}
}

func TestListTopicsErrorsWhenNothingQualifies(t *testing.T) {
	c := fixtureClient(t, map[string]string{"latest.json": `{"topic_list":{"topics":[
		{"id":9,"posts_count":1}]}}`})
	if _, err := c.ListTopics(context.Background(), "https://forum.test", 5); err == nil {
		t.Fatal("no qualifying topics must be an error, not a silent empty run")
	}
}

func TestFetchPostsStripsCookedHTML(t *testing.T) {
	c := fixtureClient(t, map[string]string{"/t/2.json": `{"post_stream":{"posts":[
		{"id":10,"cooked":"<p>My backup script <b>breaks</b> every upgrade</p>","username":"a","score":90.7},
		{"id":11,"cooked":"<pre><code>rm -rf /</code></pre>","username":"b","score":1},
		{"id":12,"cooked":"<p>Line one</p><p>Line two</p>","username":"c","score":4}]}}`})
	got, err := c.FetchPosts(context.Background(), "https://forum.test", 2)
	if err != nil {
		t.Fatalf("FetchPosts: %v", err)
	}
	// The code-only post strips to nothing and is dropped rather than archived
	// as an empty comment.
	if len(got) != 2 {
		t.Fatalf("want 2 posts, got %d: %+v", len(got), got)
	}
	if got[0].Body != "My backup script breaks every upgrade" {
		t.Errorf("html not stripped: %q", got[0].Body)
	}
	if got[0].Score != 90 {
		t.Errorf("engagement score lost: %d", got[0].Score)
	}
	if got[1].Body != "Line one\nLine two" {
		t.Errorf("paragraph boundary lost: %q", got[1].Body)
	}
	if !strings.HasPrefix(got[0].ID, "discourse:") {
		t.Errorf("id not namespaced: %q", got[0].ID)
	}
}

func TestTopicURLIsReadableByAHuman(t *testing.T) {
	base := "https://forum.test"
	if got := TopicURL(base, Topic{ID: 7, Slug: "my-thread"}); got != "https://forum.test/t/my-thread/7" {
		t.Errorf("TopicURL=%q", got)
	}
	if got := TopicURL(base, Topic{ID: 7}); got != "https://forum.test/t/7" {
		t.Errorf("slugless TopicURL=%q", got)
	}
}
