package steam

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func read(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatalf("fixture %s: %v", name, err)
	}
	return b
}

// routed serves fixtures by what the URL asks for, so a test exercises the
// same URL-building the live client uses instead of a stub that would pass
// whatever query string were sent.
func routed(t *testing.T, pages [][]byte, sum []byte) (*Client, *[]string) {
	t.Helper()
	var seen []string
	i := 0
	c := New()
	c.Delay = 0
	c.Get = func(_ context.Context, u string) ([]byte, error) {
		seen = append(seen, u)
		if strings.Contains(u, "num_per_page=0") {
			if sum == nil {
				return nil, fmt.Errorf("no summary fixture")
			}
			return sum, nil
		}
		if i >= len(pages) {
			// How the store itself ends a list: a successful response with no
			// rows and no cursor. Erroring here instead would test the fixture
			// rig rather than the walk.
			return []byte(`{"success":1,"reviews":[],"cursor":""}`), nil
		}
		p := pages[i]
		i++
		return p, nil
	}
	return c, &seen
}

// ── the query this adapter actually sends ────────────────────────────────────

func TestNegativeIsTheDefaultAndIsInTheQuery(t *testing.T) {
	// The whole reason to read Steam here is that it has already sorted the
	// complaints. An unfiltered walk of Aseprite spends its budget on 16,391
	// positives to reach 151 negatives.
	c := New()
	if !c.Negative {
		t.Fatal("negative-only must be the default")
	}
	u := c.reviewsURL("431730", "", 50)
	for _, want := range []string{
		"review_type=negative", "language=english", "filter=recent",
		"num_per_page=50", "cursor=%2A", "purchase_type=all",
	} {
		if !strings.Contains(u, want) {
			t.Errorf("query is missing %q: %s", want, u)
		}
	}
}

func TestFirstPageUsesTheDocumentedStartCursor(t *testing.T) {
	// An empty cursor re-serves page one forever; "*" is the documented start.
	c := New()
	if !strings.Contains(c.reviewsURL("1", "", 10), "cursor=%2A") {
		t.Fatal("the first page must send cursor=*")
	}
	if !strings.Contains(c.reviewsURL("1", "AoJw+abc=", 10), "cursor=AoJw%2Babc%3D") {
		t.Fatal("a real cursor must be sent url-encoded, + and = intact")
	}
}

func TestPageSizeIsCappedAtTheEndpointsOwnCeiling(t *testing.T) {
	// Asking for 500 is silently truncated to 100 by the store, so a caller
	// that believed the larger number would under-fetch without being told.
	c := New()
	if !strings.Contains(c.reviewsURL("1", "", 500), "num_per_page=100") {
		t.Fatal("page size must be clamped to 100")
	}
	if !strings.Contains(c.reviewsURL("1", "", 0), "num_per_page=100") {
		t.Fatal("a zero page size must mean the maximum, not zero rows")
	}
}

func TestLanguageFilterCanBeCleared(t *testing.T) {
	c := New()
	c.Language = ""
	if strings.Contains(c.reviewsURL("1", "", 10), "language=") {
		t.Fatal("an empty language must send no language filter at all")
	}
}

// ── app ids ──────────────────────────────────────────────────────────────────

func TestAppIDFromURL(t *testing.T) {
	cases := map[string]string{
		"https://store.steampowered.com/app/431730/Aseprite/":    "431730",
		"https://store.steampowered.com/app/431730":              "431730",
		"http://store.steampowered.com/app/292030/Witcher/?l=en": "292030",
		// The slug is decorative; the id is the identity.
		"https://store.steampowered.com/app/365670/Blender/#reviews": "365670",
		"431730": "431730",
		// Not an app link, and inventing an id from one would be worse than
		// refusing the source.
		"https://store.steampowered.com/":                    "",
		"https://store.steampowered.com/app/notanumber/":     "",
		"https://steamcommunity.com/app/431730/discussions/": "431730",
		"": "",
	}
	for in, want := range cases {
		if got := AppIDFromURL(in); got != want {
			t.Errorf("AppIDFromURL(%q) = %q, want %q", in, got, want)
		}
	}
}

