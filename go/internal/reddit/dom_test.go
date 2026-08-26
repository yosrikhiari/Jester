package reddit

// §37.21 Phase B tests: calibrated DOM extraction + cross-language fingerprints.

import (
	"crypto/sha1"
	"encoding/hex"
	"strings"
	"testing"
)

func TestFingerprintMatchesPythonSha1Prefix(t *testing.T) {
	// Python: hashlib.sha1(b"t1_p5jpjst").hexdigest()[:16] -> 5f11279bee0110a6
	got := Fingerprint(FetchedComment{ID: "t1_p5jpjst", Body: "anything else"})
	if got != "5f11279bee0110a6" {
		t.Fatalf("fingerprint mismatch with Python dialect: got %q", got)
	}
}

func TestFingerprintFallsBackToBodyWhenIDEmpty(t *testing.T) {
	sum := sha1.Sum([]byte("orphan text"))
	want := hex.EncodeToString(sum[:])[:16]
	got := Fingerprint(FetchedComment{ID: "", Body: "orphan text"})
	if got != want {
		t.Fatalf("fallback fingerprint: got %q want %q", got, want)
	}
}

func TestCommentsFromNodesMapsTrimsAndFilters(t *testing.T) {
	nodes := []map[string]any{
		{"id": "t1_a", "body": "  padded body  ", "score": "12"},
		{"id": "", "body": "", "score": "5"}, // dropped: empty body
		{"thingid": "t1_b", "body": "alt keys", "score": 7.0},
	}
	out := CommentsFromNodes(nodes)
	if len(out) != 2 {
		t.Fatalf("expected 2 comments, got %d", len(out))
	}
	if out[0].Body != "padded body" || out[0].Score != 12 {
		t.Fatalf("row0 mapped wrong: %+v", out[0])
	}
	if out[1].ID != "t1_b" || out[1].Score != 7 {
		t.Fatalf("row1 mapped wrong: %+v", out[1])
	}
}

func TestDOMJSTargetsCalibratedSelectors(t *testing.T) {
	// The calibrated source of truth (§37.19): thingid/score attrs + .md body.
	for _, marker := range []string{"shreddit-comment", "thingid", ".md", "score"} {
		if !contains(DOMJS, marker) {
			t.Fatalf("DOMJS missing calibrated selector %q", marker)
		}
	}
}

func contains(haystack, needle string) bool {
	return len(needle) == 0 || (len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0)
}

func indexOf(h, n string) int {
	for i := 0; i+len(n) <= len(h); i++ {
		if h[i:i+len(n)] == n {
			return i
		}
	}
	return -1
}

func TestPermalinksJSCollectsEveryPostOnTheListing(t *testing.T) {
	// Walking only the first post made "add a subreddit" a one-thread sample;
	// max_threads_per_source caps the list this selector returns.
	for _, marker := range []string{"querySelectorAll", "shreddit-post[permalink]", "permalink"} {
		if !contains(PERMALINKS_JS, marker) {
			t.Fatalf("PERMALINKS_JS missing %q", marker)
		}
	}
}

func TestChallengeREMatchesLiveInterstitials(t *testing.T) {
	// Calibrated against a real cloakserve run: Reddit answered a flagged
	// fingerprint with "Reddit - Prove your humanity" and the old pattern
	// missed it, so a block surfaced as "no shreddit-post[permalink]".
	for _, page := range []string{
		"Reddit - Prove your humanity",
		"Just a moment...",
		"Please verify you are human",
		"Are you a robot?",
		"unusual traffic from your network",
		"CAPTCHA required",
	} {
		if !challengeRE.MatchString(page) {
			t.Errorf("challengeRE missed %q", page)
		}
	}
	for _, page := range []string{
		"r/selfhosted - What are you running this week?",
		"Comments on my homelab rack",
	} {
		if challengeRE.MatchString(page) {
			t.Errorf("challengeRE false-positived on %q", page)
		}
	}
}

// The listing already renders a comment count next to every post. Hacker News
// and Discourse both use theirs to skip a thread too quiet to be worth
// fetching; Reddit read its own and threw it away. A scheduled run then spent
// its entire two-post budget on two r/hiredev threads advertising 0 and 1
// comments against a floor of 8, fetched both, kept nothing, and finished with
// 27 sources unvisited.
func TestPermalinksJSAsksForTheCommentCount(t *testing.T) {
	if !strings.Contains(PERMALINKS_JS, "comment-count") {
		t.Fatal("the listing walk must read the count the page already renders")
	}
	// It must still be the permalink that identifies a row, not the count.
	if !strings.Contains(PERMALINKS_JS, "permalink") {
		t.Fatal("permalink is still the identity")
	}
}
