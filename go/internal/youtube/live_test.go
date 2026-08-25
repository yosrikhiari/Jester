package youtube

// §37.26 live validation — requires the cloakserve container running.
// Public video comments only, per the §37.17 decision.

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"
)

func cdpUpYT() bool {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:9222/json/version")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

func TestLiveFetchRealVideoComments(t *testing.T) {
	if !cdpUpYT() {
		t.Skip("cloakserve CDP endpoint not reachable")
	}
	videoURL := "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
	comments, err := FetchVideoComments(
		context.Background(), "http://127.0.0.1:9222", videoURL,
		2*time.Second, 5, "backoff",
	)
	if err != nil {
		t.Fatalf("live fetch: %v", err)
	}
	_ = videoURL
	if len(comments) == 0 {
		t.Fatal("expected >=1 comment from a real video via next-XHR extraction")
	}
	for _, c := range comments {
		if c.Body == "" {
			t.Fatalf("empty comment body: %+v", c)
		}
		if got := Fingerprint(c); len(got) != 16 {
			t.Fatalf("bad fingerprint length for %+v: %q", c, got)
		}
		if !strings.HasPrefix(c.ID, "Ug") && c.ID != "" {
			// entity keys are base64 of ids beginning Ug...; empty = fallback path
			t.Logf("note: unusual id prefix %q", c.ID[:min2(6, len(c.ID))])
		}
	}
}

func min2(a, b int) int {
	if a < b {
		return a
	}
	return b
}
