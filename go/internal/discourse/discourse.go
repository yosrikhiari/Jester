// Package discourse ingests any Discourse forum through its public JSON API.
//
// This is the highest-leverage adapter in the set: Discourse runs a large share
// of the software and product communities worth listening to, and every one of
// them exposes the same two endpoints — `<base>/latest.json` for the topic list
// and `<base>/t/<id>.json` for a topic's posts. So one adapter reaches many
// forums, and adding a new one is a config line rather than code.
//
// Like the Hacker News adapter this is plain HTTP against a documented public
// API: no browser, no fingerprint, no challenge.
package discourse

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

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

type Client struct {
	Get Getter
	// MinPosts skips topics with too little discussion to be worth a fetch.
	MinPosts int
}

func New() *Client { return &Client{Get: httpGet, MinPosts: 3} }

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

func (c *Client) get(ctx context.Context, u string) ([]byte, error) {
	if c.Get != nil {
		return c.Get(ctx, u)
	}
	return httpGet(ctx, u)
}

// BaseURL normalises a forum root: scheme kept, trailing slash and any known
// endpoint suffix removed, so both "https://forum.example" and
// "https://forum.example/latest" resolve to the same base.
func BaseURL(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	if !strings.Contains(s, "://") {
		s = "https://" + s
	}
	s = strings.TrimRight(s, "/")
	for _, suffix := range []string{"/latest.json", "/latest", "/categories.json", "/categories"} {
		s = strings.TrimSuffix(s, suffix)
	}
	return strings.TrimRight(s, "/")
}

// Topic is one thread worth fetching.
type Topic struct {
	ID     int64
	Slug   string
	Title  string
	Posts  int
}

type latestResponse struct {
	TopicList struct {
		Topics []struct {
			ID         int64  `json:"id"`
			Slug       string `json:"slug"`
			Title      string `json:"title"`
			PostsCount int    `json:"posts_count"`
			Pinned     bool   `json:"pinned"`
		} `json:"topics"`
	} `json:"topic_list"`
}

// ListTopics returns up to `limit` recent topics with enough discussion.
func (c *Client) ListTopics(ctx context.Context, base string, limit int) ([]Topic, error) {
	base = BaseURL(base)
	if base == "" {
		return nil, fmt.Errorf("empty discourse base url")
	}
	if limit < 1 {
		limit = 1
	}
	// no_definitions drops the pinned "welcome"/"how to ask" boilerplate that
	// every forum pins and nobody is describing a problem in.
	body, err := c.get(ctx, base+"/latest.json?no_definitions=true")
	if err != nil {
		return nil, fmt.Errorf("discourse latest: %w", err)
	}
	var resp latestResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("discourse latest decode: %w", err)
	}
	out := make([]Topic, 0, limit)
	for _, t := range resp.TopicList.Topics {
		if t.ID == 0 || t.Pinned || t.PostsCount < c.MinPosts {
			continue
		}
		out = append(out, Topic{ID: t.ID, Slug: t.Slug, Title: t.Title, Posts: t.PostsCount})
		if len(out) == limit {
			break
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no discourse topics at %s with >=%d posts", base, c.MinPosts)
	}
	return out, nil
}

type topicResponse struct {
	PostStream struct {
		Posts []struct {
			ID       int64   `json:"id"`
			Cooked   string  `json:"cooked"`
			Username string  `json:"username"`
			Score    float64 `json:"score"`
		} `json:"posts"`
	} `json:"post_stream"`
}

// FetchPosts returns a topic's posts as batch comments.
func (c *Client) FetchPosts(ctx context.Context, base string, topicID int64) ([]FetchedComment, error) {
	base = BaseURL(base)
	u := base + "/t/" + url.PathEscape(strconv.FormatInt(topicID, 10)) + ".json"
	body, err := c.get(ctx, u)
	if err != nil {
		return nil, fmt.Errorf("discourse topic %d: %w", topicID, err)
	}
	var resp topicResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("discourse topic %d decode: %w", topicID, err)
	}
	out := make([]FetchedComment, 0, len(resp.PostStream.Posts))
	for _, p := range resp.PostStream.Posts {
		text := htmltext.Plain(p.Cooked)
		if text == "" {
			continue
		}
		// Discourse `score` is an engagement composite, not an upvote count —
		// carried through as the engagement signal rather than invented.
		out = append(out, FetchedComment{
			ID:    "discourse:" + strconv.FormatInt(p.ID, 10),
			Body:  text,
			Score: int64(p.Score),
		})
	}
	return out, nil
}

// TopicURL is where a reviewer can read the thread a nugget came from.
func TopicURL(base string, t Topic) string {
	base = BaseURL(base)
	if t.Slug != "" {
		return fmt.Sprintf("%s/t/%s/%d", base, t.Slug, t.ID)
	}
	return fmt.Sprintf("%s/t/%d", base, t.ID)
}