// ── mapping ──────────────────────────────────────────────────────────────────

func TestReviewsMapOntoTheSharedShape(t *testing.T) {
	c, _ := routed(t, [][]byte{read(t, "reviews_page1.json")}, read(t, "summary.json"))
	comments, post, err := c.FetchReviews(context.Background(), "431730", 5)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if len(comments) == 0 {
		t.Fatal("the fixture holds reviews; none survived mapping")
	}
	if post == nil {
		t.Fatal("the app is the post these reviews hang off")
	}
	for _, cm := range comments {
		if cm.ID == "" || cm.Body == "" {
			t.Fatalf("a mapped review needs an id and a body: %+v", cm)
		}
		if cm.Permalink == "" {
			t.Errorf("review %s has no permalink", cm.ID)
		}
		if cm.CreatedAt == "" {
			t.Errorf("review %s has no timestamp; Steam publishes one", cm.ID)
		}
		if cm.Upvotes == nil {
			t.Errorf("review %s: votes_up IS published, so it must not read as unknown", cm.ID)
		}
		if _, ok := cm.Extra["voted_up"]; !ok {
			t.Errorf("review %s must state its own verdict, not rely on the query filter", cm.ID)
		}
	}
}

func TestHelpfulnessIsNotTheVerdict(t *testing.T) {
	// votes_up is "other people found this review helpful". voted_up is the
	// reviewer's thumbs up/down on the product. They are different facts and
	// mapping one onto the other turns a helpfulness count into a rating.
	c := commentFrom(review{
		RecommendationID: "1", Review: "crashes on export",
		VotedUp: false, VotesUp: 42,
	})
	if c == nil {
		t.Fatal("a review with a body must map")
	}
	if c.Upvotes == nil || *c.Upvotes != 42 {
		t.Fatalf("upvotes should carry votes_up (42), got %v", c.Upvotes)
	}
	if c.Extra["voted_up"] != false {
		t.Fatalf("voted_up must survive as the verdict, got %v", c.Extra["voted_up"])
	}
}

func TestStandingUsesPlaytimeAtReviewNotForever(t *testing.T) {
	// playtime_forever keeps growing after the complaint was written, so using
	// it would credit a reviewer with hours they had not yet spent when they
	// said it. Both are kept; the one that dates the claim is named for it.
	c := commentFrom(review{
		RecommendationID: "1", Review: "no clipping mask",
		Author: author{PlaytimeAtReview: 126, PlaytimeForever: 165372},
	})
	if c.Extra["playtime_at_review_minutes"] != int64(126) {
		t.Fatalf("want 126 minutes at review, got %v", c.Extra["playtime_at_review_minutes"])
	}
	if c.Extra["playtime_forever_minutes"] != int64(165372) {
		t.Fatalf("lifetime playtime should be kept too, got %v", c.Extra["playtime_forever_minutes"])
	}
}

func TestAnEmptyReviewIsDropped(t *testing.T) {
	// Steam allows a review with a rating and no words. There is nothing to
	// extract from it, and archiving a blank body would be a row that claims
	// to be a pain signal and says nothing.
	if commentFrom(review{RecommendationID: "1", Review: "   "}) != nil {
		t.Fatal("a bodyless review must not become a comment")
	}
	if commentFrom(review{RecommendationID: "", Review: "real text"}) != nil {
		t.Fatal("a review with no id has no stable fingerprint")
	}
}

func TestDeveloperResponseIsKept(t *testing.T) {
	c := commentFrom(review{
		RecommendationID: "1", Review: "save files corrupt",
		DeveloperResponse: "Fixed in 1.4.2, sorry about that.", TimestampDevResp: 1700000000,
	})
	if c.Extra["developer_response"] != "Fixed in 1.4.2, sorry about that." {
		t.Fatal("a publisher reply is the strongest confirmation the pain was real")
	}
	if c.Extra["developer_responded_at"] == nil {
		t.Fatal("the reply's timestamp is published and should be kept")
	}
}

