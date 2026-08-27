// Package steam ingests Steam product reviews through the public store API.
//
// WHY THIS SOURCE. Every other platform in this system yields discussion: a
// question, a thread, an argument. Steam yields a VERDICT — one person, one
// product, thumbs up or down, with the complaint attached. That is a different
// shape of evidence, and it arrives with two things the rest of the corpus
// cannot supply at any price:
//
//	SEVERITY, already measured. `review_type=negative` is a first-class filter
//	on the endpoint, so the pain does not have to be inferred from tone. A
//	forum thread has to be read to find out whether anyone is unhappy; here
//	the platform has already sorted them.
//
//	STANDING. `playtime_at_review` is how many minutes the reviewer had spent
//	in the product WHEN THEY COMPLAINED. A complaint at 2,756 hours and a
//	complaint at 0.3 hours are not the same claim, and nothing else in this
//	archive can tell them apart. Combined with num_games_owned and num_reviews
//	it is the closest thing to a credibility signal the corpus has.
//
// It is also the third source able to fill Downvotes at all: total_negative is
// published alongside total_positive on the app, next to GitHub's -1 reactions
// and Lemmy's separate vote counts.
//
// SANCTIONED, KEYLESS. store.steampowered.com/appreviews is Valve's own
// documented Store API (Steamworks "Get App Reviews"). No key, no login, no
// circumvention — the same category as Hacker News via Algolia, Discourse and
// Lemmy, and deliberately NOT the category the Reddit adapter sits in.
//
// WHAT THIS IS NOT. Steam is not only games: it sells Aseprite, Blender,
// Wallpaper Engine, RPG Maker and a long tail of creative and utility
// software, and those reviews are ordinary product complaints about ordinary
// product failures. The games matter too, for a narrower reason — a game is a
// piece of software with an enormous, unusually articulate user base that
// complains in public about performance, input handling, save corruption and
// UI, which are the same failures every other product has.
package steam

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"jester/internal/reddit"
)

// FetchedComment is the shared batch comment shape (one dialect, §37.21).
type FetchedComment = reddit.FetchedComment

// FetchedPost is the shared thread shape.
type FetchedPost = reddit.FetchedPost

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

// maxPerPage is the endpoint's own ceiling. Asking for more is silently
// truncated to 100, so the constant is here to stop a caller believing a
// larger number did anything.
const maxPerPage = 100

type Client struct {
	Get Getter
	// Negative restricts the walk to thumbs-down reviews, and defaults ON.
	//
	// This is the adapter's central decision, so it is worth stating plainly:
	// an unfiltered walk of a popular title spends its whole budget on
	// "Addicting to play" and "One of the best". Aseprite's own summary is
	// 251,109 positive against 8,287 negative — an unfiltered fetch is 97%
	// noise for a pipeline whose entire job is finding pain. The positives are
	// not deleted, they are simply not what this system is for, and a caller
	// who wants them can turn this off.
	Negative bool
	// Language filters to reviews written in one language. The pipeline's
	// prefilter, extractor and embeddings are all English, so a Russian
	// complaint is real signal this stack cannot read — it would be embedded
	// as noise rather than dropped honestly.
	Language string
	// Delay paces multi-request walks. Valve publishes no documented rate
	// limit for this endpoint, which is a reason to be more careful rather
	// than less: an undocumented limit is one you discover by being blocked.
	Delay time.Duration
}

func New() *Client {
	return &Client{Get: httpGet, Negative: true, Language: "english", Delay: 400 * time.Millisecond}
}

func httpGet(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "jester/1.0 (local research tool)")
	req.Header.Set("Accept", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", u, resp.Status)
	}
	return body, nil
}

// ---- wire shapes ----------------------------------------------------------

type author struct {
	SteamID          string `json:"steamid"`
	PersonaName      string `json:"personaname"`
	ProfileURL       string `json:"profile_url"`
	NumGamesOwned    int64  `json:"num_games_owned"`
	NumReviews       int64  `json:"num_reviews"`
	PlaytimeForever  int64  `json:"playtime_forever"`
	PlaytimeAtReview int64  `json:"playtime_at_review"`
	LastPlayed       int64  `json:"last_played"`
}

