package reddit

// §37.21 Phase B tests: calibrated DOM extraction + cross-language fingerprints.

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
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

// ── listing pagination (A9) ──────────────────────────────────────────────────

// The plan assumed Reddit's listing renders "roughly 25 posts" and that the
// adapter merely failed to scroll. Measured live, neither half held: a paint
// renders exactly THREE, scrolling adds none at any viewport (checked at
// 1440x2400, where the document is shorter than the window), and
// old.reddit.com answers this session with a "Welcome to Reddit" interstitial.
// The number was invisible because max_threads_per_platform.reddit is also 3 —
// the adapter looked like it honoured its depth setting while being pinned at
// the platform's floor.
func TestListingPageURLBuildsTheAfterCursor(t *testing.T) {
	base := "https://www.reddit.com/r/datascience/new/"
	if got := listingPageURL(base, "", 0); got != base {
		t.Errorf("the first page carries no cursor, got %q", got)
	}
	got := listingPageURL(base, "t3_1vz462h", 3)
	want := base + "?after=t3_1vz462h&count=3"
	if got != want {
		t.Errorf("listingPageURL = %q, want %q", got, want)
	}
	// A listing URL that already carries a query must not grow a second "?".
	got = listingPageURL("https://www.reddit.com/r/x/new/?sort=new", "t3_abc", 6)
	want = "https://www.reddit.com/r/x/new/?sort=new&after=t3_abc&count=6"
	if got != want {
		t.Errorf("listingPageURL = %q, want %q", got, want)
	}
}

func TestListingPageURLEscapesTheCursor(t *testing.T) {
	// The fullname comes off the page, so it is untrusted input on its way
	// back into a URL.
	got := listingPageURL("https://www.reddit.com/r/x/new/", "t3_a&b=c", 3)
	if strings.Contains(got, "t3_a&b=c") {
		t.Errorf("the cursor was not escaped: %s", got)
	}
	if !strings.Contains(got, "after=t3_a%26b%3Dc") {
		t.Errorf("want an escaped cursor, got %s", got)
	}
}

// The cursor is the post's own element id, so the listing read has to return
// it. Without this field the walk has nothing to page from and silently stops
// at one page — which is exactly the state this fixed.
func TestPermalinksJSReturnsThePaginationCursor(t *testing.T) {
	if !strings.Contains(PERMALINKS_JS, "fullname") {
		t.Fatal("PERMALINKS_JS must expose the post fullname for ?after=")
	}
	if !strings.Contains(PERMALINKS_JS, "p.id") {
		t.Fatal("the fullname comes from the element id attribute")
	}
	var row listingRow
	if err := json.Unmarshal(
		[]byte(`{"permalink":"/r/x/comments/1/a/","comments":"12","fullname":"t3_1"}`),
		&row); err != nil {
		t.Fatalf("listingRow must decode what the page returns: %v", err)
	}
	if row.Fullname != "t3_1" {
		t.Fatalf("fullname did not decode: %+v", row)
	}
}

// ── comment-tree expansion (A11) ─────────────────────────────────────────────

// A thread page renders exactly 25 comments however many the thread holds —
// both threads sampled hit that number precisely — and what is missing is the
// deep end. The archive's Reddit rows stopped at depth 2 while Lemmy's reached
// 6 on the same kind of discussion. Measured live, one expansion pass took a
// 31-comment thread from 25 across depths 0-2 to 31 across depths 0-3.
func TestExpandJSPressesEveryKindOfRevealControl(t *testing.T) {
	// shreddit renders these as faceplate-partial, button, summary and anchor
	// depending on where in the tree they sit. Missing any one of them leaves
	// part of the tree closed.
	for _, want := range []string{
		"faceplate-partial", "more-comments", "button", "summary",
		"more repl", "load more",
	} {
		if !strings.Contains(EXPAND_JS, want) {
			t.Errorf("EXPAND_JS does not reach %q", want)
		}
	}
	// It must report how many it pressed, or the caller cannot tell a tree
	// that was already open from selectors that stopped matching.
	if !strings.Contains(EXPAND_JS, "return n") {
		t.Error("EXPAND_JS must return the number of controls pressed")
	}
	// A click that throws must not abandon the rest of the tree.
	if !strings.Contains(EXPAND_JS, "catch") {
		t.Error("one unclickable control must not stop the pass")
	}
}

func TestCountJSCountsRenderedComments(t *testing.T) {
	// The growth check is what stops the loop: EXPAND_JS happily keeps
	// pressing the same buttons, so only a count that fails to rise can say
	// the tree is fully open.
	if !strings.Contains(COUNT_JS, "shreddit-comment") {
		t.Error("COUNT_JS must count the same elements DOMJS reads")
	}
	if !strings.Contains(COUNT_JS, "length") {
		t.Error("COUNT_JS must return a count")
	}
}

func TestExpandAndReadAgreeOnWhatACommentIs(t *testing.T) {
	// If these ever diverge, the loop would measure growth in one population
	// and extract from another — and could spin until its round cap every time.
	if !strings.Contains(DOMJS, "shreddit-comment") || !strings.Contains(COUNT_JS, "shreddit-comment") {
		t.Fatal("DOMJS and COUNT_JS must agree on the comment element")
	}
}