// ── the app-level vote totals ────────────────────────────────────────────────

func TestFilteredPagesFallBackToTheSummaryForVoteTotals(t *testing.T) {
	// review_type=negative SUPPRESSES total_positive / total_negative — the
	// filtered page answers with a bare num_reviews. Since the filter is this
	// adapter's default, taking totals off the filtered page would mean never
	// recording the one real downvote count Steam publishes.
	c, seen := routed(t, [][]byte{read(t, "reviews_page1.json")}, read(t, "summary.json"))
	_, post, err := c.FetchReviews(context.Background(), "431730", 5)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if post.Downvotes == nil || *post.Downvotes == 0 {
		t.Fatalf("want the app's real negative count, got %v", post.Downvotes)
	}
	if post.Upvotes == nil || *post.Upvotes == 0 {
		t.Fatalf("want the app's real positive count, got %v", post.Upvotes)
	}
	if post.UpvoteRatio == nil || *post.UpvoteRatio <= 0 || *post.UpvoteRatio > 1 {
		t.Fatalf("upvote ratio should be a published fraction, got %v", post.UpvoteRatio)
	}
	asked := strings.Join(*seen, "\n")
	if !strings.Contains(asked, "num_per_page=0") {
		t.Fatalf("the summary must be fetched separately:\n%s", asked)
	}
}

func TestAnUnfilteredPageDoesNotCostAnExtraSummaryRequest(t *testing.T) {
	// When the page already carries the totals there is nothing to go back for.
	var page map[string]any
	if err := json.Unmarshal(read(t, "reviews_page1.json"), &page); err != nil {
		t.Fatal(err)
	}
	var sum map[string]any
	if err := json.Unmarshal(read(t, "summary.json"), &sum); err != nil {
		t.Fatal(err)
	}
	page["query_summary"] = sum["query_summary"]
	page["cursor"] = ""
	merged, _ := json.Marshal(page)

	c, seen := routed(t, [][]byte{merged}, nil)
	_, post, err := c.FetchReviews(context.Background(), "431730", 5)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if post.Downvotes == nil {
		t.Fatal("totals were on the page and should have been used")
	}
	for _, u := range *seen {
		if strings.Contains(u, "num_per_page=0") {
			t.Fatal("no second request should be made when the page already answered")
		}
	}
}

func TestTotalsAreNeverInventedWhenTheStoreWithholdsThem(t *testing.T) {
	// If the summary request also fails, the post says nothing about votes
	// rather than reading as an app nobody has ever disliked.
	c, _ := routed(t, [][]byte{read(t, "reviews_page1.json")}, nil)
	_, post, err := c.FetchReviews(context.Background(), "431730", 5)
	if err != nil {
		t.Fatalf("a missing summary must not fail the fetch: %v", err)
	}
	if post.Downvotes != nil || post.Upvotes != nil {
		t.Fatal("unknown totals must stay nil, not read as zero")
	}
}

// ── the walk ─────────────────────────────────────────────────────────────────

func TestTheWalkStopsWhenAPageIsAllRepeats(t *testing.T) {
	// Steam re-serves the same review when a cursor lands on a boundary. With
	// no guard the walk follows a cursor that never advances, forever.
	page := read(t, "reviews_page1.json")
	c, seen := routed(t, [][]byte{page, page, page, page}, read(t, "summary.json"))
	comments, _, err := c.FetchReviews(context.Background(), "431730", 100)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	var ids []string
	for _, cm := range comments {
		ids = append(ids, cm.ID)
	}
	unique := map[string]bool{}
	for _, id := range ids {
		if unique[id] {
			t.Fatalf("the same review was returned twice: %s", id)
		}
		unique[id] = true
	}
	// One page of content, one repeat that ended it, plus the summary.
	if len(*seen) > 4 {
		t.Fatalf("the walk did not stop on a repeated page: %d requests", len(*seen))
	}
}

