// Package lemmy ingests Lemmy communities through the public API.
//
// WHY THIS SOURCE. It is Reddit-shaped — communities, posts, threaded comments,
// votes — behind an open, documented, keyless API on servers that are glad to
// be read. That makes it the structural hedge against the single dependency
// that killed this category's leader: the week Reddit blocks a fingerprint,
// this is the thing that still works.
//
// It is also, on the one axis that matters most, BETTER data than Reddit.
// Reddit stopped publishing per-comment downvotes in 2014 and fuzzes the score
// it does show. Lemmy publishes both sides separately, on posts and comments
// alike:
//
//	counts: {score: 779, upvotes: 791, downvotes: 12, comments: 58}
//
// So the hedge is not a downgrade. Together with GitHub reactions this is the
// second source in the system able to fill FetchedComment.Downvotes at all.
//
// FEDERATION, and why sources name an instance. Every instance carries its own
// communities plus whatever it federates. Reading lemmy.world and
// programming.dev separately does NOT double-count: comments carry an `ap_id`
// naming the instance that actually hosts them, which is what the fingerprint
// is built from.
package lemmy

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

// FetchedPost is the shared thread shape.
type FetchedPost = reddit.FetchedPost

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

type Client struct {
	Get Getter
	// MinComments skips posts with too little discussion to be worth a fetch.
	MinComments int
	// Delay paces multi-request walks.
	Delay time.Duration
}

func New() *Client { return &Client{Get: httpGet, MinComments: 3} }

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

// InstanceAndCommunity splits a source URL into its two parts.
//
//	https://programming.dev/c/rust  -> ("https://programming.dev", "rust")
//	https://lemmy.world             -> ("https://lemmy.world", "")
//
// An empty community means "this instance's active posts, whatever the
// community" — useful for a general-purpose instance, wasteful for a large one.
func InstanceAndCommunity(raw string) (string, string) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return "", ""
	}
	if !strings.Contains(s, "://") {
		s = "https://" + s
	}
	s = strings.TrimRight(s, "/")
	community := ""
	if i := strings.Index(s, "/c/"); i > 0 {
		community = strings.Trim(s[i+3:], "/")
		// A federated community reads as `name@instance`; the API wants only
		// the part before the @ when querying its own host.
		if j := strings.Index(community, "/"); j >= 0 {
			community = community[:j]
		}
		s = s[:i]
	}
	return s, community
}

type counts struct {
	Comments   int64 `json:"comments"`
	Score      int64 `json:"score"`
	Upvotes    int64 `json:"upvotes"`
	Downvotes  int64 `json:"downvotes"`
	ChildCount int64 `json:"child_count"`
}

type person struct {
	Name    string `json:"name"`
	ActorID string `json:"actor_id"`
}

type postView struct {
	Post struct {
		ID        int64  `json:"id"`
		Name      string `json:"name"`
		Body      string `json:"body"`
		URL       string `json:"url"`
		APID      string `json:"ap_id"`
		Published string `json:"published"`
		Locked    bool   `json:"locked"`
		Deleted   bool   `json:"deleted"`
		Removed   bool   `json:"removed"`
		NSFW      bool   `json:"nsfw"`
	} `json:"post"`
	Counts    counts `json:"counts"`
	Creator   person `json:"creator"`
	Community struct {
		Name    string `json:"name"`
		Title   string `json:"title"`
		ActorID string `json:"actor_id"`
	} `json:"community"`
}

type postListResponse struct {
	Posts []postView `json:"posts"`
	Error string     `json:"error"`
}

// Post is one discussion worth fetching.
type Post struct {
	ID       int64
	Title    string
	Comments int64
	Score    int64
}

