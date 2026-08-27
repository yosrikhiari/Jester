// Package hackernews ingests Hacker News discussion through Algolia's public
// HN Search API.
//
// Unlike the Reddit/YouTube adapters this needs no browser at all: the API is
// public, documented, keyless and unmetered, so there is no fingerprint to
// warm, no challenge to absorb and no ToS grey area (§6's scrape framing does
// not apply — this is a sanctioned read). That makes it the cheapest and most
// reliable source in the list, which is why it is worth its own adapter rather
// than being scraped through cloakserve.
//
// "Ask HN" threads in particular are dense with the thing Jester is looking
// for: practitioners describing a problem they have, in their own words.
package hackernews

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

	"jester/internal/htmltext"
	"jester/internal/reddit"
)

// FetchedComment is the shared batch comment shape (one dialect, §37.21).
type FetchedComment = reddit.FetchedComment

const (
	// search_by_date, NOT search. The plain /search endpoint ranks by
	// relevance over ALL time, so a recurring run kept re-fetching the same
	// famous threads — "Ask HN: I'm an FCC Commissioner" (2023), "Is S3
	// down?" (2017) — every single tick. Dedup then discarded all of it, so
	// the schedule fired on time and queued nothing new, forever. A feed read
	// on a clock has to be ordered by the clock.
	SearchEndpoint = "https://hn.algolia.com/api/v1/search_by_date"
	ItemEndpoint   = "https://hn.algolia.com/api/v1/items/"
	// ItemURL is where a human would read the thread; stored as the source URL.
	ItemURL = "https://news.ycombinator.com/item?id="
)

// Tags worth ingesting. `ask_hn` is the default because a question about a
// problem is a better pain signal than a link submission's commentary.
var KnownTags = []string{"ask_hn", "show_hn", "story", "front_page"}

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

// Client reads HN. A nil Getter uses a plain HTTP client.
type Client struct {
	Get Getter
	// MinComments filters out threads with too little discussion to be worth
	// a fetch. Zero means "no floor".
	MinComments int
}

func New() *Client { return &Client{Get: httpGet, MinComments: 10} }

func httpGet(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	// Identify honestly rather than impersonating a browser: this is a public
	// API being used as intended.
	req.Header.Set("User-Agent", "jester/1.0 (local research tool)")
	req.Header.Set("Accept", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", u, resp.Status)
	}
	return body, nil
}

func (c *Client) get(ctx context.Context, u string) ([]byte, error) {
	if c.Get != nil {
		return c.Get(ctx, u)
	}
	return httpGet(ctx, u)
}

// SearchURL builds the story-listing request for a tag.
func (c *Client) SearchURL(tag string, limit int) string {
	if tag == "" {
		tag = "ask_hn"
	}
	if limit < 1 {
		limit = 1
	}
	v := url.Values{}
	v.Set("tags", tag)
	v.Set("hitsPerPage", strconv.Itoa(limit))
	if c.MinComments > 0 {
		v.Set("numericFilters", "num_comments>"+strconv.Itoa(c.MinComments))
	}
	return SearchEndpoint + "?" + v.Encode()
}

type searchResponse struct {
	Hits []struct {
		ObjectID    string `json:"objectID"`
		Title       string `json:"title"`
		NumComments int    `json:"num_comments"`
	} `json:"hits"`
}

// Story is one discussion thread worth fetching.
type Story struct {
	ID          string
	Title       string
	NumComments int
}

// ListStories returns up to `limit` stories for a tag, newest first — the
// order search_by_date returns them in.
func (c *Client) ListStories(ctx context.Context, tag string, limit int) ([]Story, error) {
	body, err := c.get(ctx, c.SearchURL(tag, limit))
	if err != nil {
		return nil, fmt.Errorf("hn search: %w", err)
	}
	var resp searchResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("hn search decode: %w", err)
	}
	out := make([]Story, 0, len(resp.Hits))
	for _, h := range resp.Hits {
		if h.ObjectID == "" {
			continue
		}
		out = append(out, Story{ID: h.ObjectID, Title: h.Title, NumComments: h.NumComments})
		if len(out) == limit {
			break
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no HN stories for tag %q (min_comments=%d)", tag, c.MinComments)
	}
	return out, nil
}

// item mirrors the Algolia item tree. Comments nest arbitrarily deep.
type item struct {
	ID         int64  `json:"id"`
	Text       string `json:"text"`
	Author     string `json:"author"`
	Points     *int   `json:"points"`
	Type       string `json:"type"`
	Title      string `json:"title"`
	URL        string `json:"url"`
	ParentID   *int64 `json:"parent_id"`
	StoryID    *int64 `json:"story_id"`
	CreatedAt  string `json:"created_at"`
	CreatedAtI int64  `json:"created_at_i"`
	Options    []any  `json:"options"`
	Children   []item `json:"children"`
}

// itemURL is where a human reads one comment or story.
func itemURL(id int64) string { return ItemURL + strconv.FormatInt(id, 10) }