type review struct {
	RecommendationID string    `json:"recommendationid"`
	Author           author    `json:"author"`
	Language         string    `json:"language"`
	Review           string    `json:"review"`
	TimestampCreated int64     `json:"timestamp_created"`
	TimestampUpdated int64     `json:"timestamp_updated"`
	VotedUp          bool      `json:"voted_up"`
	VotesUp          int64     `json:"votes_up"`
	VotesFunny       int64     `json:"votes_funny"`
	WeightedVote     flexFloat `json:"weighted_vote_score"`
	CommentCount     int64     `json:"comment_count"`
	SteamPurchase    bool      `json:"steam_purchase"`
	ReceivedForFree  bool      `json:"received_for_free"`
	EarlyAccess      bool      `json:"written_during_early_access"`
	PrimarilyDeck    bool      `json:"primarily_steam_deck"`
	// DeveloperResponse is present only when the publisher answered, which
	// makes its presence the signal: the vendor read this complaint and felt
	// it needed a reply.
	DeveloperResponse string `json:"developer_response"`
	TimestampDevResp  int64  `json:"timestamp_dev_responded"`
}

// flexFloat decodes a number Steam sends BOTH ways.
//
// weighted_vote_score comes back quoted for a review that has accumulated
// helpfulness votes ("0.454545468091964722") and unquoted for one that has not
// (0.5). Verified live on app 365670: thirteen strings and seven numbers in a
// single twenty-row page. A plain string field is not a cosmetic mismatch —
// encoding/json fails the WHOLE response on the first number, so every Steam
// fetch died with "cannot unmarshal number into Go struct field
// review.reviews.weighted_vote_score of type string" and the source reported
// as skipped.
type flexFloat struct {
	Value float64
	Set   bool
}

func (f *flexFloat) UnmarshalJSON(b []byte) error {
	trimmed := strings.TrimSpace(string(b))
	if trimmed == "" || trimmed == "null" {
		return nil
	}
	// Quoted form: unwrap, then parse. An unparseable body leaves Set false
	// rather than recording a zero score nobody published.
	if len(trimmed) >= 2 && trimmed[0] == '"' {
		var raw string
		if err := json.Unmarshal(b, &raw); err != nil {
			return err
		}
		raw = strings.TrimSpace(raw)
		if raw == "" {
			return nil
		}
		v, err := strconv.ParseFloat(raw, 64)
		if err != nil {
			return nil
		}
		f.Value, f.Set = v, true
		return nil
	}
	var v float64
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	f.Value, f.Set = v, true
	return nil
}

type summary struct {
	NumReviews      int64  `json:"num_reviews"`
	ReviewScore     int64  `json:"review_score"`
	ReviewScoreDesc string `json:"review_score_desc"`
	TotalPositive   int64  `json:"total_positive"`
	TotalNegative   int64  `json:"total_negative"`
	TotalReviews    int64  `json:"total_reviews"`
}

type reviewsResponse struct {
	Success      int      `json:"success"`
	QuerySummary summary  `json:"query_summary"`
	Reviews      []review `json:"reviews"`
	Cursor       string   `json:"cursor"`
}

// ---- URLs -----------------------------------------------------------------

