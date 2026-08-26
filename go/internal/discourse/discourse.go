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
	"regexp"
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
	// Delay paces multi-page listing walks. Zero in tests, the configured
	// request delay in production.
	Delay time.Duration
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
	ID    int64
	Slug  string
	Title string
	Posts int
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
		// Present while more pages exist; absent on the last one. Cheaper and
		// more truthful than inferring the end from a short page, which also
		// happens when a page is all pinned topics.
		MoreTopicsURL string `json:"more_topics_url"`
	} `json:"topic_list"`
}

// LikeActionID is the "like" entry in a post's actions_summary. Discourse
// numbers its post actions and 2 is Like; a stock install has no downvote
// action at all, which is why Dislikes and Downvotes stay nil for this
// platform rather than being recorded as zero.
const LikeActionID = 2

// : Discourse serves /latest.json 30 topics at a time and advertises the next
// : page in `more_topics_url`. Reading one page and stopping capped this
// : adapter at 18 qualifying topics on meta.discourse.org — measured, and
// : silent: asking for 100 or 300 returned the same 18.
const topicsPerPage = 30

// : Ceiling on pages walked in one call, so a `limit` set far beyond what a
// : forum holds cannot turn into an unbounded crawl. 34 pages ~ 1,000 topics,
// : which is past any sane per-run depth.
const maxTopicPages = 34

