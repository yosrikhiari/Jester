package discourse

import (
	"context"
	"encoding/json"
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

// Pagination: /latest.json serves 30 topics a page and advertises the next in
// more_topics_url. Reading one page and stopping capped this adapter at 18
// qualifying topics on meta.discourse.org — measured, and silent: asking for
// 100 or 300 returned the same 18.
func TestListTopicsWalksPastTheFirstPage(t *testing.T) {
	page := func(ids []int, more string) string {
		out := `{"topic_list":{"topics":[`
		for i, id := range ids {
			if i > 0 {
				out += ","
			}
			out += fmt.Sprintf(`{"id":%d,"posts_count":50}`, id)
		}
		out += `],"more_topics_url":"` + more + `"}}`
		return out
	}
	c := fixtureClient(t, map[string]string{
		"page=0": page([]int{1, 2, 3, 4, 5}, "/latest?page=1"),
		"page=1": page([]int{6, 7, 8, 9, 10}, "/latest?page=2"),
		"page=2": page([]int{11, 12}, ""),
	})
	got, err := c.ListTopics(context.Background(), "https://forum.test", 12)
	if err != nil {
		t.Fatalf("ListTopics: %v", err)
	}
	if len(got) != 12 {
		t.Fatalf("want 12 across three pages, got %d", len(got))
	}
}

func TestListTopicsStopsWhenTheForumSaysThereIsNoMore(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"page=0": `{"topic_list":{"topics":[{"id":1,"posts_count":50}],"more_topics_url":""}}`,
	})
	got, err := c.ListTopics(context.Background(), "https://forum.test", 100)
	if err != nil {
		t.Fatalf("ListTopics: %v", err)
	}
	// Asking for 100 from a one-page forum returns what exists, and does not
	// keep hammering pages that will never come.
	if len(got) != 1 {
		t.Fatalf("want the single available topic, got %d", len(got))
	}
}

func TestALaterPageFailingKeepsWhatEarlierPagesYielded(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"page=0": `{"topic_list":{"topics":[{"id":1,"posts_count":50},{"id":2,"posts_count":50}],"more_topics_url":"/latest?page=1"}}`,
		// page=1 deliberately absent -> the fixture getter errors on it
	})
	got, err := c.ListTopics(context.Background(), "https://forum.test", 50)
	if err != nil {
		t.Fatalf("a mid-walk failure must not lose the whole call: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want the 2 topics page 0 gave us, got %d", len(got))
	}
}

// Discourse discussions quote each other constantly. Left in, the quoted words
// are embedded as though the quoting poster had said them — so a thread agrees
// with itself and a cluster forms from one sentence repeated by three people.
// Observed live: a cluster whose three "independent" members were one original
// post and two others quoting it.
func TestQuotedTextIsNotAttributedToTheQuoter(t *testing.T) {
	cooked := `<aside class="quote no-group" data-username="Jagster" data-post="2">` +
		`<div class="title">Jagster:</div>` +
		`<blockquote><p>You will not need a persistent last_changed.</p></blockquote>` +
		`</aside>` +
		`<p>I disagree, my dashboards break without it.</p>`
	c := fixtureClient(t, map[string]string{
		"/t/2.json": `{"id":2,"post_stream":{"posts":[` +
			`{"id":10,"cooked":` + jsonString(cooked) + `,"username":"a","score":5}]}}`,
	})
	got, _, err := c.FetchPosts(context.Background(), "https://forum.test", 2)
	if err != nil {
		t.Fatalf("FetchPosts: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 post, got %d", len(got))
	}
	body := got[0].Body
	if !strings.Contains(body, "dashboards break") {
		t.Errorf("the poster's own words were lost: %q", body)
	}
	if strings.Contains(body, "persistent last_changed") {
		t.Errorf("quoted text survived and will be embedded as this poster's: %q", body)
	}
	if strings.Contains(body, "Jagster") {
		t.Errorf("quote attribution survived: %q", body)
	}
}

