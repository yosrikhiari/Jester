package reddit

// §37.21 Phase B live validation — requires the cloakserve container running
// (docker compose up -d cloakbrowser). Scrapes public comment metadata only,
// per the §37.17 decision.

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"jester/internal/cloakbrowser"
)

func cdpUp() bool {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:9222/json/version")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

func TestLiveFetchRealThread(t *testing.T) {
	if !cdpUp() {
		t.Skip("cloakserve CDP endpoint not reachable")
	}
	cb := cloakbrowser.New("http://127.0.0.1:9222")
	sess, err := cb.NewSession(context.Background())
	if err != nil {
		t.Fatalf("cdp session: %v", err)
	}
	defer sess.Cancel()

	listing := "https://www.reddit.com/r/selfhosted/new/"
	comments, threadURL, threadID, err := FetchThread(
		sess.Ctx, listing, 2*time.Second, ThreadIDFromPath, "backoff", 2,
	)
	if err != nil {
		t.Fatalf("live fetch: %v", err)
	}
	if threadID == "" || !strings.Contains(threadURL, "/comments/") {
		t.Fatalf("thread discovery failed: url=%q id=%q", threadURL, threadID)
	}
	if len(comments) == 0 {
		t.Fatal("expected >=1 comment from a real thread via SSR DOM extraction")
	}
	for _, c := range comments {
		if !strings.HasPrefix(c.ID, "t1_") {
			t.Fatalf("comment id not a t1 thingid: %+v", c)
		}
		want := Fingerprint(c) // sha1(id)[:16] by construction; guards regressions
		if len(want) != 16 {
			t.Fatalf("bad fingerprint length for %+v", c)
		}
	}
}