// ListTopics returns up to `limit` recent topics with enough discussion,
// paginating until it has them or the forum runs out.
//
// Pacing between pages is the caller's `Delay` (0 for tests): this is a public
// API being read as intended, but reading it as fast as the loop can go is
// still rude, and Discourse rate-limits anonymous clients.
func (c *Client) ListTopics(ctx context.Context, base string, limit int) ([]Topic, error) {
	base = BaseURL(base)
	if base == "" {
		return nil, fmt.Errorf("empty discourse base url")
	}
	if limit < 1 {
		limit = 1
	}
	out := make([]Topic, 0, limit)
	seen := make(map[int64]bool, limit)

	// Walk until we have `limit`, the forum runs out, or a hard page ceiling.
	//
	// Deliberately NOT a page budget computed from `limit`: that assumes
	// almost every topic qualifies, and on a quiet forum most are filtered by
	// MinPosts, so the walk stopped short while pages remained. The
	// qualification rate is not knowable in advance — only walking reveals it.
	//
	// `barren` bounds the other direction: a forum with pages of nothing
	// useful should be abandoned, not crawled to the ceiling.
	const maxBarrenPages = 3
	barren := 0

	for page := 0; page < maxTopicPages; page++ {
		// no_definitions drops the pinned "welcome"/"how to ask" boilerplate
		// that every forum pins and nobody is describing a problem in.
		u := fmt.Sprintf("%s/latest.json?no_definitions=true&page=%d", base, page)
		body, err := c.get(ctx, u)
		if err != nil {
			// A later page failing is not a failed call: keep what the
			// earlier ones yielded rather than losing the whole walk.
			if len(out) > 0 {
				break
			}
			return nil, fmt.Errorf("discourse latest: %w", err)
		}
		var resp latestResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			if len(out) > 0 {
				break
			}
			return nil, fmt.Errorf("discourse latest decode: %w", err)
		}
		if len(resp.TopicList.Topics) == 0 {
			break // ran off the end of the forum
		}
		before := len(out)
		for _, t := range resp.TopicList.Topics {
			if t.ID == 0 || t.Pinned || t.PostsCount < c.MinPosts || seen[t.ID] {
				continue
			}
			seen[t.ID] = true
			out = append(out, Topic{
				ID: t.ID, Slug: t.Slug, Title: t.Title, Posts: t.PostsCount,
			})
			if len(out) == limit {
				return out, nil
			}
		}
		if len(out) == before {
			barren++
			if barren >= maxBarrenPages {
				break
			}
		} else {
			barren = 0
		}
		// The forum told us there is no next page.
		if resp.TopicList.MoreTopicsURL == "" {
			break
		}
		if c.Delay > 0 {
			select {
			case <-time.After(c.Delay):
			case <-ctx.Done():
				return out, ctx.Err()
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no discourse topics at %s with >=%d posts", base, c.MinPosts)
	}
	return out, nil
}

type postJSON struct {
	ID                int64   `json:"id"`
	Cooked            string  `json:"cooked"`
	Username          string  `json:"username"`
	Name              string  `json:"name"`
	DisplayUsername   string  `json:"display_username"`
	UserID            int64   `json:"user_id"`
	CreatedAt         string  `json:"created_at"`
	UpdatedAt         string  `json:"updated_at"`
	PostNumber        int     `json:"post_number"`
	ReplyCount        int64   `json:"reply_count"`
	ReplyToPostNumber *int    `json:"reply_to_post_number"`
	QuoteCount        int64   `json:"quote_count"`
	IncomingLinks     int64   `json:"incoming_link_count"`
	Reads             int64   `json:"reads"`
	ReadersCount      int64   `json:"readers_count"`
	Score             float64 `json:"score"`
	TrustLevel        int     `json:"trust_level"`
	Admin             bool    `json:"admin"`
	Moderator         bool    `json:"moderator"`
	Staff             bool    `json:"staff"`
	Hidden            bool    `json:"hidden"`
	Wiki              bool    `json:"wiki"`
	UserDeleted       bool    `json:"user_deleted"`
	AcceptedAnswer    bool    `json:"accepted_answer"`
	EditReason        string  `json:"edit_reason"`
	Version           int     `json:"version"`
	PostURL           string  `json:"post_url"`
	PostType          int     `json:"post_type"`
	UserTitle         string  `json:"user_title"`
	ReactionUsers     int64   `json:"reaction_users_count"`
	ActionsSummary    []struct {
		ID    int   `json:"id"`
		Count int64 `json:"count"`
	} `json:"actions_summary"`
}

type topicResponse struct {
	ID               int64    `json:"id"`
	Title            string   `json:"title"`
	Slug             string   `json:"slug"`
	CreatedAt        string   `json:"created_at"`
	LastPostedAt     string   `json:"last_posted_at"`
	PostsCount       int64    `json:"posts_count"`
	ReplyCount       int64    `json:"reply_count"`
	Views            int64    `json:"views"`
	LikeCount        int64    `json:"like_count"`
	ParticipantCount int64    `json:"participant_count"`
	WordCount        int64    `json:"word_count"`
	CategoryID       int64    `json:"category_id"`
	Tags             []string `json:"tags"`
	Closed           bool     `json:"closed"`
	Archived         bool     `json:"archived"`
	Pinned           bool     `json:"pinned"`
	HasAccepted      bool     `json:"has_accepted_answer"`
	Archetype        string   `json:"archetype"`
	Locale           string   `json:"locale"`
	PostStream       struct {
		Posts []postJSON `json:"posts"`
	} `json:"post_stream"`
}

// quoteBlock matches Discourse's rendered quote: an <aside class="quote ...">
// wrapping the quoted user's avatar, name and words.
//
// (?is) so it spans newlines and ignores case; non-greedy so two quotes in one
// post do not swallow everything between them.
// `quote` as a whole space-delimited class token, NOT a substring:
// Discourse renders class="quote" and class="quote no-group", while
// `blockquote` also contains the letters "quote" and must not match.
var quoteBlock = regexp.MustCompile(
	`(?is)<aside[^>]*class="(?:[^"]*\s)?quote(?:\s[^"]*)?".*?</aside>`)

// bbcodeQuote is the older markup that still appears in imported posts.
var bbcodeQuote = regexp.MustCompile(`(?is)\[quote[^\]]*\].*?\[/quote\]`)

// stripQuotes removes text this person did not write.
//
// Discourse discussions quote each other constantly. Left in, the quoted words
// are embedded as though the quoting poster had said them — so a thread agrees
// with itself and a cluster forms out of one person's sentence repeated by
// three others. Observed directly: a live cluster whose three "independent"
// members were one original post and two people quoting it.
//
// Attribution goes with the quote. "Falco: we should do a splash contest" is
// not this poster's complaint, and the original is already captured as its own
// post — keeping both double-counts one opinion.
func stripQuotes(cooked string) string {
	out := quoteBlock.ReplaceAllString(cooked, " ")
	return bbcodeQuote.ReplaceAllString(out, " ")
}

// likeCount reads the like tally out of actions_summary, and reports whether
// one was published at all. A payload carrying no summary is not a post that
// nobody liked.
func likeCount(p postJSON) (int64, bool) {
	for _, a := range p.ActionsSummary {
		if a.ID == LikeActionID {
			return a.Count, true
		}
	}
	return 0, false
}

// FetchPosts returns a topic's posts as batch comments, plus the topic itself.
//
// Discourse publishes far more per post than the three fields this used to
// read: who wrote it and when, how many people read it, how many replies and
// quotes it drew, whether it was accepted as the answer, whether it was
// edited. All of it is captured now. None of it was recoverable later.
func (c *Client) FetchPosts(ctx context.Context, base string, topicID int64) ([]FetchedComment, *reddit.FetchedPost, error) {
	base = BaseURL(base)
	u := base + "/t/" + url.PathEscape(strconv.FormatInt(topicID, 10)) + ".json"
	body, err := c.get(ctx, u)
	if err != nil {
		return nil, nil, fmt.Errorf("discourse topic %d: %w", topicID, err)
	}
	var resp topicResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, nil, fmt.Errorf("discourse topic %d decode: %w", topicID, err)
	}
	out := make([]FetchedComment, 0, len(resp.PostStream.Posts))
	for _, p := range resp.PostStream.Posts {
		text := htmltext.Plain(stripQuotes(p.Cooked))
		if text == "" {
			// A post that was ONLY a quote has nothing of its own to say.
			continue
		}
		author := p.Username
		if author == "" {
			author = p.DisplayUsername
		}
		fc := FetchedComment{
			ID:         "discourse:" + strconv.FormatInt(p.ID, 10),
			PlatformID: strconv.FormatInt(p.ID, 10),
			Body:       text,
			// Discourse `score` is an engagement composite, not an upvote
			// count — carried through as the engagement signal rather than
			// relabelled as votes it is not.
			Score:     int64(p.Score),
			Author:    author,
			CreatedAt: reddit.NormalizeTime(p.CreatedAt),
			Permalink: postURL(base, p, resp),
			Replies:   reddit.I64(p.ReplyCount),
			Reads:     reddit.I64(p.Reads),
			// post_number 1 is the topic's opening post, i.e. the OP.
			AuthorIsOP:    p.PostNumber == 1,
			Distinguished: p.Admin || p.Moderator || p.Staff,
			Accepted:      p.AcceptedAnswer,
			Hidden:        p.Hidden || p.UserDeleted,
			// version starts at 1; anything above it means the post was edited.
			Edited: p.Version > 1 || p.EditReason != "",
		}
		if author != "" {
			fc.AuthorURL = base + "/u/" + author
		}
		if n, ok := likeCount(p); ok {
			fc.Likes = reddit.I64(n)
		}
		if p.ReplyToPostNumber != nil {
			fc.ParentID = strconv.Itoa(*p.ReplyToPostNumber)
			// Nesting depth is not published — only WHICH post this replies
			// to. Recording 1 for "is a reply" and 0 for "is not" says exactly
			// what the API said and nothing more.
			fc.Depth = 1
		}
		fc.Extra = map[string]any{
			"post_number":         p.PostNumber,
			"trust_level":         p.TrustLevel,
			"quote_count":         p.QuoteCount,
			"incoming_link_count": p.IncomingLinks,
			"readers_count":       p.ReadersCount,
			"reaction_users":      p.ReactionUsers,
			"post_type":           p.PostType,
			"wiki":                p.Wiki,
		}
		if p.UserTitle != "" {
			fc.Extra["user_title"] = p.UserTitle
		}
		if p.UpdatedAt != "" {
			fc.Extra["updated_at"] = reddit.NormalizeTime(p.UpdatedAt)
		}
		out = append(out, fc)
	}
	return out, topicPost(base, resp), nil
}

