package github

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func fixtureClient(t *testing.T, byURL map[string]string) *Client {
	t.Helper()
	return &Client{Get: func(_ context.Context, u string) ([]byte, error) {
		// LONGEST match wins, deterministically. The comments URL
		// (/repos/a/b/issues/5/comments) contains the issue key
		// (/issues/5) as a substring, so a first-match-wins loop over a Go
		// map returns whichever the runtime happened to iterate first — and
		// the test then fails at random.
		best, bestLen := "", -1
		for frag, body := range byURL {
			if strings.Contains(u, frag) && len(frag) > bestLen {
				best, bestLen = body, len(frag)
			}
		}
		if bestLen >= 0 {
			return []byte(best), nil
		}
		return nil, fmt.Errorf("no fixture for %s", u)
	}, MinComments: 0}
}

func TestRepoFromURLHandlesEveryShapeAConfigMightHold(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"rclone/rclone", "rclone/rclone"},
		{"https://github.com/rclone/rclone", "rclone/rclone"},
		{"https://github.com/rclone/rclone/", "rclone/rclone"},
		{"github.com/n8n-io/n8n", "n8n-io/n8n"},
		{"https://github.com/home-assistant/core/issues/151223", "home-assistant/core"},
		{"nonsense", ""},
		{"", ""},
	} {
		if got := RepoFromURL(tc.in); got != tc.want {
			t.Errorf("RepoFromURL(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// The property that makes this source unique in the whole pipeline: a real,
// published downvote. Reddit stopped exposing per-comment downvotes in 2014,
// YouTube withdrew dislikes in 2021, Hacker News never had them and Discourse
// has none — so Downvotes has been nil for every other adapter.
func TestNegativeReactionsBecomeRealDownvotes(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/issues/151223": `{"number":151223,"title":"Upcoming API changes",
			"body":"The vendor is changing the API and every integration breaks.",
			"state":"open","comments":806,"created_at":"2025-08-27T07:52:43Z",
			"html_url":"https://github.com/home-assistant/core/issues/151223",
			"reactions":{"total_count":199,"+1":9,"-1":177,"confused":11},
			"user":{"login":"tado-com","html_url":"https://github.com/tado-com"},
			"labels":[{"name":"integration: tado"}]}`,
		"/issues/151223/comments": `[]`,
	})
	got, post, err := c.FetchIssue(context.Background(), "home-assistant/core", 151223)
	if err != nil {
		t.Fatalf("FetchIssue: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want the issue body, got %d items", len(got))
	}
	if got[0].Downvotes == nil || *got[0].Downvotes != 177 {
		t.Fatalf("downvotes lost: %+v", got[0].Downvotes)
	}
	if got[0].Upvotes == nil || *got[0].Upvotes != 9 {
		t.Errorf("upvotes lost: %+v", got[0].Upvotes)
	}
	// Score is the NET, which is what a ranker should sort on: 177 people
	// objecting outweighs 9 agreeing, and a positive score here would be a lie.
	if got[0].Score != 9-177 {
		t.Errorf("score should be net (+1 minus -1), got %d", got[0].Score)
	}
	// `confused` means "this is unclear" — neither agreement nor objection.
	// Folding it into either would invent a reading nobody expressed.
	if got[0].Extra["confused"] != int64(11) {
		t.Errorf("confused should be kept separately: %+v", got[0].Extra)
	}
	if post.Downvotes == nil || *post.Downvotes != 177 {
		t.Errorf("post downvotes lost: %+v", post.Downvotes)
	}
	if len(post.Tags) != 1 || post.Tags[0] != "integration: tado" {
		t.Errorf("labels lost: %+v", post.Tags)
	}
	if post.Closed {
		t.Error("an open issue must not be marked closed")
	}
}

func TestPullRequestsAreExcluded(t *testing.T) {
	// Issues and PRs share an endpoint and a number space. A PR is a proposed
	// FIX, not a report of pain; including them fills the archive with patch
	// discussion.
	c := fixtureClient(t, map[string]string{
		"/search/issues": `{"total_count":2,"items":[
			{"number":1,"title":"real issue","comments":10},
			{"number":2,"title":"a pull request","comments":50,"pull_request":{}}]}`,
	})
	got, err := c.ListIssues(context.Background(), "a/b", 10)
	if err != nil {
		t.Fatalf("ListIssues: %v", err)
	}
	if len(got) != 1 || got[0].Number != 1 {
		t.Fatalf("pull request leaked into the results: %+v", got)
	}
}

func TestTheIssueTitleLeadsItsBody(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/issues/5": `{"number":5,"title":"Backups silently fail",
			"body":"and nobody notices for weeks","state":"closed","comments":2,
			"html_url":"https://github.com/a/b/issues/5","user":{"login":"x"}}`,
		"/issues/5/comments": `[]`,
	})
	got, post, err := c.FetchIssue(context.Background(), "a/b", 5)
	if err != nil {
		t.Fatalf("FetchIssue: %v", err)
	}
	if !strings.HasPrefix(got[0].Body, "Backups silently fail") {
		t.Errorf("title must lead the body: %q", got[0].Body)
	}
	if !post.Closed {
		t.Error("a closed issue must be marked closed")
	}
}

func TestCommentsBecomeRepliesAtDepthOne(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/issues/5": `{"number":5,"title":"T","body":"the problem","comments":1,
			"html_url":"https://github.com/a/b/issues/5","user":{"login":"x"}}`,
		"/issues/5/comments": `[{"id":99,"body":"I hit this too, here is a workaround",
			"created_at":"2026-01-02T03:04:05Z",
			"html_url":"https://github.com/a/b/issues/5#issuecomment-99",
			"reactions":{"total_count":3,"+1":3,"-1":0},
			"user":{"login":"y","html_url":"https://github.com/y"}}]`,
	})
	got, _, err := c.FetchIssue(context.Background(), "a/b", 5)
	if err != nil {
		t.Fatalf("FetchIssue: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want issue + 1 comment, got %d", len(got))
	}
	if got[0].Depth != 0 || got[1].Depth != 1 {
		t.Errorf("depths wrong: %d/%d", got[0].Depth, got[1].Depth)
	}
	if got[1].ParentID != "5" {
		t.Errorf("comment must point at its issue: %q", got[1].ParentID)
	}
	if got[1].Author != "y" || got[1].CreatedAt != "2026-01-02T03:04:05Z" {
		t.Errorf("comment detail lost: %+v", got[1])
	}
}

func TestAnApiMessageIsReportedNotSwallowed(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/search/issues": `{"message":"API rate limit exceeded","items":[]}`,
	})
	_, err := c.ListIssues(context.Background(), "a/b", 5)
	if err == nil || !strings.Contains(err.Error(), "rate limit") {
		t.Fatalf("an API message must surface, got %v", err)
	}
}

func TestAnEmptyRepoIsAnErrorNotASilentZero(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/search/issues": `{"total_count":0,"items":[]}`,
	})
	if _, err := c.ListIssues(context.Background(), "a/b", 5); err == nil {
		t.Fatal("no issues must be an error, not a silent zero-item run")
	}
}

func TestMinCommentsFloorIsApplied(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/search/issues": `{"items":[
			{"number":1,"comments":1},{"number":2,"comments":40}]}`,
	})
	c.MinComments = 10
	got, err := c.ListIssues(context.Background(), "a/b", 10)
	if err != nil {
		t.Fatalf("ListIssues: %v", err)
	}
	if len(got) != 1 || got[0].Number != 2 {
		t.Fatalf("floor not applied: %+v", got)
	}
}