// ListPosts returns up to `limit` active posts, optionally from one community.
func (c *Client) ListPosts(ctx context.Context, source string, limit int) ([]Post, error) {
	instance, community := InstanceAndCommunity(source)
	if instance == "" {
		return nil, fmt.Errorf("empty lemmy instance url")
	}
	if limit < 1 {
		limit = 1
	}
	out := make([]Post, 0, limit)
	// Dedup ACROSS pages. Two things make this necessary rather than tidy: an
	// instance that ignores `page` would otherwise return the same post on
	// every request and fill the result with copies of it, and `Active` sort
	// genuinely reorders between requests, so a post can legitimately appear
	// on page 1 and again on page 2.
	seen := make(map[int64]bool, limit)
	// 50 is the API's per-page ceiling.
	pageSize := limit
	if pageSize > 50 {
		pageSize = 50
	}
	for page := 1; page <= 6 && len(out) < limit; page++ {
		v := url.Values{}
		// Active, not New: a post people are still arguing about is live pain,
		// while the newest post is usually nobody's problem yet.
		v.Set("sort", "Active")
		v.Set("limit", strconv.Itoa(pageSize))
		v.Set("page", strconv.Itoa(page))
		if community != "" {
			v.Set("community_name", community)
		}
		body, err := c.get(ctx, instance+"/api/v3/post/list?"+v.Encode())
		if err != nil {
			if len(out) > 0 {
				break
			}
			return nil, fmt.Errorf("lemmy post list: %w", err)
		}
		var resp postListResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			if len(out) > 0 {
				break
			}
			return nil, fmt.Errorf("lemmy post list decode: %w", err)
		}
		if resp.Error != "" {
			return nil, fmt.Errorf("lemmy: %s", resp.Error)
		}
		if len(resp.Posts) == 0 {
			break
		}
		before := len(out)
		for _, pv := range resp.Posts {
			if pv.Post.ID == 0 || pv.Post.Deleted || pv.Post.Removed {
				continue
			}
			if seen[pv.Post.ID] {
				continue
			}
			seen[pv.Post.ID] = true
			if pv.Counts.Comments < int64(c.MinComments) {
				continue
			}
			out = append(out, Post{
				ID: pv.Post.ID, Title: pv.Post.Name,
				Comments: pv.Counts.Comments, Score: pv.Counts.Score,
			})
			if len(out) == limit {
				break
			}
		}
		// A page that added nothing new means either the floor rejected all of
		// it or the server is re-serving the same window. Either way another
		// page of the same is not worth a request.
		if len(out) == before {
			break
		}
		if c.Delay > 0 && len(out) < limit {
			select {
			case <-time.After(c.Delay):
			case <-ctx.Done():
				return out, ctx.Err()
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no lemmy posts at %s with >=%d comment(s)",
			source, c.MinComments)
	}
	return out, nil
}

type commentView struct {
	Comment struct {
		ID            int64  `json:"id"`
		Content       string `json:"content"`
		Path          string `json:"path"`
		APID          string `json:"ap_id"`
		Published     string `json:"published"`
		Updated       string `json:"updated"`
		Deleted       bool   `json:"deleted"`
		Removed       bool   `json:"removed"`
		Distinguished bool   `json:"distinguished"`
	} `json:"comment"`
	Counts  counts `json:"counts"`
	Creator person `json:"creator"`
}

type commentListResponse struct {
	Comments []commentView `json:"comments"`
	Error    string        `json:"error"`
}

// depthFromPath reads nesting out of Lemmy's materialised path.
//
// `0.25589421` is top level; `0.25589421.25589500` is one reply deep. Counting
// segments is exact and free, where Reddit's equivalent had to be inferred.
func depthFromPath(path string) (int, string) {
	parts := strings.Split(strings.TrimSpace(path), ".")
	// Drop the leading "0" root and the comment's own id.
	if len(parts) < 2 {
		return 0, ""
	}
	depth := len(parts) - 2
	parent := ""
	if depth > 0 {
		parent = parts[len(parts)-2]
	}
	return depth, parent
}