// AppIDFromURL pulls the numeric id out of a store URL. Steam's own links
// carry a slug after it (/app/431730/Aseprite/) which is decorative — the id
// is the identity — so anything after the number is ignored.
func AppIDFromURL(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	// A bare id is a legitimate way to name an app.
	if isDigits(s) {
		return s
	}
	i := strings.Index(strings.ToLower(s), "/app/")
	if i < 0 {
		return ""
	}
	rest := s[i+len("/app/"):]
	if j := strings.IndexAny(rest, "/?#"); j >= 0 {
		rest = rest[:j]
	}
	if !isDigits(rest) {
		return ""
	}
	return rest
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// StoreURL is the app's public page, used as the post URL and community link.
func StoreURL(appID string) string {
	return "https://store.steampowered.com/app/" + appID + "/"
}

// ReviewURL is one review's permalink.
func ReviewURL(recommendationID string) string {
	return "https://steamcommunity.com/profiles/id/recommended/?" +
		url.Values{"recommendationid": {recommendationID}}.Encode()
}

func (c *Client) reviewsURL(appID, cursor string, perPage int) string {
	if perPage <= 0 || perPage > maxPerPage {
		perPage = maxPerPage
	}
	q := url.Values{
		"json": {"1"},
		// `recent` orders by creation date, which is what makes the cursor a
		// stable walk. `all` reorders by helpfulness and re-serves the same
		// famous reviews every run.
		"filter":       {"recent"},
		"num_per_page": {strconv.Itoa(perPage)},
		// Reviews from keys and gifts are still real complaints about the
		// product, so they are not excluded; `steam_purchase` records which
		// is which instead.
		"purchase_type": {"all"},
		"cursor":        {cursorOr(cursor)},
	}
	if c.Language != "" {
		q.Set("language", c.Language)
	}
	if c.Negative {
		q.Set("review_type", "negative")
	}
	return "https://store.steampowered.com/appreviews/" + appID + "?" + q.Encode()
}

// summaryURL asks for the app's vote totals and no reviews at all.
//
// Necessary because `review_type=negative` SUPPRESSES them: a filtered query
// answers with a bare {"num_reviews": N} and drops total_positive,
// total_negative and review_score_desc (verified live on app 431730 —
// unfiltered gives 16,391/151, filtered gives neither). Since the filter is
// this adapter's default, taking the totals off the filtered page would mean
// silently never recording the one real downvote count Steam publishes.
// num_per_page=0 returns the whole summary and zero rows, so the extra request
// costs a header exchange.
func summaryURL(appID string) string {
	q := url.Values{
		"json":          {"1"},
		"filter":        {"recent"},
		"purchase_type": {"all"},
		"num_per_page":  {"0"},
	}
	return "https://store.steampowered.com/appreviews/" + appID + "?" + q.Encode()
}

// Summary is the app's lifetime vote totals.
//
// A failure is returned rather than swallowed, but callers treat it as
// non-fatal: the reviews are the point, and a post row without the totals is
// worse than one with them and far better than no batch at all.
func (c *Client) Summary(ctx context.Context, appID string) (summary, error) {
	var out summary
	body, err := c.Get(ctx, summaryURL(appID))
	if err != nil {
		return out, err
	}
	var resp reviewsResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return out, fmt.Errorf("steam app %s summary decode: %w", appID, err)
	}
	if resp.Success != 1 {
		return out, fmt.Errorf("steam app %s: the store refused the summary", appID)
	}
	return resp.QuerySummary, nil
}

// hasTotals reports whether a summary carries the app-level vote counts, as
// opposed to the stub a filtered query returns.
func (s summary) hasTotals() bool { return s.TotalPositive > 0 || s.TotalNegative > 0 }

// cursorOr is "*" for the first page — the endpoint's documented start token,
// and an empty cursor silently returns page one forever.
func cursorOr(cursor string) string {
	if strings.TrimSpace(cursor) == "" {
		return "*"
	}
	return cursor
}

// ---- fetching -------------------------------------------------------------

