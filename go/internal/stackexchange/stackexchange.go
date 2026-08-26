// Package stackexchange ingests Stack Exchange questions through the public
// API.
//
// WHY THIS SOURCE. 170-odd sites of "this does not work and here is exactly
// how", written by people who have already tried the obvious things. Three
// properties no forum thread has:
//
//	is_answered   whether the pain was ever resolved. An UNANSWERED question
//	              with high views is the strongest signal in this whole
//	              pipeline: many people hit it, nobody has a fix.
//	view_count    how many others arrived with the same problem.
//	tags          a free, human-assigned topic label.
//
// And unlike Reddit it is a documented, keyless, sanctioned API with a
// published quota — no stealth browser, no anti-bot circumvention, no DMCA
// §1201 surface. That is the whole point of adding it.
//
// QUOTA. 300 requests/day per IP unencrypted-keyless, 10,000 with a free key.
// Every response carries `quota_remaining`, which this reads and reports: a
// source that silently stops working at request 301 is worse than one that
// says how much room is left.
package stackexchange

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

const apiBase = "https://api.stackexchange.com/2.3"

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

type Client struct {
	Get Getter
	// MinAnswers skips questions with too little discussion to be worth a
	// fetch. Zero means no floor.
	MinAnswers int
	// Key is an optional API key. Without one the quota is 300/day per IP;
	// with one, 10,000. Never required.
	Key string
	// QuotaRemaining is what the last response reported. Read it rather than
	// discovering the ceiling by hitting it.
	QuotaRemaining int
}

// New reads an optional API key from STACKEXCHANGE_KEY.
//
// The quota arithmetic is why this matters. Each question costs two requests
// (the question, then its answers) plus one listing per site per run:
//
//	depth  3 ->   7 requests per site per run
//	depth  5 ->  11
//	depth 20 ->  41
//
// Against a KEYLESS quota of 300/day per IP, six sites on a 30-minute schedule
// exceed it even at depth 3. A free key — no account approval, no cost —
// raises it to 10,000/day, which makes the whole source comfortable. Without
// one the adapter still works; it just stops partway through the day, and
// says so via QuotaRemaining rather than failing mysteriously.
func New() *Client {
	return &Client{
		Get:        httpGet,
		MinAnswers: 0,
		Key:        strings.TrimSpace(os.Getenv("STACKEXCHANGE_KEY")),
	}
}

