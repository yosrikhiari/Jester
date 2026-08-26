// §37.21 Phase B: calibrated shreddit DOM extraction, ported 1:1 from the
// Python adapter (fetchers/cloak.py). shreddit is server-rendered — comments
// ship as <shreddit-comment thingid score> elements with the body in the
// inner .md node; /svc/shreddit/graphql returns stubs during SSR loads.
package reddit

import (
	"crypto/sha1"
	"encoding/hex"
	"strconv"
	"strings"
)

// DOMJS evaluates inside the loaded thread page and returns normalized rows.
//
// The attribute list is not guesswork: it is what a live r/selfhosted thread
// actually carried when this was calibrated. Everything published is taken —
// the previous version read three attributes (thingid, body, score) off an
// element carrying twenty, so who said it, when, where it sits in the tree and
// how many awards it drew were all thrown away at the point of capture and
// could never be recovered downstream.
//
// `score` is Reddit's fuzzed net score. There is no per-comment downvote
// attribute to read — the site stopped publishing one in 2014 — so none is
// invented here; see fetched.go.
const DOMJS = `() => [...document.querySelectorAll('shreddit-comment')].map(n => {
    const at = k => n.getAttribute(k);
    const timeEl = n.querySelector('time');
    return {
      id: at('thingid') || '',
      body: ((n.querySelector('.md') || {}).innerText || '').trim(),
      score: at('score') || '',
      author: at('author') || '',
      created: at('created') || (timeEl ? timeEl.getAttribute('datetime') : '') || '',
      permalink: at('permalink') || '',
      depth: at('depth') || '',
      postid: at('postid') || '',
      parent_positions: at('comment-parent-positions') || '',
      position: at('comment-position') || '',
      award_count: at('award-count') || '',
      content_type: at('content-type') || '',
      distinguished: at('is-mod-distinguished') === 'true',
      edited: !!n.querySelector('[data-testid="comment-edited"], .edited-flair'),
      collapsed: n.hasAttribute('collapsed'),
      deleted: at('deleted') === 'true',
      replies: n.querySelectorAll(':scope > shreddit-comment').length
    };
}).filter(c => c.body)`

// POST_META_JS reads the thread's own metadata off <shreddit-post>.
//
// Nothing read this before: a batch carried a bare thread_id, so the archive
// could not say what the discussion was about, who started it, how big it got,
// or how contested it was. `upvote-ratio` in particular is the only downvote
// signal Reddit publishes anywhere.
const POST_META_JS = `() => {
  const p = document.querySelector('shreddit-post');
  if (!p) return null;
  const at = k => p.getAttribute(k) || '';
  return {
    id: at('id'),
    title: at('post-title'),
    // permalink is the DISCUSSION; content-href is whatever the post links
    // to (an i.redd.it image, an external article). Using content-href as the
    // post URL sent a reader to the picture rather than the thread.
    url: at('permalink'),
    target_url: at('content-href'),
    author: at('author'),
    created: at('created-timestamp'),
    score: at('score'),
    upvote_ratio: at('upvote-ratio'),
    comment_count: at('comment-count'),
    award_count: at('award-count'),
    community: at('subreddit-prefixed-name') || at('subreddit-name'),
    community_id: at('subreddit-id'),
    language: at('post-language'),
    kind: at('post-type'),
    domain: at('domain'),
    item_state: at('item-state'),
    flair: (() => { const f = p.querySelector('shreddit-post-flair, .post-flair');
                    return f ? f.innerText.trim() : ''; })(),
    body: (() => { const b = p.querySelector('[slot="text-body"] .md, .md');
                   return b ? b.innerText.trim().slice(0, 4000) : ''; })()
  };
}`

// PERMALINK_JS discovers the first post permalink on a listing page.
const PERMALINK_JS = `() => { const p = document.querySelector('shreddit-post[permalink]'); return p ? p.getAttribute('permalink') : null; }`