func postURL(base string, p postJSON, t topicResponse) string {
	if p.PostURL != "" {
		return base + p.PostURL
	}
	if t.Slug != "" {
		return fmt.Sprintf("%s/t/%s/%d/%d", base, t.Slug, t.ID, p.PostNumber)
	}
	return fmt.Sprintf("%s/t/%d/%d", base, t.ID, p.PostNumber)
}

// topicPost maps the topic envelope into the shared post shape.
func topicPost(base string, t topicResponse) *reddit.FetchedPost {
	if t.ID == 0 {
		return nil
	}
	host := strings.TrimPrefix(strings.TrimPrefix(base, "https://"), "http://")
	p := &reddit.FetchedPost{
		ID:           strconv.FormatInt(t.ID, 10),
		Title:        t.Title,
		URL:          TopicURL(base, Topic{ID: t.ID, Slug: t.Slug}),
		CreatedAt:    reddit.NormalizeTime(t.CreatedAt),
		CommentCount: reddit.I64(t.PostsCount),
		Views:        reddit.I64(t.Views),
		// Likes are the whole engagement story here: Discourse ships no
		// downvote, so Dislikes stays nil rather than claiming zero.
		Likes:        reddit.I64(t.LikeCount),
		Participants: reddit.I64(t.ParticipantCount),
		Community:    host,
		CommunityURL: base,
		Tags:         t.Tags,
		Language:     t.Locale,
		Kind:         t.Archetype,
		Closed:       t.Closed,
		Archived:     t.Archived,
		Pinned:       t.Pinned,
		Extra: map[string]any{
			"category_id":         t.CategoryID,
			"word_count":          t.WordCount,
			"reply_count":         t.ReplyCount,
			"has_accepted_answer": t.HasAccepted,
			"last_posted_at":      reddit.NormalizeTime(t.LastPostedAt),
		},
	}
	if len(t.PostStream.Posts) > 0 {
		op := t.PostStream.Posts[0]
		p.Author = op.Username
		p.Body = htmltext.Plain(stripQuotes(op.Cooked))
		if p.Author != "" {
			p.AuthorURL = base + "/u/" + p.Author
		}
	}
	return p
}

// TopicURL is where a reviewer can read the thread a nugget came from.
func TopicURL(base string, t Topic) string {
	base = BaseURL(base)
	if t.Slug != "" {
		return fmt.Sprintf("%s/t/%s/%d", base, t.Slug, t.ID)
	}
	return fmt.Sprintf("%s/t/%d", base, t.ID)
}