func httpGet(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	// Identify honestly: this is a public API being used as intended.
	req.Header.Set("User-Agent", "jester/1.0 (local research tool)")
	req.Header.Set("Accept", "application/json")
	// The API always gzips; net/http handles it transparently when we do not
	// set Accept-Encoding ourselves.
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

// SiteFromURL reads the API site slug out of a curated source URL.
// "https://serverfault.com" -> "serverfault"; a bare slug passes through.
func SiteFromURL(raw string) string {
	s := strings.TrimSpace(strings.ToLower(raw))
	s = strings.TrimPrefix(strings.TrimPrefix(s, "https://"), "http://")
	s = strings.TrimSuffix(strings.Trim(s, "/"), "/questions")
	if s == "" {
		return "stackoverflow"
	}
	host := strings.SplitN(s, "/", 2)[0]
	// stackoverflow.com -> stackoverflow; unix.stackexchange.com -> unix
	if strings.HasSuffix(host, ".stackexchange.com") {
		return strings.TrimSuffix(host, ".stackexchange.com")
	}
	if i := strings.Index(host, "."); i > 0 {
		return host[:i]
	}
	return host
}

type question struct {
	QuestionID   int64    `json:"question_id"`
	Title        string   `json:"title"`
	Body         string   `json:"body"`
	Score        int64    `json:"score"`
	AnswerCount  int64    `json:"answer_count"`
	ViewCount    int64    `json:"view_count"`
	IsAnswered   bool     `json:"is_answered"`
	CreationDate int64    `json:"creation_date"`
	Link         string   `json:"link"`
	Tags         []string `json:"tags"`
	Owner        struct {
		DisplayName string `json:"display_name"`
		Link        string `json:"link"`
	} `json:"owner"`
}

type questionsResponse struct {
	Items          []question `json:"items"`
	HasMore        bool       `json:"has_more"`
	QuotaRemaining int        `json:"quota_remaining"`
	ErrorMessage   string     `json:"error_message"`
}

// Question is one problem worth ingesting.
type Question struct {
	ID         int64
	Title      string
	Views      int64
	Answers    int64
	IsAnswered bool
}

func (c *Client) listURL(site string, page, pageSize int) string {
	v := url.Values{}
	v.Set("site", site)
	v.Set("order", "desc")
	// By ACTIVITY, not creation: a question people are still arguing about is
	// a live pain, while the newest question is usually nobody's problem yet.
	v.Set("sort", "activity")
	v.Set("pagesize", strconv.Itoa(pageSize))
	v.Set("page", strconv.Itoa(page))
	v.Set("filter", "withbody")
	if c.Key != "" {
		v.Set("key", c.Key)
	}
	return apiBase + "/questions?" + v.Encode()
}

// ListQuestions returns up to `limit` recently-active questions.
func (c *Client) ListQuestions(ctx context.Context, site string, limit int) ([]Question, error) {
	site = SiteFromURL(site)
	if limit < 1 {
		limit = 1
	}
	out := make([]Question, 0, limit)
	// 100 is the API's per-page maximum; asking for fewer just costs more
	// requests against a 300/day quota.
	pageSize := limit
	if pageSize > 100 {
		pageSize = 100
	}
	for page := 1; page <= 10 && len(out) < limit; page++ {
		body, err := c.get(ctx, c.listURL(site, page, pageSize))
		if err != nil {
			if len(out) > 0 {
				break // keep what earlier pages gave us
			}
			return nil, fmt.Errorf("stackexchange questions: %w", err)
		}
		var resp questionsResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			if len(out) > 0 {
				break
			}
			return nil, fmt.Errorf("stackexchange decode: %w", err)
		}
		if resp.ErrorMessage != "" {
			return nil, fmt.Errorf("stackexchange: %s", resp.ErrorMessage)
		}
		c.QuotaRemaining = resp.QuotaRemaining
		for _, q := range resp.Items {
			if q.QuestionID == 0 || q.AnswerCount < int64(c.MinAnswers) {
				continue
			}
			out = append(out, Question{
				ID: q.QuestionID, Title: q.Title, Views: q.ViewCount,
				Answers: q.AnswerCount, IsAnswered: q.IsAnswered,
			})
			if len(out) == limit {
				break
			}
		}
		if !resp.HasMore {
			break
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no stackexchange questions on %s with >=%d answers",
			site, c.MinAnswers)
	}
	return out, nil
}

type answer struct {
	AnswerID     int64  `json:"answer_id"`
	Body         string `json:"body"`
	Score        int64  `json:"score"`
	IsAccepted   bool   `json:"is_accepted"`
	CreationDate int64  `json:"creation_date"`
	Owner        struct {
		DisplayName string `json:"display_name"`
		Link        string `json:"link"`
	} `json:"owner"`
}

type answersResponse struct {
	Items          []answer `json:"items"`
	QuotaRemaining int      `json:"quota_remaining"`
	ErrorMessage   string   `json:"error_message"`
}

func (c *Client) questionURL(site string, id int64) string {
	v := url.Values{}
	v.Set("site", site)
	v.Set("filter", "withbody")
	if c.Key != "" {
		v.Set("key", c.Key)
	}
	return fmt.Sprintf("%s/questions/%d?%s", apiBase, id, v.Encode())
}

func (c *Client) answersURL(site string, id int64) string {
	v := url.Values{}
	v.Set("site", site)
	v.Set("order", "desc")
	v.Set("sort", "votes")
	v.Set("pagesize", "100")
	v.Set("filter", "withbody")
	if c.Key != "" {
		v.Set("key", c.Key)
	}
	return fmt.Sprintf("%s/questions/%d/answers?%s", apiBase, id, v.Encode())
}