// flatten walks the whole comment tree. A reply three levels down is still a
// person describing a problem, so depth is not a reason to drop it.
//
// `depth` is derived from the walk rather than read from the payload: Algolia
// publishes the tree, not a depth field, so the position in that tree IS the
// depth — it is observed, not guessed.
func flatten(nodes []item, depth int, out *[]FetchedComment) {
	for _, n := range nodes {
		body := htmltext.Plain(n.Text)
		if body != "" {
			c := FetchedComment{
				ID:         "hn:" + strconv.FormatInt(n.ID, 10),
				PlatformID: strconv.FormatInt(n.ID, 10),
				Body:       body,
				Author:     strings.TrimSpace(n.Author),
				CreatedAt:  reddit.NormalizeTime(n.CreatedAt),
				Permalink:  itemURL(n.ID),
				Depth:      depth,
				Replies:    reddit.I64(int64(len(n.Children))),
			}
			if c.CreatedAt == "" && n.CreatedAtI > 0 {
				c.CreatedAt = reddit.FromUnix(n.CreatedAtI)
			}
			if c.Author != "" {
				c.AuthorURL = "https://news.ycombinator.com/user?id=" + c.Author
			}
			if n.ParentID != nil {
				c.ParentID = strconv.FormatInt(*n.ParentID, 10)
			}
			// Points: Algolia returns null for every COMMENT — HN does not
			// publish per-comment scores at all (verified live). Recording a
			// 0 there would say "nobody upvoted this", which is not what the
			// API said; it said nothing. Score therefore stays 0 as the
			// neutral sort key while Upvotes stays nil to mark it unknown.
			if n.Points != nil {
				c.Score = int64(*n.Points)
				c.Upvotes = reddit.I64(int64(*n.Points))
			}
			*out = append(*out, c)
		}
		if len(n.Children) > 0 {
			flatten(n.Children, depth+1, out)
		}
	}
}

// FeedName is the human name of an Algolia tag, used as the community a story
// was read from. Hacker News has no subreddits, so without this every one of
// its stories lands in a single bucket named after the whole site and the
// console can only say "2,923 nuggets, from Hacker News" — but Ask HN, Show HN
// and the front page are three different rooms with three different intents,
// and that is exactly the distinction a grouped archive is for.
//
// The feed is known to the caller, not to the item: Algolia's story node says
// nothing about which listing it was found through, so this cannot live in
// postFrom.
func FeedName(tag string) string {
	switch tag {
	case "ask_hn":
		return "Ask HN"
	case "show_hn":
		return "Show HN"
	case "front_page":
		return "HN front page"
	default:
		return ""
	}
}

// FeedURL is the listing a FeedName came from, so the console can link the
// community it groups by. An unknown name gets the site root rather than a
// guessed path.
func FeedURL(feed string) string {
	switch feed {
	case "Ask HN":
		return "https://news.ycombinator.com/ask"
	case "Show HN":
		return "https://news.ycombinator.com/show"
	case "HN front page":
		return "https://news.ycombinator.com/news"
	default:
		return "https://news.ycombinator.com/"
	}
}

// postFrom maps the story node at the root of an item tree.
func postFrom(it item) *reddit.FetchedPost {
	if it.ID == 0 {
		return nil
	}
	p := &reddit.FetchedPost{
		ID:           strconv.FormatInt(it.ID, 10),
		Title:        strings.TrimSpace(it.Title),
		URL:          itemURL(it.ID),
		Body:         htmltext.Plain(it.Text),
		Author:       strings.TrimSpace(it.Author),
		CreatedAt:    reddit.NormalizeTime(it.CreatedAt),
		Kind:         strings.TrimSpace(it.Type),
		Community:    "Hacker News",
		CommunityURL: "https://news.ycombinator.com/",
		CommentCount: reddit.I64(countComments(it.Children)),
	}
	if p.CreatedAt == "" && it.CreatedAtI > 0 {
		p.CreatedAt = reddit.FromUnix(it.CreatedAtI)
	}
	if p.Author != "" {
		p.AuthorURL = "https://news.ycombinator.com/user?id=" + p.Author
	}
	// Story points ARE public, unlike comment points.
	if it.Points != nil {
		p.Score = reddit.I64(int64(*it.Points))
		p.Upvotes = reddit.I64(int64(*it.Points))
	}
	if it.URL != "" {
		p.Extra = map[string]any{"target_url": it.URL}
	}
	return p
}

func countComments(nodes []item) int64 {
	var n int64
	for _, c := range nodes {
		n++
		n += countComments(c.Children)
	}
	return n
}

// FetchComments returns every comment in one story's tree, plus the story
// itself. The story used to be discarded: a batch reached the archive knowing
// the comment bodies but not the question they were answering.
func (c *Client) FetchComments(ctx context.Context, storyID string) ([]FetchedComment, *reddit.FetchedPost, error) {
	storyID = strings.TrimSpace(storyID)
	if storyID == "" {
		return nil, nil, fmt.Errorf("empty HN story id")
	}
	body, err := c.get(ctx, ItemEndpoint+url.PathEscape(storyID))
	if err != nil {
		return nil, nil, fmt.Errorf("hn item %s: %w", storyID, err)
	}
	var it item
	if err := json.Unmarshal(body, &it); err != nil {
		return nil, nil, fmt.Errorf("hn item %s decode: %w", storyID, err)
	}
	var out []FetchedComment
	flatten(it.Children, 0, &out)
	return out, postFrom(it), nil
}

// SourceURL is where a reviewer can read the thread the nugget came from.
func SourceURL(storyID string) string { return ItemURL + storyID }
