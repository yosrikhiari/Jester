package youtube

// §37.26 Phase B-style unit tests: calibrated commentEntityPayload walking +
// cross-language fingerprint parity with python/jester/fetchers/youtube.py.

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"testing"
)

const fixtureXHR = `{
  "frameworkUpdates": {
    "entityBatchUpdate": {
      "mutations": [
        {"payload": {"other": true}},
        {"payload": {"commentEntityPayload": {
          "key": "EgpxYQ",
          "properties": {"content": {"content": "great talk, exactly my pain"}},
          "author": {"displayName": "@selfhoster"}
        }}},
        {"payload": {"commentEntityPayload": {
          "key": "",
          "properties": {"content": {"content": "no id here"}},
          "author": {"displayName": "@anon"}
        }}}
      ]
    }
  }
}`

func TestWalkCommentPayloadsFindsNestedEntities(t *testing.T) {
	var payload map[string]any
	if err := json.Unmarshal([]byte(fixtureXHR), &payload); err != nil {
		t.Fatalf("fixture parse: %v", err)
	}
	var out []map[string]any
	WalkCommentPayloads(payload, &out)
	if len(out) != 2 {
		t.Fatalf("expected 2 comment payloads, got %d", len(out))
	}
}

func TestCommentsFromPayloadsMapsAndSkipsEmpty(t *testing.T) {
	var payload map[string]any
	_ = json.Unmarshal([]byte(fixtureXHR), &payload)
	comments := CommentsFromPayloads(payload)
	if len(comments) != 2 {
		t.Fatalf("expected 2 comments, got %d", len(comments))
	}
	if comments[0].Body != "great talk, exactly my pain" || comments[0].ID != "EgpxYQ" {
		t.Fatalf("comment0 mapped wrong: %+v", comments[0])
	}
}

// The §37.21 lesson applied preemptively: Go and Python must produce the same
// fingerprint for the same entity key or the skip-list splits into dialects.
// Python: hashlib.sha1(b"EgpxYQ").hexdigest()[:16] -> a1c38324d4a79414
func TestYtFingerprintMatchesPython(t *testing.T) {
	got := Fingerprint(FetchedComment{ID: "EgpxYQ", Body: "anything"})
	if got != "a1c38324d4a79414" {
		t.Fatalf("yt fingerprint mismatch with Python: got %q", got)
	}
}

func TestYtFingerprintFallbackAuthorBody(t *testing.T) {
	c := CommentsFromPayloads(mustFixture(t))[1]
	// Python fallback source = displayName + body: "@anon" + "no id here".
	sum := sha1.Sum([]byte("@anonno id here"))
	want := hex.EncodeToString(sum[:])[:16]
	if got := Fingerprint(c); got != want {
		t.Fatalf("fallback fingerprint: got %q want %q", got, want)
	}
}

func mustFixture(t *testing.T) map[string]any {
	t.Helper()
	var payload map[string]any
	if err := json.Unmarshal([]byte(fixtureXHR), &payload); err != nil {
		t.Fatalf("fixture parse: %v", err)
	}
	return payload
}

func TestIsBlockRelevantScopesToYouTubeOrigin(t *testing.T) {
	cases := []struct{ url string; want bool }{
		{"https://www.youtube.com/youtubei/v1/next", true},
		{"https://www.youtube.com/watch?v=x", true},
		{"https://rr1---sn-x.googlevideo.com/videoplayback?expire=1", false},
		{"https://i.ytimg.com/vi/x/hq.jpg", false},
	}
	for _, c := range cases {
		if got := isBlockRelevant(c.url); got != c.want {
			t.Fatalf("isBlockRelevant(%q) = %v, want %v", c.url, got, c.want)
		}
	}
}