func TestAPostThatIsOnlyAQuoteIsDropped(t *testing.T) {
	cooked := `<aside class="quote" data-username="x"><blockquote><p>agreed</p></blockquote></aside>`
	c := fixtureClient(t, map[string]string{
		"/t/2.json": `{"id":2,"post_stream":{"posts":[` +
			`{"id":10,"cooked":` + jsonString(cooked) + `,"username":"a"}]}}`,
	})
	got, _, err := c.FetchPosts(context.Background(), "https://forum.test", 2)
	if err != nil {
		t.Fatalf("FetchPosts: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("a post with nothing of its own to say must be dropped, got %+v", got)
	}
}

func TestTwoQuotesInOnePostDoNotSwallowTheMiddle(t *testing.T) {
	// A greedy regex here would delete everything between the first <aside>
	// and the last </aside>, taking the poster's reply with it.
	cooked := `<aside class="quote"><blockquote>first quoted</blockquote></aside>` +
		`<p>my actual point</p>` +
		`<aside class="quote"><blockquote>second quoted</blockquote></aside>` +
		`<p>and my conclusion</p>`
	c := fixtureClient(t, map[string]string{
		"/t/2.json": `{"id":2,"post_stream":{"posts":[` +
			`{"id":10,"cooked":` + jsonString(cooked) + `,"username":"a"}]}}`,
	})
	got, _, _ := c.FetchPosts(context.Background(), "https://forum.test", 2)
	if len(got) != 1 {
		t.Fatalf("want 1 post, got %d", len(got))
	}
	for _, want := range []string{"my actual point", "and my conclusion"} {
		if !strings.Contains(got[0].Body, want) {
			t.Errorf("lost %q from between two quotes: %q", want, got[0].Body)
		}
	}
	for _, unwanted := range []string{"first quoted", "second quoted"} {
		if strings.Contains(got[0].Body, unwanted) {
			t.Errorf("quote %q survived: %q", unwanted, got[0].Body)
		}
	}
}

func TestLegacyBBCodeQuotesAreStrippedToo(t *testing.T) {
	cooked := `[quote="busman, post:11, topic:1010612"]you will not need this[/quote] but I do`
	c := fixtureClient(t, map[string]string{
		"/t/2.json": `{"id":2,"post_stream":{"posts":[` +
			`{"id":10,"cooked":` + jsonString(cooked) + `,"username":"a"}]}}`,
	})
	got, _, _ := c.FetchPosts(context.Background(), "https://forum.test", 2)
	if len(got) != 1 {
		t.Fatalf("want 1 post, got %d", len(got))
	}
	if strings.Contains(got[0].Body, "you will not need this") {
		t.Errorf("bbcode quote survived: %q", got[0].Body)
	}
	if !strings.Contains(got[0].Body, "but I do") {
		t.Errorf("own words lost: %q", got[0].Body)
	}
}

// jsonString quotes a string for embedding in a JSON fixture.
func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func TestStripQuotesDirectly(t *testing.T) {
	in := `<aside class="quote"><blockquote><p>agreed</p></blockquote></aside>`
	out := stripQuotes(in)
	t.Logf("in  = %q", in)
	t.Logf("out = %q", out)
	if strings.Contains(out, "agreed") {
		t.Errorf("stripQuotes did not strip: %q", out)
	}
}

func TestBlockquoteIsNotMistakenForAQuoteAside(t *testing.T) {
	// `blockquote` contains the letters "quote". A substring match here would
	// delete an ordinary quoted-code or emphasis block that the poster wrote
	// themselves — which is why the class is matched as a whole token.
	in := `<aside class="blockquote-styling"><p>my own words</p></aside>`
	if got := stripQuotes(in); !strings.Contains(got, "my own words") {
		t.Errorf("a non-quote aside was stripped: %q", got)
	}
	// …and the real thing still goes.
	real := `<aside class="quote no-group"><blockquote>theirs</blockquote></aside>`
	if got := stripQuotes(real); strings.Contains(got, "theirs") {
		t.Errorf("a real quote survived: %q", got)
	}
}
