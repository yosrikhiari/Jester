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
	c := fixtureClient(t, map[string]string{"/t/2.json": `{
	  "id":2,"title":"Backups keep breaking","slug":"backups-keep-breaking",
	  "created_at":"2026-08-10T15:54:59.367Z","posts_count":3,"views":650,
	  "like_count":84,"participant_count":10,"tags":["backup"],
	  "post_stream":{"posts":[
		{"id":10,"cooked":"<p>My backup script <b>breaks</b> every upgrade</p>","username":"a","score":90.7,
		 "created_at":"2026-08-10T15:54:59.657Z","post_number":1,"reply_count":2,"reads":79,
		 "actions_summary":[{"id":2,"count":18}],"version":1},
		{"id":11,"cooked":"<pre><code>rm -rf /</code></pre>","username":"b","score":1},
		{"id":12,"cooked":"<p>Line one</p><p>Line two</p>","username":"c","score":4,
		 "reply_to_post_number":1,"version":3,"staff":true}]}}`})
	got, topic, err := c.FetchPosts(context.Background(), "https://forum.test", 2)
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

	// Everything else the topic JSON has always carried and the adapter used
	// to throw away at the point of capture.
	if got[0].Author != "a" || got[0].CreatedAt != "2026-08-10T15:54:59Z" {
		t.Errorf("author/created lost: %q %q", got[0].Author, got[0].CreatedAt)
	}
	// actions_summary id 2 IS the like count.
	if got[0].Likes == nil || *got[0].Likes != 18 {
		t.Errorf("like count lost: %+v", got[0].Likes)
	}
	if got[0].Reads == nil || *got[0].Reads != 79 {
		t.Errorf("read count lost: %+v", got[0].Reads)
	}
	if !got[0].AuthorIsOP {
		t.Error("post_number 1 is the opening post, i.e. the OP")
	}
	if got[1].ParentID != "1" || got[1].Depth != 1 {
		t.Errorf("reply linkage lost: parent=%q depth=%d", got[1].ParentID, got[1].Depth)
	}
	if !got[1].Edited {
		t.Error("version 3 means the post was edited twice")
	}
	if !got[1].Distinguished {
		t.Error("a staff post is distinguished")
	}
	// Discourse ships no downvote action, so nothing may claim one.
	if got[0].Dislikes != nil || got[0].Downvotes != nil {
		t.Error("discourse publishes no dislikes/downvotes; none may be recorded")
	}

	if topic == nil {
		t.Fatal("the topic must be captured, not dropped")
	}
	if topic.Title != "Backups keep breaking" {
		t.Errorf("topic title lost: %q", topic.Title)
	}
	if topic.Views == nil || *topic.Views != 650 {
		t.Errorf("topic views lost: %+v", topic.Views)
	}
	if topic.Likes == nil || *topic.Likes != 84 {
		t.Errorf("topic likes lost: %+v", topic.Likes)
	}
	if len(topic.Tags) != 1 || topic.Tags[0] != "backup" {
		t.Errorf("topic tags lost: %+v", topic.Tags)
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
