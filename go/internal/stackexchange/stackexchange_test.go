package stackexchange

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func fixtureClient(t *testing.T, byURL map[string]string) *Client {
	t.Helper()
	return &Client{Get: func(_ context.Context, u string) ([]byte, error) {
		for frag, body := range byURL {
			if strings.Contains(u, frag) {
				return []byte(body), nil
			}
		}
		return nil, fmt.Errorf("no fixture for %s", u)
	}}
}

func TestSiteFromURLHandlesEveryShapeAConfigMightHold(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"serverfault", "serverfault"},
		{"https://serverfault.com", "serverfault"},
		{"https://serverfault.com/", "serverfault"},
		{"unix.stackexchange.com", "unix"},
		{"https://unix.stackexchange.com/questions", "unix"},
		{"https://stackoverflow.com", "stackoverflow"},
		{"dba", "dba"},
		{"", "stackoverflow"},
	} {
		if got := SiteFromURL(tc.in); got != tc.want {
			t.Errorf("SiteFromURL(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestSiteHostRoundTrips(t *testing.T) {
	// The sites on their own domain are the exception, and getting them wrong
	// produces a permalink that 404s for whoever follows a citation.
	for _, tc := range []struct{ slug, want string }{
		{"serverfault", "serverfault.com"},
		{"stackoverflow", "stackoverflow.com"},
		{"askubuntu", "askubuntu.com"},
		{"unix", "unix.stackexchange.com"},
		{"dba", "dba.stackexchange.com"},
	} {
		if got := siteHost(tc.slug); got != tc.want {
			t.Errorf("siteHost(%q) = %q, want %q", tc.slug, got, tc.want)
		}
	}
}

func TestTheQuestionLeadsAndCarriesItsTitle(t *testing.T) {
	// On Stack Exchange the title is usually the clearest one-line statement
	// of the problem. Dropping it loses the best sentence in the post.
	c := fixtureClient(t, map[string]string{
		"/questions/42?": `{"items":[{"question_id":42,"title":"Disk fills overnight",
			"body":"<p>Every night my root partition fills and I cannot see why.</p>",
			"score":7,"answer_count":2,"view_count":1500,"is_answered":false,
			"creation_date":1700000000,"link":"https://serverfault.com/questions/42/x",
			"tags":["disk","linux"],"owner":{"display_name":"ann","link":"https://serverfault.com/users/1"}}],
			"quota_remaining":275}`,
		"/answers?": `{"items":[
			{"answer_id":91,"body":"<p>Check journald retention.</p>","score":12,
			 "is_accepted":true,"creation_date":1700001000,
			 "owner":{"display_name":"bob","link":"https://serverfault.com/users/2"}}],
			"quota_remaining":274}`,
	})
	got, post, err := c.FetchQuestion(context.Background(), "serverfault", 42)
	if err != nil {
		t.Fatalf("FetchQuestion: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want question + 1 answer, got %d", len(got))
	}
	if !strings.HasPrefix(got[0].Body, "Disk fills overnight") {
		t.Errorf("the title must lead the question body: %q", got[0].Body)
	}
	if !strings.Contains(got[0].Body, "root partition fills") {
		t.Errorf("question body lost: %q", got[0].Body)
	}
	if got[0].Depth != 0 || got[1].Depth != 1 {
		t.Errorf("question must be depth 0 and answers depth 1: %d/%d",
			got[0].Depth, got[1].Depth)
	}
	if !got[1].Accepted {
		t.Error("the accepted answer must be marked as such")
	}
	if got[0].Reads == nil || *got[0].Reads != 1500 {
		t.Errorf("view count lost: %+v", got[0].Reads)
	}

	// The signal that makes this source worth having.
	if post.Extra["is_answered"] != false {
		t.Errorf("is_answered must survive: %+v", post.Extra)
	}
	if post.Views == nil || *post.Views != 1500 {
		t.Errorf("post views lost: %+v", post.Views)
	}
	if len(post.Tags) != 2 {
		t.Errorf("tags lost: %+v", post.Tags)
	}
	if c.QuotaRemaining != 274 {
		t.Errorf("quota must be tracked, got %d", c.QuotaRemaining)
	}
}

func TestAnswersFailingDoesNotLoseTheQuestion(t *testing.T) {
	// The question IS the pain. Losing it because the answers call failed
	// would throw away the thing we came for.
	c := fixtureClient(t, map[string]string{
		"/questions/42?": `{"items":[{"question_id":42,"title":"T",
			"body":"<p>the problem</p>","link":"https://serverfault.com/questions/42/x",
			"owner":{"display_name":"ann"}}],"quota_remaining":9}`,
	})
	got, post, err := c.FetchQuestion(context.Background(), "serverfault", 42)
	if err != nil {
		t.Fatalf("FetchQuestion: %v", err)
	}
	if len(got) != 1 || post == nil {
		t.Fatalf("question must survive an answers failure, got %d items", len(got))
	}
}

func TestAnApiErrorIsReportedNotSwallowed(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/questions?": `{"error_message":"throttle violation","items":[]}`,
	})
	_, err := c.ListQuestions(context.Background(), "serverfault", 5)
	if err == nil || !strings.Contains(err.Error(), "throttle") {
		t.Fatalf("an API error must surface, got %v", err)
	}
}

func TestAnEmptyFeedIsAnErrorNotASilentZero(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/questions?": `{"items":[],"has_more":false,"quota_remaining":100}`,
	})
	if _, err := c.ListQuestions(context.Background(), "serverfault", 5); err == nil {
		t.Fatal("an empty feed must be an error, not a silent zero-item run")
	}
}

func TestMinAnswersFloorIsApplied(t *testing.T) {
	c := fixtureClient(t, map[string]string{
		"/questions?": `{"items":[
			{"question_id":1,"answer_count":0},
			{"question_id":2,"answer_count":5}],"has_more":false}`,
	})
	c.MinAnswers = 3
	got, err := c.ListQuestions(context.Background(), "serverfault", 10)
	if err != nil {
		t.Fatalf("ListQuestions: %v", err)
	}
	if len(got) != 1 || got[0].ID != 2 {
		t.Fatalf("floor not applied: %+v", got)
	}
}