func TestWantIsACeilingNotAPromise(t *testing.T) {
	// A niche app holds fewer negative reviews than asked for. Stopping early
	// is the correct outcome, and there is nothing to pad with.
	c, _ := routed(t, [][]byte{read(t, "reviews_page1.json")}, read(t, "summary.json"))
	comments, _, err := c.FetchReviews(context.Background(), "431730", 500)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if len(comments) > 500 {
		t.Fatal("more than asked for")
	}
	if len(comments) == 0 {
		t.Fatal("the fixture has reviews")
	}
}

func TestAStoreRefusalIsAnError(t *testing.T) {
	// success:0 means the store declined the query. Treating that as "no
	// reviews" would quietly mark a source barren.
	c, _ := routed(t, [][]byte{[]byte(`{"success":0}`)}, nil)
	if _, _, err := c.FetchReviews(context.Background(), "431730", 10); err == nil {
		t.Fatal("a refused query must surface as an error")
	}
}

func TestNoAppIDIsRefusedBeforeAnyRequest(t *testing.T) {
	c, seen := routed(t, nil, nil)
	if _, _, err := c.FetchReviews(context.Background(), "  ", 10); err == nil {
		t.Fatal("an empty app id must be refused")
	}
	if len(*seen) != 0 {
		t.Fatal("nothing should be requested for an app that was never named")
	}
}

// ── the wire type Steam sends two ways ───────────────────────────────────────

// weighted_vote_score arrives quoted for a review with helpfulness votes and
// unquoted for one without — thirteen strings and seven numbers in a single
// twenty-row page, verified live on app 365670. A plain string field is not a
// cosmetic mismatch: encoding/json fails the WHOLE response on the first
// number, so every Steam fetch died and all three sources reported as skipped.
func TestAPageMixingBothVoteScoreFormsDecodes(t *testing.T) {
	fixture := read(t, "mixed_vote_score.json")

	// The fixture has to actually contain both, or this passes vacuously.
	var probe struct {
		Reviews []struct {
			WeightedVote json.RawMessage `json:"weighted_vote_score"`
		} `json:"reviews"`
	}
	if err := json.Unmarshal(fixture, &probe); err != nil {
		t.Fatal(err)
	}
	var quoted, bare int
	for _, r := range probe.Reviews {
		if len(r.WeightedVote) > 0 && r.WeightedVote[0] == '"' {
			quoted++
		} else if len(r.WeightedVote) > 0 {
			bare++
		}
	}
	if quoted == 0 || bare == 0 {
		t.Fatalf("fixture must hold both forms: %d quoted, %d bare", quoted, bare)
	}

	c, _ := routed(t, [][]byte{fixture}, read(t, "summary.json"))
	comments, _, err := c.FetchReviews(context.Background(), "365670", 20)
	if err != nil {
		t.Fatalf("a mixed page must decode: %v", err)
	}
	if len(comments) != len(probe.Reviews) {
		t.Fatalf("want all %d reviews, got %d", len(probe.Reviews), len(comments))
	}
	scored := 0
	for _, cm := range comments {
		if v, ok := cm.Extra["weighted_vote_score"].(float64); ok {
			scored++
			if v < 0 || v > 1 {
				t.Errorf("review %s: %v is not a fraction", cm.ID, v)
			}
		}
	}
	if scored != quoted+bare {
		t.Fatalf("every published score should survive: %d of %d", scored, quoted+bare)
	}
}

func TestAnUnpublishedVoteScoreIsNotRecordedAsZero(t *testing.T) {
	for _, raw := range []string{`null`, `""`, `"not a number"`} {
		var f flexFloat
		if err := f.UnmarshalJSON([]byte(raw)); err != nil {
			t.Fatalf("%s should not error: %v", raw, err)
		}
		if f.Set {
			t.Fatalf("%s must leave the score unset, got %v", raw, f.Value)
		}
	}
	// And a real zero IS a value, distinct from all of the above.
	var f flexFloat
	if err := f.UnmarshalJSON([]byte(`0`)); err != nil || !f.Set || f.Value != 0 {
		t.Fatalf("a published zero must be recorded: set=%v value=%v err=%v", f.Set, f.Value, err)
	}
}