// AppName is the store's own title for an app, or "" if the store will not say.
//
// A failure here is deliberately not fatal to a fetch: the reviews are the
// point, and a batch labelled "app 431730" is worse than one labelled
// "Aseprite" but far better than no batch at all.
func (c *Client) AppName(ctx context.Context, appID string) string {
	u := "https://store.steampowered.com/api/appdetails?filters=basic&appids=" + appID
	body, err := c.Get(ctx, u)
	if err != nil {
		return ""
	}
	var wrapper map[string]struct {
		Success bool `json:"success"`
		Data    struct {
			Name string `json:"name"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &wrapper); err != nil {
		return ""
	}
	entry, ok := wrapper[appID]
	if !ok || !entry.Success {
		return ""
	}
	return strings.TrimSpace(entry.Data.Name)
}

// FetchReviews walks an app's reviews newest-first, following the cursor until
// it has `want` of them or the app runs out.
//
// Returns the reviews as comments plus the app as their post. `want` is a
// ceiling, not a promise: a niche app may hold fewer negative reviews than
// asked for, and stopping early is the correct outcome rather than something
// to pad.
func (c *Client) FetchReviews(ctx context.Context, appID string, want int) ([]FetchedComment, *FetchedPost, error) {
	if strings.TrimSpace(appID) == "" {
		return nil, nil, fmt.Errorf("steam: no app id")
	}
	if want <= 0 {
		want = maxPerPage
	}
	var (
		out    []FetchedComment
		post   *FetchedPost
		cursor string
		// Steam re-serves the same review across pages when the cursor lands
		// on a boundary, and a repeated review is a repeated fingerprint the
		// store would reject one row at a time. Cheaper to notice here.
		seen = map[string]bool{}
	)
	for len(out) < want {
		page := want - len(out)
		if page > maxPerPage {
			page = maxPerPage
		}
		body, err := c.Get(ctx, c.reviewsURL(appID, cursor, page))
		if err != nil {
			return out, post, err
		}
		var resp reviewsResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			return out, post, fmt.Errorf("steam app %s decode: %w", appID, err)
		}
		if resp.Success != 1 {
			return out, post, fmt.Errorf("steam app %s: the store refused the query", appID)
		}
		if post == nil {
			sum := resp.QuerySummary
			// A filtered page carries no totals, so ask for them once.
			if !sum.hasTotals() {
				if full, err := c.Summary(ctx, appID); err == nil && full.hasTotals() {
					sum = full
				}
			}
			post = postFrom(appID, sum)
		}
		fresh := 0
		for _, r := range resp.Reviews {
			if seen[r.RecommendationID] {
				continue
			}
			seen[r.RecommendationID] = true
			if c := commentFrom(r); c != nil {
				out = append(out, *c)
				fresh++
			}
		}
		// No cursor, no rows, or a page that was entirely repeats: the walk is
		// over. Without the last of those a boundary cursor loops forever.
		if resp.Cursor == "" || len(resp.Reviews) == 0 || fresh == 0 {
			break
		}
		cursor = resp.Cursor
		if c.Delay > 0 {
			select {
			case <-ctx.Done():
				return out, post, ctx.Err()
			case <-time.After(c.Delay):
			}
		}
	}
	if len(out) > want {
		out = out[:want]
	}
	return out, post, nil
}

// ---- mapping --------------------------------------------------------------

// put appends to a long-tail map, allocating on first use so an adapter with
// nothing extra to say serialises no `extra` key at all.
func put(m map[string]any, k string, v any) map[string]any {
	if m == nil {
		m = map[string]any{}
	}
	m[k] = v
	return m
}

// postFrom maps the app itself, which is the "thread" these reviews hang off.
func postFrom(appID string, s summary) *FetchedPost {
	p := &FetchedPost{
		ID:           appID,
		URL:          StoreURL(appID),
		Kind:         "app",
		Community:    appID, // replaced by the store's title when it answers
		CommunityURL: StoreURL(appID),
	}
	// total_reviews is the app's lifetime count and is published even when the
	// current query is filtered, so it describes the app rather than the page.
	if s.TotalReviews > 0 {
		p.CommentCount = reddit.I64(s.TotalReviews)
	}
	// The real prize: an actual downvote count, which Reddit has not published
	// since 2014 and YouTube withdrew in 2021.
	if s.TotalPositive > 0 || s.TotalNegative > 0 {
		p.Upvotes = reddit.I64(s.TotalPositive)
		p.Downvotes = reddit.I64(s.TotalNegative)
		p.Score = reddit.I64(s.TotalPositive - s.TotalNegative)
		total := s.TotalPositive + s.TotalNegative
		if total > 0 {
			// Stored as the ratio, matching how Reddit's upvote_ratio is
			// handled: it is a published fact, not something solved out of a
			// fuzzed score.
			ratio := float64(s.TotalPositive) / float64(total)
			p.UpvoteRatio = &ratio
		}
	}
	if s.ReviewScoreDesc != "" {
		p.Extra = put(p.Extra, "review_score_desc", s.ReviewScoreDesc)
	}
	if s.ReviewScore > 0 {
		p.Extra = put(p.Extra, "review_score", s.ReviewScore)
	}
	return p
}

// commentFrom maps one review, or nil when there is no text to extract from.
func commentFrom(r review) *FetchedComment {
	body := strings.TrimSpace(r.Review)
	if body == "" || strings.TrimSpace(r.RecommendationID) == "" {
		return nil
	}
	c := &FetchedComment{
		ID:         r.RecommendationID,
		PlatformID: r.RecommendationID,
		Body:       body,
		Author:     strings.TrimSpace(r.Author.PersonaName),
		AuthorURL:  strings.TrimSpace(r.Author.ProfileURL),
		Permalink:  ReviewURL(r.RecommendationID),
		CreatedAt:  reddit.FromUnix(r.TimestampCreated),
		// Reviews are flat. Steam threads COMMENTS under a review, but those
		// are a separate endpoint and are not fetched here, so every row is
		// genuinely top-level rather than defaulted to it.
		Depth: 0,
		// votes_up is "found this helpful", which is an upvote on the REVIEW.
		// It is not voted_up, which is the reviewer's verdict on the product;
		// conflating the two would turn a helpfulness count into a rating.
		Upvotes: reddit.I64(r.VotesUp),
		Score:   r.VotesUp,
		Edited:  r.TimestampUpdated > r.TimestampCreated,
	}
	if r.CommentCount > 0 {
		c.Replies = reddit.I64(r.CommentCount)
	}
	// The verdict, kept explicitly rather than implied by the query filter: a
	// row must state what it is even when read outside the fetch that made it.
	c.Extra = put(c.Extra, "voted_up", r.VotedUp)
	if r.VotesFunny > 0 {
		c.Extra = put(c.Extra, "votes_funny", r.VotesFunny)
	}
	if r.WeightedVote.Set {
		c.Extra = put(c.Extra, "weighted_vote_score", r.WeightedVote.Value)
	}
	// Standing. playtime_at_review is the one that matters — playtime_forever
	// keeps growing after the complaint was written, so using it would credit
	// a reviewer with hours they had not yet spent when they said it.
	if r.Author.PlaytimeAtReview > 0 {
		c.Extra = put(c.Extra, "playtime_at_review_minutes", r.Author.PlaytimeAtReview)
	}
	if r.Author.PlaytimeForever > 0 {
		c.Extra = put(c.Extra, "playtime_forever_minutes", r.Author.PlaytimeForever)
	}
	if r.Author.NumReviews > 0 {
		c.Extra = put(c.Extra, "author_num_reviews", r.Author.NumReviews)
	}
	if r.Author.NumGamesOwned > 0 {
		c.Extra = put(c.Extra, "author_num_products_owned", r.Author.NumGamesOwned)
	}
	if r.Author.SteamID != "" {
		c.Extra = put(c.Extra, "steamid", r.Author.SteamID)
	}
	if r.Language != "" {
		c.Extra = put(c.Extra, "language", r.Language)
	}
	// How they came by the product, which changes how a complaint reads: a
	// refund request from a paying customer is not a free-key holder's gripe.
	c.Extra = put(c.Extra, "steam_purchase", r.SteamPurchase)
	if r.ReceivedForFree {
		c.Extra = put(c.Extra, "received_for_free", true)
	}
	if r.EarlyAccess {
		c.Extra = put(c.Extra, "written_during_early_access", true)
	}
	if r.PrimarilyDeck {
		c.Extra = put(c.Extra, "primarily_steam_deck", true)
	}
	// A publisher reply means the vendor read this one and thought it needed
	// answering — the strongest "this pain is real" marker on the platform.
	if resp := strings.TrimSpace(r.DeveloperResponse); resp != "" {
		c.Extra = put(c.Extra, "developer_response", resp)
		if r.TimestampDevResp > 0 {
			c.Extra = put(c.Extra, "developer_responded_at", reddit.FromUnix(r.TimestampDevResp))
		}
	}
	return c
}