// FetchQuestion returns the question body as the FIRST comment, then its
// answers.
//
// The question is the pain and the answers are the workarounds; both matter,
// and the question leads because that is the sentence a reader wants first.
func (c *Client) FetchQuestion(ctx context.Context, site string, id int64) ([]FetchedComment, *FetchedPost, error) {
	site = SiteFromURL(site)

	qBody, err := c.get(ctx, c.questionURL(site, id))
	if err != nil {
		return nil, nil, fmt.Errorf("stackexchange question %d: %w", id, err)
	}
	var qResp questionsResponse
	if err := json.Unmarshal(qBody, &qResp); err != nil {
		return nil, nil, fmt.Errorf("stackexchange question %d decode: %w", id, err)
	}
	if qResp.ErrorMessage != "" {
		return nil, nil, fmt.Errorf("stackexchange: %s", qResp.ErrorMessage)
	}
	c.QuotaRemaining = qResp.QuotaRemaining
	if len(qResp.Items) == 0 {
		return nil, nil, fmt.Errorf("stackexchange question %d not found", id)
	}
	q := qResp.Items[0]

	var out []FetchedComment
	if text := htmltext.Plain(q.Body); text != "" {
		// Title AND body: on Stack Exchange the title is usually the clearest
		// one-line statement of the problem, and dropping it loses the best
		// sentence in the post.
		full := strings.TrimSpace(q.Title + "\n\n" + text)
		out = append(out, FetchedComment{
			ID:         fmt.Sprintf("se:%s:q%d", site, q.QuestionID),
			PlatformID: strconv.FormatInt(q.QuestionID, 10),
			Body:       full,
			Score:      q.Score,
			Upvotes:    reddit.I64(q.Score),
			Author:     q.Owner.DisplayName,
			AuthorURL:  q.Owner.Link,
			CreatedAt:  reddit.FromUnix(q.CreationDate),
			Permalink:  q.Link,
			Depth:      0,
			Replies:    reddit.I64(q.AnswerCount),
			Reads:      reddit.I64(q.ViewCount),
			Extra: map[string]any{
				"kind":        "question",
				"is_answered": q.IsAnswered,
				"tags":        q.Tags,
			},
		})
	}

	aBody, err := c.get(ctx, c.answersURL(site, id))
	if err == nil {
		var aResp answersResponse
		if json.Unmarshal(aBody, &aResp) == nil && aResp.ErrorMessage == "" {
			c.QuotaRemaining = aResp.QuotaRemaining
			for _, a := range aResp.Items {
				text := htmltext.Plain(a.Body)
				if text == "" {
					continue
				}
				out = append(out, FetchedComment{
					ID:         fmt.Sprintf("se:%s:a%d", site, a.AnswerID),
					PlatformID: strconv.FormatInt(a.AnswerID, 10),
					Body:       text,
					Score:      a.Score,
					Upvotes:    reddit.I64(a.Score),
					Author:     a.Owner.DisplayName,
					AuthorURL:  a.Owner.Link,
					CreatedAt:  reddit.FromUnix(a.CreationDate),
					Permalink:  fmt.Sprintf("%s#%d", q.Link, a.AnswerID),
					ParentID:   strconv.FormatInt(q.QuestionID, 10),
					Depth:      1,
					Accepted:   a.IsAccepted,
					Extra:      map[string]any{"kind": "answer"},
				})
			}
		}
	}

	post := &FetchedPost{
		ID:           strconv.FormatInt(q.QuestionID, 10),
		Title:        q.Title,
		URL:          q.Link,
		Body:         htmltext.Plain(q.Body),
		Author:       q.Owner.DisplayName,
		AuthorURL:    q.Owner.Link,
		CreatedAt:    reddit.FromUnix(q.CreationDate),
		Score:        reddit.I64(q.Score),
		Upvotes:      reddit.I64(q.Score),
		CommentCount: reddit.I64(q.AnswerCount),
		Views:        reddit.I64(q.ViewCount),
		Community:    site,
		CommunityURL: "https://" + siteHost(site),
		Tags:         q.Tags,
		Kind:         "question",
		Extra: map[string]any{
			// The signal that makes this source worth having: many people
			// arrived with this problem and nobody has answered it.
			"is_answered": q.IsAnswered,
		},
	}
	return out, post, nil
}

// siteHost turns an API slug back into a browsable host.
func siteHost(site string) string {
	switch site {
	case "stackoverflow", "serverfault", "superuser", "askubuntu", "mathoverflow":
		return site + ".com"
	default:
		return site + ".stackexchange.com"
	}
}

// QuestionURL is where a reviewer reads the thread a nugget came from.
func QuestionURL(site string, id int64) string {
	return fmt.Sprintf("https://%s/questions/%d", siteHost(SiteFromURL(site)), id)
}
