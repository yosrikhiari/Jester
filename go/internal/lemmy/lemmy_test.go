package lemmy

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func fixtureClient(t *testing.T, byURL map[string]string) *Client {
	t.Helper()
	return &Client{Get: func(_ context.Context, u string) ([]byte, error) {
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
	}}
}

func TestInstanceAndCommunitySplit(t *testing.T) {
	for _, tc := range []struct{ in, inst, comm string }{
		{"https://programming.dev/c/rust", "https://programming.dev", "rust"},
		{"programming.dev/c/rust", "https://programming.dev", "rust"},
		{"https://lemmy.world/c/selfhosted/", "https://lemmy.world", "selfhosted"},
		{"https://lemmy.world", "https://lemmy.world", ""},
		{"lemmy.ml", "https://lemmy.ml", ""},
		{"", "", ""},
	} {
		inst, comm := InstanceAndCommunity(tc.in)
		if inst != tc.inst || comm != tc.comm {
			t.Errorf("InstanceAndCommunity(%q) = (%q,%q), want (%q,%q)",
				tc.in, inst, comm, tc.inst, tc.comm)
		}
	}
}

// Lemmy publishes both sides of the vote, which Reddit stopped doing in 2014
// and still fuzzes. This is the second source in the system able to fill
// Downvotes at all.
func TestBothSidesOfTheVoteAreKept(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/api/v3/post?": `{"post_view":{
			"post":{"id":7,"name":"Docker eats my disk","body":"and I cannot see why",
			        "ap_id":"https://lemmy.world/post/7","published":"2026-08-23T09:54:39.191778Z"},
			"counts":{"comments":58,"score":779,"upvotes":791,"downvotes":12},
			"creator":{"name":"ann","actor_id":"https://lemmy.world/u/ann"},
			"community":{"name":"selfhosted","actor_id":"https://lemmy.world/c/selfhosted"}}}`,
		"/api/v3/comment/list?": `{"comments":[
			{"comment":{"id":11,"content":"journald retention, every time","path":"0.11",
			            "ap_id":"https://lemmy.world/comment/11","published":"2026-08-23T10:00:00Z"},
			 "counts":{"score":16,"upvotes":18,"downvotes":2,"child_count":3},
			 "creator":{"name":"bob","actor_id":"https://lemmy.world/u/bob"}},
			{"comment":{"id":12,"content":"nested reply here","path":"0.11.12",
			            "ap_id":"https://lemmy.world/comment/12","published":"2026-08-23T10:05:00Z"},
			 "counts":{"score":4,"upvotes":4,"downvotes":0,"child_count":0},
			 "creator":{"name":"cat"}}]}`,
	})
	got, post, err := c.FetchPost(context.Background(), "https://lemmy.world/c/selfhosted", 7)
	if err != nil {
		t.Fatalf("FetchPost: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want post + 2 comments, got %d", len(got))
	}
	if got[0].Upvotes == nil || *got[0].Upvotes != 791 {
		t.Errorf("post upvotes lost: %+v", got[0].Upvotes)
	}
	if got[0].Downvotes == nil || *got[0].Downvotes != 12 {
		t.Errorf("post downvotes lost: %+v", got[0].Downvotes)
	}
	if got[1].Downvotes == nil || *got[1].Downvotes != 2 {
		t.Errorf("comment downvotes lost: %+v", got[1].Downvotes)
	}
	// A published ZERO is recorded as zero, not dropped as absent.
	if got[2].Downvotes == nil || *got[2].Downvotes != 0 {
		t.Errorf("a published zero must survive: %+v", got[2].Downvotes)
	}
	if post.Downvotes == nil || *post.Downvotes != 12 {
		t.Errorf("post-level downvotes lost: %+v", post.Downvotes)
	}
}

func TestDepthComesFromTheMaterialisedPath(t *testing.T) {
	// `0.11` is top level; `0.11.12` is one reply deep. Counting segments is
	// exact and free, where Reddit's equivalent had to be inferred.
	for _, tc := range []struct {
		path   string
		depth  int
		parent string
	}{
		{"0.11", 0, ""},
		{"0.11.12", 1, "11"},
		{"0.11.12.13", 2, "12"},
		{"", 0, ""},
		{"0", 0, ""},
	} {
		d, p := depthFromPath(tc.path)
		if d != tc.depth || p != tc.parent {
			t.Errorf("depthFromPath(%q) = (%d,%q), want (%d,%q)",
				tc.path, d, p, tc.depth, tc.parent)
		}
	}
}

// Federation is the trap. The same comment carries a different LOCAL id on
// every instance that mirrors it, so hashing the local id would let one
// comment into the archive once per instance we read.
func TestFingerprintIdentityIsTheFederatedApId(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/api/v3/post?": `{"post_view":{"post":{"id":7,"name":"T","ap_id":"https://lemmy.world/post/7"},
			"counts":{},"creator":{"name":"a"},"community":{"name":"c"}}}`,
		"/api/v3/comment/list?": `{"comments":[
			{"comment":{"id":999,"content":"same comment, local id 999 here",
			            "path":"0.999","ap_id":"https://lemmy.world/comment/11"},
			 "counts":{},"creator":{"name":"bob"}}]}`,
	})
	got, _, err := c.FetchPost(context.Background(), "https://programming.dev", 7)
	if err != nil {
		t.Fatalf("FetchPost: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want post + comment, got %d", len(got))
	}
	// The id hashed for dedup must be the home-instance ap_id, not the local 999.
	if !strings.Contains(got[1].ID, "lemmy.world/comment/11") {
		t.Errorf("identity must be the federated ap_id, got %q", got[1].ID)
	}
	if got[1].PlatformID != "999" {
		t.Errorf("the local id is still worth keeping alongside: %q", got[1].PlatformID)
	}
}

func TestDeletedAndRemovedContentIsSkipped(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/api/v3/post?": `{"post_view":{"post":{"id":7,"name":"T","ap_id":"x"},
			"counts":{},"creator":{"name":"a"},"community":{"name":"c"}}}`,
		"/api/v3/comment/list?": `{"comments":[
			{"comment":{"id":1,"content":"gone","path":"0.1","deleted":true},"counts":{},"creator":{}},
			{"comment":{"id":2,"content":"moderated away","path":"0.2","removed":true},"counts":{},"creator":{}},
			{"comment":{"id":3,"content":"a real complaint about disks","path":"0.3","ap_id":"y"},
			 "counts":{},"creator":{"name":"d"}}]}`,
	})
	got, _, _ := c.FetchPost(context.Background(), "https://lemmy.world", 7)
	if len(got) != 2 {
		t.Fatalf("want post + 1 live comment, got %d", len(got))
	}
	if !strings.Contains(got[1].Body, "real complaint") {
		t.Errorf("kept the wrong comment: %q", got[1].Body)
	}
}

func TestMinCommentsFloorAndEmptyFeed(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/api/v3/post/list?": `{"posts":[
			{"post":{"id":1},"counts":{"comments":0}},
			{"post":{"id":2},"counts":{"comments":40}}]}`,
	})
	c.MinComments = 5
	got, err := c.ListPosts(context.Background(), "https://lemmy.world", 10)
	if err != nil {
		t.Fatalf("ListPosts: %v", err)
	}
	if len(got) != 1 || got[0].ID != 2 {
		t.Fatalf("floor not applied: %+v", got)
	}

	empty := fixtureClient(t, map[string]string{"/api/v3/post/list?": `{"posts":[]}`})
	if _, err := empty.ListPosts(context.Background(), "https://lemmy.world", 5); err == nil {
		t.Fatal("an empty feed must be an error, not a silent zero-post run")
	}
}

func TestAnApiErrorIsReportedNotSwallowed(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/api/v3/post/list?": `{"error":"rate_limit_error","posts":[]}`,
	})
	_, err := c.ListPosts(context.Background(), "https://lemmy.world", 5)
	if err == nil || !strings.Contains(err.Error(), "rate_limit") {
		t.Fatalf("an API error must surface, got %v", err)
	}
}

func TestAnInstanceThatIgnoresPagingDoesNotYieldCopies(t *testing.T) {
	// Some instances ignore `page` and re-serve the same window. Without a
	// cross-page dedup the result fills with copies of one post — which is
	// exactly what the first version of this adapter did.
	c := fixtureClient(t, map[string]string{
		"/api/v3/post/list?": `{"posts":[{"post":{"id":42},"counts":{"comments":40}}]}`,
	})
	got, err := c.ListPosts(context.Background(), "https://lemmy.world", 10)
	if err != nil {
		t.Fatalf("ListPosts: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want the single distinct post, got %d copies", len(got))
	}
}
