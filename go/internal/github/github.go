// Package github ingests GitHub issues through the public REST API.
//
// WHY THIS SOURCE. An issue tracker is a database of things that are broken,
// written by people who cared enough to file, with reproduction steps
// attached. Nothing else in this pipeline has that density.
//
// It also publishes something NO other platform here does: real negative
// signal. Reddit stopped exposing per-comment downvotes in 2014, YouTube
// withdrew dislikes in 2021, Hacker News never had them, and Discourse has no
// downvote at all — so FetchedComment.Downvotes has been nil for every source
// in the system. GitHub reaction counts include `-1`, and they are unambiguous:
//
//	reactions: {total_count: 199, "+1": 9, "-1": 177, confused: 11}
//
// 177 people actively objecting is a stronger signal than any upvote count,
// and it finally gives that field something true to hold.
//
// Two more properties worth having:
//
//	state    open vs closed. An OLD, OPEN, heavily-commented issue is pain
//	         the maintainers could not or would not fix — the same shape as
//	         an unanswered Stack Exchange question with high views.
//	labels   maintainer-assigned taxonomy, free.
//
// RATE LIMITS. 60 core requests/hour and 10 search requests/minute
// unauthenticated; 5,000/hour and 30/minute with a token. A token is a
// personal access token with NO scopes — it authenticates the rate limit, not
// any permission. See GITHUB_TOKEN in .env.example.
package github

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"jester/internal/htmltext"
	"jester/internal/reddit"
)

// FetchedComment is the shared batch comment shape (one dialect, §37.21).
type FetchedComment = reddit.FetchedComment

// FetchedPost is the shared thread shape.
type FetchedPost = reddit.FetchedPost

const apiBase = "https://api.github.com"

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

type Client struct {
	Get Getter
	// MinComments skips issues with too little discussion to be worth a fetch.
	MinComments int
	// Token authenticates the RATE LIMIT, not any permission. A scopeless
	// personal access token lifts 60 req/hour to 5,000.
	Token string
	// RateRemaining is what the last response's header reported.
	RateRemaining int
}

func New() *Client {
	return &Client{
		Get:         nil, // set below so httpGet can see the token
		MinComments: 3,
		Token:       strings.TrimSpace(os.Getenv("GITHUB_TOKEN")),
	}
}

func (c *Client) get(ctx context.Context, u string) ([]byte, error) {
	if c.Get != nil {
		return c.Get(ctx, u)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "jester/1.0 (local research tool)")
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	// The remaining budget is a header, not a body field. Read it so a run
	// can say how much room is left instead of discovering the ceiling as a
	// sudden 403.
	if v := resp.Header.Get("X-RateLimit-Remaining"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			c.RateRemaining = n
		}
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusForbidden && c.RateRemaining == 0 {
		return nil, fmt.Errorf(
			"github rate limit exhausted (set GITHUB_TOKEN to raise 60/hour to 5,000)")
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", u, resp.Status)
	}
	return body, nil
}

// RepoFromURL reads "owner/repo" out of a curated source URL.
func RepoFromURL(raw string) string {
	s := strings.TrimSpace(strings.ToLower(raw))
	s = strings.TrimPrefix(strings.TrimPrefix(s, "https://"), "http://")
	s = strings.TrimPrefix(s, "github.com/")
	s = strings.Trim(s, "/")
	parts := strings.Split(s, "/")
	if len(parts) >= 2 && parts[0] != "" && parts[1] != "" {
		return parts[0] + "/" + parts[1]
	}
	return ""
}

type reactions struct {
	TotalCount int64 `json:"total_count"`
	PlusOne    int64 `json:"+1"`
	MinusOne   int64 `json:"-1"`
	Confused   int64 `json:"confused"`
	Heart      int64 `json:"heart"`
}