// PERMALINKS_JS discovers every post permalink on a listing page, in feed
// order, WITH the comment count the listing already renders beside it.
//
// The count is the point. Hacker News and Discourse both skip a thread that is
// too quiet to be worth a fetch, because their listings carry a count; Reddit's
// listing carries one too (`comment-count` on <shreddit-post>) and this read it
// and threw it away. A scheduled run then spent its whole two-post budget on
// two r/hiredev threads advertising 0 and 1 comments against a floor of 8,
// fetched both, kept nothing, and ended with 27 sources unvisited.
const PERMALINKS_JS = `() => [...document.querySelectorAll('shreddit-post[permalink]')]
    .map(p => ({
      permalink: p.getAttribute('permalink') || '',
      comments: p.getAttribute('comment-count') || ''
    }))
    .filter(x => x.permalink)`

// Fingerprint derives the cross-language dedup key: hex(sha1(id-or-body))[:16],
// byte-identical to Python's fetchers.cloak fingerprints.
func Fingerprint(c FetchedComment) string {
	source := c.ID
	if source == "" {
		source = c.Body
	}
	sum := sha1.Sum([]byte(source))
	return hex.EncodeToString(sum[:])[:16]
}

// AbsURL turns a Reddit-relative path into an absolute one; anything already
// absolute (or empty) is returned untouched.
func AbsURL(path string) string {
	p := strings.TrimSpace(path)
	if p == "" || strings.Contains(p, "://") {
		return p
	}
	if !strings.HasPrefix(p, "/") {
		p = "/" + p
	}
	return "https://www.reddit.com" + p
}

// atoiNode reads an integer out of a JS-evaluated value, which arrives as a
// string for attributes and a float64 for computed numbers. The bool reports
// whether a number was actually THERE — an absent attribute must stay absent
// rather than becoming a published zero.
func atoiNode(v any) (int64, bool) {
	switch t := v.(type) {
	case string:
		s := strings.TrimSpace(t)
		if s == "" {
			return 0, false
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			f, ferr := strconv.ParseFloat(s, 64)
			if ferr != nil {
				return 0, false
			}
			return int64(f), true
		}
		return n, true
	case float64:
		return int64(t), true
	case int:
		return int64(t), true
	case int64:
		return t, true
	}
	return 0, false
}

func atofNode(v any) (float64, bool) {
	switch t := v.(type) {
	case string:
		s := strings.TrimSpace(t)
		if s == "" {
			return 0, false
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return 0, false
		}
		return f, true
	case float64:
		return t, true
	}
	return 0, false
}

func strNode(n map[string]any, key string) string {
	s, _ := n[key].(string)
	return strings.TrimSpace(s)
}

func boolNode(n map[string]any, key string) bool {
	switch t := n[key].(type) {
	case bool:
		return t
	case string:
		return t == "true" || t == "1"
	}
	return false
}

