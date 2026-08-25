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
	SearchEndpoint = "https://hn.algolia.com/api/v1/search"
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

// ListStories returns up to `limit` recent stories for a tag, busiest first as
// the API orders them.
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
	ID       int64  `json:"id"`
	Text     string `json:"text"`
	Author   string `json:"author"`
	Points   *int   `json:"points"`
	Type     string `json:"type"`
	Title    string `json:"title"`
	Children []item `json:"children"`
}

// flatten walks the whole comment tree. A reply three levels down is still a
// person describing a problem, so depth is not a reason to drop it.
func flatten(nodes []item, out *[]FetchedComment) {
	for _, n := range nodes {
		body := htmltext.Plain(n.Text)
		if body != "" {
			// Algolia returns null points on comments; upvotes are never
			// fabricated (R29) — absent means 0, not "guess from position".
			score := int64(0)
			if n.Points != nil {
				score = int64(*n.Points)
			}
			*out = append(*out, FetchedComment{
				ID:    "hn:" + strconv.FormatInt(n.ID, 10),
				Body:  body,
				Score: score,
			})
		}
		if len(n.Children) > 0 {
			flatten(n.Children, out)
		}
	}
}

// FetchComments returns every comment in one story's tree.
func (c *Client) FetchComments(ctx context.Context, storyID string) ([]FetchedComment, error) {
	storyID = strings.TrimSpace(storyID)
	if storyID == "" {
		return nil, fmt.Errorf("empty HN story id")
	}
	body, err := c.get(ctx, ItemEndpoint+url.PathEscape(storyID))
	if err != nil {
		return nil, fmt.Errorf("hn item %s: %w", storyID, err)
	}
	var it item
	if err := json.Unmarshal(body, &it); err != nil {
		return nil, fmt.Errorf("hn item %s decode: %w", storyID, err)
	}
	var out []FetchedComment
	flatten(it.Children, &out)
	return out, nil
}

// SourceURL is where a reviewer can read the thread the nugget came from.
func SourceURL(storyID string) string { return ItemURL + storyID }