type issue struct {
	Number    int64     `json:"number"`
	Title     string    `json:"title"`
	Body      string    `json:"body"`
	State     string    `json:"state"`
	Comments  int64     `json:"comments"`
	CreatedAt string    `json:"created_at"`
	HTMLURL   string    `json:"html_url"`
	Reactions reactions `json:"reactions"`
	User      struct {
		Login   string `json:"login"`
		HTMLURL string `json:"html_url"`
	} `json:"user"`
	Labels []struct {
		Name string `json:"name"`
	} `json:"labels"`
	// Present only on pull requests. Issues and PRs share an endpoint and a
	// number space, and a PR is a proposed FIX rather than a report of pain —
	// including them would fill the archive with patch discussion.
	PullRequest *struct{} `json:"pull_request"`
}

type searchResponse struct {
	TotalCount int     `json:"total_count"`
	Items      []issue `json:"items"`
	Message    string  `json:"message"`
}

// Issue is one reported problem worth ingesting.
type Issue struct {
	Number   int64
	Title    string
	Comments int64
	State    string
	Negative int64
}

// ListIssues returns up to `limit` issues from a repo, busiest first.
//
// Sorted by comment count rather than recency: a new issue is nobody's problem
// yet, while one people are still arguing about is live pain.
func (c *Client) ListIssues(ctx context.Context, repo string, limit int) ([]Issue, error) {
	repo = RepoFromURL(repo)
	if repo == "" {
		return nil, fmt.Errorf("not an owner/repo: %q", repo)
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100 // the API's per-page ceiling
	}
	v := url.Values{}
	v.Set("q", fmt.Sprintf("repo:%s is:issue", repo))
	v.Set("sort", "comments")
	v.Set("order", "desc")
	v.Set("per_page", strconv.Itoa(limit))
	body, err := c.get(ctx, apiBase+"/search/issues?"+v.Encode())
	if err != nil {
		return nil, fmt.Errorf("github search %s: %w", repo, err)
	}
	var resp searchResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("github search decode: %w", err)
	}
	if resp.Message != "" {
		return nil, fmt.Errorf("github: %s", resp.Message)
	}
	out := make([]Issue, 0, limit)
	for _, it := range resp.Items {
		if it.Number == 0 || it.PullRequest != nil {
			continue
		}
		if it.Comments < int64(c.MinComments) {
			continue
		}
		out = append(out, Issue{
			Number: it.Number, Title: it.Title, Comments: it.Comments,
			State: it.State, Negative: it.Reactions.MinusOne,
		})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no github issues in %s with >=%d comments",
			repo, c.MinComments)
	}
	return out, nil
}

type comment struct {
	ID        int64     `json:"id"`
	Body      string    `json:"body"`
	CreatedAt string    `json:"created_at"`
	HTMLURL   string    `json:"html_url"`
	Reactions reactions `json:"reactions"`
	User      struct {
		Login   string `json:"login"`
		HTMLURL string `json:"html_url"`
	} `json:"user"`
}

// commentFrom maps reaction counts onto the shared shape.
//
// `+1` is the upvote and `-1` is the downvote — the only genuine downvote any
// adapter in this system can supply. `confused` is kept in Extra rather than
// folded into either: it means "this is unclear", which is neither agreement
// nor objection, and merging it would invent a reading nobody expressed.
func reactionFields(c *FetchedComment, r reactions) {
	if r.PlusOne > 0 || r.TotalCount > 0 {
		c.Upvotes = reddit.I64(r.PlusOne)
	}
	if r.MinusOne > 0 || r.TotalCount > 0 {
		c.Downvotes = reddit.I64(r.MinusOne)
	}
	// Score is the net: what a ranker should sort on.
	c.Score = r.PlusOne - r.MinusOne
	if r.Confused > 0 || r.Heart > 0 {
		if c.Extra == nil {
			c.Extra = map[string]any{}
		}
		if r.Confused > 0 {
			c.Extra["confused"] = r.Confused
		}
		if r.Heart > 0 {
			c.Extra["heart"] = r.Heart
		}
	}
}