// CommentsFromNodes maps raw chromedp-evaluated rows into FetchedComment.
// Tolerates both attribute spellings seen in the wild (id/thingid).
func CommentsFromNodes(nodes []map[string]any) []FetchedComment {
	out := make([]FetchedComment, 0, len(nodes))
	for _, n := range nodes {
		id := strNode(n, "id")
		if id == "" {
			id = strNode(n, "thingid")
		}
		body := strNode(n, "body")
		if body == "" {
			continue
		}
		c := FetchedComment{
			ID:            id,
			PlatformID:    id,
			Body:          body,
			Author:        strNode(n, "author"),
			CreatedAt:     normalizeTime(strNode(n, "created")),
			Permalink:     AbsURL(strNode(n, "permalink")),
			Distinguished: boolNode(n, "distinguished"),
			Edited:        boolNode(n, "edited"),
			Hidden:        boolNode(n, "collapsed") || boolNode(n, "deleted"),
		}
		if a := c.Author; a != "" {
			c.AuthorURL = "https://www.reddit.com/user/" + a + "/"
		}
		if score, ok := atoiNode(n["score"]); ok {
			c.Score = score
			// Reddit's score IS its published upvote figure — one fuzzed net
			// number, with no separate up/down breakdown to record.
			c.Upvotes = I64(score)
		}
		if d, ok := atoiNode(n["depth"]); ok {
			c.Depth = int(d)
		}
		if r, ok := atoiNode(n["replies"]); ok {
			c.Replies = I64(r)
		}
		if a, ok := atoiNode(n["award_count"]); ok {
			c.Awards = I64(a)
		}
		// The parent chain is published as a JSON array of ancestor positions;
		// the LAST entry is the immediate parent. An empty array is a
		// top-level comment, which is a fact worth keeping as such.
		if pp := strNode(n, "parent_positions"); pp != "" && pp != "[]" {
			c.ParentID = pp
		}
		if pid := strNode(n, "postid"); pid != "" {
			c.Extra = putExtra(c.Extra, "post_id", pid)
		}
		if ct := strNode(n, "content_type"); ct != "" {
			c.Extra = putExtra(c.Extra, "content_type", ct)
		}
		if pos := strNode(n, "position"); pos != "" {
			c.Extra = putExtra(c.Extra, "position", pos)
		}
		out = append(out, c)
	}
	return out
}

// PostFromNode maps the POST_META_JS row into a FetchedPost. A nil row (no
// <shreddit-post> on the page) yields nil rather than an empty shell, so a
// reader can tell "not captured" from "captured and blank".
func PostFromNode(n map[string]any) *FetchedPost {
	if n == nil {
		return nil
	}
	p := &FetchedPost{
		ID:           strNode(n, "id"),
		Title:        strNode(n, "title"),
		URL:          AbsURL(strNode(n, "url")),
		Body:         strNode(n, "body"),
		Author:       strNode(n, "author"),
		CreatedAt:    normalizeTime(strNode(n, "created")),
		Community:    strNode(n, "community"),
		Language:     strNode(n, "language"),
		Kind:         strNode(n, "kind"),
		CommunityURL: communityURL(strNode(n, "community")),
	}
	if p.Author != "" {
		p.AuthorURL = "https://www.reddit.com/user/" + p.Author + "/"
	}
	if v, ok := atoiNode(n["score"]); ok {
		p.Score = I64(v)
		p.Upvotes = I64(v)
	}
	// The one genuine downvote signal on any of these platforms.
	if v, ok := atofNode(n["upvote_ratio"]); ok {
		p.UpvoteRatio = F64(v)
	}
	if v, ok := atoiNode(n["comment_count"]); ok {
		p.CommentCount = I64(v)
	}
	if v, ok := atoiNode(n["award_count"]); ok {
		p.Awards = I64(v)
	}
	if f := strNode(n, "flair"); f != "" {
		p.Tags = []string{f}
	}
	// A link/image post's target is a fact about the post, kept alongside
	// rather than in place of the discussion URL.
	if t := strNode(n, "target_url"); t != "" && t != p.URL {
		p.Extra = putExtra(p.Extra, "target_url", t)
	}
	for _, k := range []string{"domain", "item_state", "community_id"} {
		if v := strNode(n, k); v != "" {
			p.Extra = putExtra(p.Extra, k, v)
		}
	}
	if p.ID == "" && p.Title == "" && p.URL == "" {
		return nil
	}
	return p
}

func communityURL(name string) string {
	n := strings.TrimSpace(name)
	if n == "" {
		return ""
	}
	n = strings.TrimPrefix(n, "r/")
	return "https://www.reddit.com/r/" + n + "/"
}

// putExtra appends to the long-tail map, allocating on first use so an adapter
// with nothing extra to say serialises no `extra` key at all.
func putExtra(m map[string]any, k string, v any) map[string]any {
	if m == nil {
		m = map[string]any{}
	}
	m[k] = v
	return m
}
