package reddit

import (
	"encoding/json"
	"testing"
)

// The DOM rows below are the SHAPE the live page produced when the widened
// extraction was calibrated (a real r/selfhosted thread), attribute names and
// value formats included — including Reddit's `created` offset spelling, which
// is not RFC3339 and which the obvious time.Parse call rejects.

func TestCommentsFromNodesKeepsEverythingPublished(t *testing.T) {
	got := CommentsFromNodes([]map[string]any{{
		"id":               "t1_p5yoori",
		"body":             "  centralized secrets are a mess  ",
		"score":            "12",
		"author":           "asimovs-auditor",
		"created":          "2026-08-26T07:27:21.991000+0000",
		"permalink":        "/r/selfhosted/comments/1vyq33h/comment/p5yoori/",
		"depth":            "0",
		"postid":           "t3_1vyq33h",
		"parent_positions": "[]",
		"award_count":      "3",
		"content_type":     "text",
		"replies":          float64(2),
	}})
	if len(got) != 1 {
		t.Fatalf("want 1 comment, got %d", len(got))
	}
	c := got[0]
	if c.Body != "centralized secrets are a mess" {
		t.Errorf("body not trimmed: %q", c.Body)
	}
	if c.Author != "asimovs-auditor" {
		t.Errorf("author lost: %q", c.Author)
	}
	if c.AuthorURL != "https://www.reddit.com/user/asimovs-auditor/" {
		t.Errorf("author url wrong: %q", c.AuthorURL)
	}
	// Reddit's own offset spelling, normalised to RFC3339 UTC.
	if c.CreatedAt != "2026-08-26T07:27:21Z" {
		t.Errorf("created not normalised: %q", c.CreatedAt)
	}
	if c.Permalink != "https://www.reddit.com/r/selfhosted/comments/1vyq33h/comment/p5yoori/" {
		t.Errorf("permalink not absolutised: %q", c.Permalink)
	}
	if c.Upvotes == nil || *c.Upvotes != 12 || c.Score != 12 {
		t.Errorf("score/upvotes lost: %d %+v", c.Score, c.Upvotes)
	}
	// Reddit has published no per-comment downvote since 2014; inventing one
	// (from the ratio, from the rank) is exactly what must not happen.
	if c.Downvotes != nil {
		t.Errorf("reddit publishes no comment downvotes; got %d", *c.Downvotes)
	}
	if c.Replies == nil || *c.Replies != 2 {
		t.Errorf("reply count lost: %+v", c.Replies)
	}
	if c.Awards == nil || *c.Awards != 3 {
		t.Errorf("award count lost: %+v", c.Awards)
	}
	// An empty parent chain IS the fact that this is top-level.
	if c.ParentID != "" || c.Depth != 0 {
		t.Errorf("top-level comment mis-parented: parent=%q depth=%d", c.ParentID, c.Depth)
	}
	if c.Extra["post_id"] != "t3_1vyq33h" {
		t.Errorf("post id lost: %+v", c.Extra)
	}
}

func TestCommentsFromNodesLeavesAbsentCountsUnset(t *testing.T) {
	// A row with no score attribute at all: the comment must not claim zero
	// upvotes, because the page did not say zero — it said nothing.
	got := CommentsFromNodes([]map[string]any{{
		"id": "t1_x", "body": "hi there", "score": "", "award_count": "",
	}})
	if len(got) != 1 {
		t.Fatalf("want 1 comment, got %d", len(got))
	}
	if got[0].Upvotes != nil {
		t.Errorf("absent score must stay unset, got %d", *got[0].Upvotes)
	}
	if got[0].Awards != nil {
		t.Errorf("absent award count must stay unset, got %d", *got[0].Awards)
	}
}

func TestPostFromNodeCapturesTheUpvoteRatio(t *testing.T) {
	p := PostFromNode(map[string]any{
		"id":            "t3_1vyq33h",
		"title":         "Centralized secret management options",
		"url":           "/r/selfhosted/comments/1vyq33h/centralized/",
		"target_url":    "https://i.redd.it/abc.jpeg",
		"author":        "SugarvetFounder",
		"created":       "2026-08-26T07:27:11.248000+0000",
		"score":         "41",
		"upvote_ratio":  "0.6666666666666666",
		"comment_count": "10",
		"award_count":   "0",
		"community":     "r/selfhosted",
		"language":      "en",
		"kind":          "image",
		"flair":         "Release (AI)",
	})
	if p == nil {
		t.Fatal("post metadata dropped")
	}
	// The URL must be the DISCUSSION, not the image the post links to.
	if p.URL != "https://www.reddit.com/r/selfhosted/comments/1vyq33h/centralized/" {
		t.Errorf("post url should be the thread: %q", p.URL)
	}
	if p.Extra["target_url"] != "https://i.redd.it/abc.jpeg" {
		t.Errorf("link target lost: %+v", p.Extra)
	}
	// The one downvote signal Reddit publishes anywhere.
	if p.UpvoteRatio == nil || *p.UpvoteRatio < 0.66 || *p.UpvoteRatio > 0.67 {
		t.Fatalf("upvote ratio lost: %+v", p.UpvoteRatio)
	}
	// …and it is kept as the raw ratio. Solving ups/downs out of it would hand
	// downstream a precise-looking number, and the score it would be solved
	// against is vote-fuzzed.
	if p.Downvotes != nil {
		t.Errorf("downvotes must not be derived from the ratio, got %d", *p.Downvotes)
	}
	if p.CommentCount == nil || *p.CommentCount != 10 {
		t.Errorf("comment count lost: %+v", p.CommentCount)
	}
	// A published zero is NOT an absence: the page said award-count="0".
	if p.Awards == nil || *p.Awards != 0 {
		t.Errorf("a published zero must be recorded as zero: %+v", p.Awards)
	}
	if len(p.Tags) != 1 || p.Tags[0] != "Release (AI)" {
		t.Errorf("flair lost: %+v", p.Tags)
	}
}

func TestPostFromNodeReturnsNilWhenThereIsNoPost(t *testing.T) {
	// "no <shreddit-post> on the page" must stay distinguishable from
	// "captured a post and it was blank".
	if p := PostFromNode(nil); p != nil {
		t.Errorf("a missing post must be nil, got %+v", p)
	}
	if p := PostFromNode(map[string]any{"title": "", "id": "", "url": ""}); p != nil {
		t.Errorf("an empty post row must be nil, got %+v", p)
	}
}

func TestNormalizeTimeRejectsWhatItCannotRead(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"2026-08-26T07:27:21.991000+0000", "2026-08-26T07:27:21Z"},
		{"2026-08-26T07:27:21.991Z", "2026-08-26T07:27:21Z"},
		{"2022-07-28T22:23:23.657Z", "2022-07-28T22:23:23Z"},
		// A relative age is not a timestamp. Returning now() here would file
		// the comment under a time the platform never claimed.
		{"1 year ago", ""},
		{"", ""},
		{"not a date", ""},
	} {
		if got := NormalizeTime(tc.in); got != tc.want {
			t.Errorf("NormalizeTime(%q)=%q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestDepthSurvivesJSONAtZero(t *testing.T) {
	// depth 0 is the commonest depth there is. With `omitempty` it vanished
	// from the payload and every top-level comment read as "depth unknown".
	raw, err := json.Marshal(FetchedComment{ID: "x", Body: "b", Depth: 0})
	if err != nil {
		t.Fatal(err)
	}
	var back map[string]any
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatal(err)
	}
	if _, ok := back["depth"]; !ok {
		t.Errorf("depth 0 must serialise, got %s", raw)
	}
}