// FetchIssue returns the issue body as the first comment, then its comments.
func (c *Client) FetchIssue(ctx context.Context, repo string, number int64) ([]FetchedComment, *FetchedPost, error) {
	repo = RepoFromURL(repo)
	if repo == "" {
		return nil, nil, fmt.Errorf("not an owner/repo: %q", repo)
	}

	raw, err := c.get(ctx, fmt.Sprintf("%s/repos/%s/issues/%d", apiBase, repo, number))
	if err != nil {
		return nil, nil, fmt.Errorf("github issue %s#%d: %w", repo, number, err)
	}
	var it issue
	if err := json.Unmarshal(raw, &it); err != nil {
		return nil, nil, fmt.Errorf("github issue decode: %w", err)
	}
	if it.Number == 0 {
		return nil, nil, fmt.Errorf("github issue %s#%d not found", repo, number)
	}

	var out []FetchedComment
	// Issue bodies are markdown, not HTML. htmltext.Plain still helps: many
	// contain inline HTML blocks, and it collapses them rather than leaving
	// tags in the text an embedder will see.
	if text := htmltext.Plain(it.Body); text != "" {
		fc := FetchedComment{
			ID:         fmt.Sprintf("gh:%s:i%d", repo, it.Number),
			PlatformID: strconv.FormatInt(it.Number, 10),
			Body:       strings.TrimSpace(it.Title + "\n\n" + text),
			Author:     it.User.Login,
			AuthorURL:  it.User.HTMLURL,
			CreatedAt:  reddit.NormalizeTime(it.CreatedAt),
			Permalink:  it.HTMLURL,
			Depth:      0,
			Replies:    reddit.I64(it.Comments),
			Extra:      map[string]any{"kind": "issue", "state": it.State},
		}
		reactionFields(&fc, it.Reactions)
		out = append(out, fc)
	}

	cRaw, cErr := c.get(ctx, fmt.Sprintf(
		"%s/repos/%s/issues/%d/comments?per_page=100", apiBase, repo, number))
	if cErr == nil {
		var comments []comment
		if json.Unmarshal(cRaw, &comments) == nil {
			for _, cm := range comments {
				text := htmltext.Plain(cm.Body)
				if text == "" {
					continue
				}
				fc := FetchedComment{
					ID:         fmt.Sprintf("gh:%s:c%d", repo, cm.ID),
					PlatformID: strconv.FormatInt(cm.ID, 10),
					Body:       text,
					Author:     cm.User.Login,
					AuthorURL:  cm.User.HTMLURL,
					CreatedAt:  reddit.NormalizeTime(cm.CreatedAt),
					Permalink:  cm.HTMLURL,
					ParentID:   strconv.FormatInt(it.Number, 10),
					Depth:      1,
					Extra:      map[string]any{"kind": "comment"},
				}
				reactionFields(&fc, cm.Reactions)
				out = append(out, fc)
			}
		}
	}

	labels := make([]string, 0, len(it.Labels))
	for _, l := range it.Labels {
		labels = append(labels, l.Name)
	}
	post := &FetchedPost{
		ID:           strconv.FormatInt(it.Number, 10),
		Title:        it.Title,
		URL:          it.HTMLURL,
		Body:         htmltext.Plain(it.Body),
		Author:       it.User.Login,
		AuthorURL:    it.User.HTMLURL,
		CreatedAt:    reddit.NormalizeTime(it.CreatedAt),
		CommentCount: reddit.I64(it.Comments),
		Upvotes:      reddit.I64(it.Reactions.PlusOne),
		Downvotes:    reddit.I64(it.Reactions.MinusOne),
		Score:        reddit.I64(it.Reactions.PlusOne - it.Reactions.MinusOne),
		Community:    repo,
		CommunityURL: "https://github.com/" + repo,
		Tags:         labels,
		Kind:         "issue",
		// An OLD, OPEN, heavily-commented issue is pain the maintainers could
		// not or would not fix.
		Closed: it.State == "closed",
		Extra:  map[string]any{"state": it.State},
	}
	return out, post, nil
}

// IssueURL is where a reviewer reads the thread a nugget came from.
func IssueURL(repo string, number int64) string {
	return fmt.Sprintf("https://github.com/%s/issues/%d", RepoFromURL(repo), number)
}