// FetchPost returns the post body as the first comment, then its comments.
func (c *Client) FetchPost(ctx context.Context, source string, postID int64) ([]FetchedComment, *FetchedPost, error) {
	instance, _ := InstanceAndCommunity(source)
	if instance == "" {
		return nil, nil, fmt.Errorf("empty lemmy instance url")
	}

	v := url.Values{}
	v.Set("id", strconv.FormatInt(postID, 10))
	raw, err := c.get(ctx, instance+"/api/v3/post?"+v.Encode())
	if err != nil {
		return nil, nil, fmt.Errorf("lemmy post %d: %w", postID, err)
	}
	var single struct {
		PostView postView `json:"post_view"`
		Error    string   `json:"error"`
	}
	if err := json.Unmarshal(raw, &single); err != nil {
		return nil, nil, fmt.Errorf("lemmy post %d decode: %w", postID, err)
	}
	if single.Error != "" {
		return nil, nil, fmt.Errorf("lemmy: %s", single.Error)
	}
	pv := single.PostView
	if pv.Post.ID == 0 {
		return nil, nil, fmt.Errorf("lemmy post %d not found", postID)
	}

	permalink := pv.Post.APID
	if permalink == "" {
		permalink = fmt.Sprintf("%s/post/%d", instance, pv.Post.ID)
	}

	var out []FetchedComment
	if text := htmltext.Plain(pv.Post.Body); text != "" || pv.Post.Name != "" {
		body := strings.TrimSpace(pv.Post.Name + "\n\n" + text)
		out = append(out, FetchedComment{
			ID:         "lemmy:" + permalink,
			PlatformID: strconv.FormatInt(pv.Post.ID, 10),
			Body:       body,
			Score:      pv.Counts.Score,
			Upvotes:    reddit.I64(pv.Counts.Upvotes),
			Downvotes:  reddit.I64(pv.Counts.Downvotes),
			Author:     pv.Creator.Name,
			AuthorURL:  pv.Creator.ActorID,
			CreatedAt:  reddit.NormalizeTime(pv.Post.Published),
			Permalink:  permalink,
			Depth:      0,
			Replies:    reddit.I64(pv.Counts.Comments),
			Extra:      map[string]any{"kind": "post", "community": pv.Community.Name},
		})
	}

	cv := url.Values{}
	cv.Set("post_id", strconv.FormatInt(postID, 10))
	cv.Set("sort", "Top")
	cv.Set("limit", "100")
	// Replies three levels down are still someone describing a problem, so
	// depth is not a reason to drop them.
	cv.Set("max_depth", "8")
	cRaw, cErr := c.get(ctx, instance+"/api/v3/comment/list?"+cv.Encode())
	if cErr == nil {
		var cResp commentListResponse
		if json.Unmarshal(cRaw, &cResp) == nil && cResp.Error == "" {
			for _, item := range cResp.Comments {
				if item.Comment.Deleted || item.Comment.Removed {
					continue
				}
				text := htmltext.Plain(item.Comment.Content)
				if text == "" {
					continue
				}
				depth, parent := depthFromPath(item.Comment.Path)
				link := item.Comment.APID
				if link == "" {
					link = fmt.Sprintf("%s/comment/%d", instance, item.Comment.ID)
				}
				out = append(out, FetchedComment{
					// The federated ap_id, not the local numeric id: the same
					// comment carries different local ids on every instance
					// that federates it, so hashing the local one would let
					// one comment into the archive once per instance read.
					ID:            "lemmy:" + link,
					PlatformID:    strconv.FormatInt(item.Comment.ID, 10),
					Body:          text,
					Score:         item.Counts.Score,
					Upvotes:       reddit.I64(item.Counts.Upvotes),
					Downvotes:     reddit.I64(item.Counts.Downvotes),
					Author:        item.Creator.Name,
					AuthorURL:     item.Creator.ActorID,
					CreatedAt:     reddit.NormalizeTime(item.Comment.Published),
					Permalink:     link,
					ParentID:      parent,
					Depth:         depth,
					Replies:       reddit.I64(item.Counts.ChildCount),
					Distinguished: item.Comment.Distinguished,
					Edited:        item.Comment.Updated != "",
					Extra:         map[string]any{"kind": "comment"},
				})
			}
		}
	}

	post := &FetchedPost{
		ID:           strconv.FormatInt(pv.Post.ID, 10),
		Title:        pv.Post.Name,
		URL:          permalink,
		Body:         htmltext.Plain(pv.Post.Body),
		Author:       pv.Creator.Name,
		AuthorURL:    pv.Creator.ActorID,
		CreatedAt:    reddit.NormalizeTime(pv.Post.Published),
		Score:        reddit.I64(pv.Counts.Score),
		Upvotes:      reddit.I64(pv.Counts.Upvotes),
		Downvotes:    reddit.I64(pv.Counts.Downvotes),
		CommentCount: reddit.I64(pv.Counts.Comments),
		Community:    pv.Community.Name,
		CommunityURL: pv.Community.ActorID,
		Kind:         "post",
		Closed:       pv.Post.Locked,
		Extra:        map[string]any{"nsfw": pv.Post.NSFW},
	}
	if pv.Post.URL != "" && pv.Post.URL != permalink {
		post.Extra["target_url"] = pv.Post.URL
	}
	return out, post, nil
}

// PostURL is where a reviewer reads the thread a nugget came from.
func PostURL(source string, id int64) string {
	instance, _ := InstanceAndCommunity(source)
	return fmt.Sprintf("%s/post/%d", instance, id)
}
