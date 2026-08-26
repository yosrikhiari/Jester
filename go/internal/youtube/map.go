package youtube

import (
	"strings"

	"jester/internal/reddit"
)

// FetchedPost is the shared thread/video shape (one dialect, §37.21).
type FetchedPost = reddit.FetchedPost

func str(n map[string]any, key string) string {
	s, _ := n[key].(string)
	return strings.TrimSpace(s)
}

func flag(n map[string]any, key string) bool {
	b, _ := n[key].(bool)
	return b
}

// CommentsFromDOM maps DOMJS rows into FetchedComments.
//
// ID is deliberately still author+body, NOT the `lc=` comment id the DOM now
// hands us. Fingerprint hashes ID, so switching it would give every YouTube
// comment already in the skip-list a new fingerprint and re-ingest the entire
// back catalogue as if it were new. The real id is kept alongside, in
// PlatformID, where it is just as useful and costs nothing.
func CommentsFromDOM(rows []map[string]any, videoURL string) []FetchedComment {
	out := make([]FetchedComment, 0, len(rows))
	for _, r := range rows {
		body := str(r, "body")
		if body == "" {
			continue
		}
		author := str(r, "author")
		c := FetchedComment{
			ID:         author + body,
			PlatformID: str(r, "comment_id"),
			Body:       body,
			Author:     author,
			AuthorURL:  ytURL(str(r, "author_url")),
			Permalink:  ytURL(str(r, "permalink")),
			// YouTube publishes ages, not timestamps ("1 year ago"). Keep its
			// words; converting to a date would invent a precision the page
			// never had, and the ingest time is already recorded separately.
			CreatedRaw: str(r, "published_raw"),
			Pinned:     flag(r, "pinned"),
			AuthorIsOP: flag(r, "author_is_uploader"),
		}
		if c.Permalink == "" && videoURL != "" && c.PlatformID != "" {
			c.Permalink = videoURL + "&lc=" + c.PlatformID
		}
		if n, ok := ParseCount(str(r, "likes")); ok {
			c.Likes = reddit.I64(n)
			// Likes ARE the engagement figure on YouTube; leaving Score at 0
			// ranked every YouTube comment below a one-upvote Reddit reply.
			c.Score = n
		}
		if n, ok := ParseCount(str(r, "replies")); ok {
			c.Replies = reddit.I64(n)
		}
		if flag(r, "hearted") {
			c.Extra = map[string]any{"creator_hearted": true}
		}
		if badge := str(r, "author_badge"); badge != "" {
			if c.Extra == nil {
				c.Extra = map[string]any{}
			}
			c.Extra["author_badge"] = badge
		}
		out = append(out, c)
	}
	return out
}

// PostFromMeta maps VIDEO_META_JS into the shared post shape.
func PostFromMeta(n map[string]any, videoURL string) *FetchedPost {
	if n == nil {
		return nil
	}
	p := &FetchedPost{
		ID:           VideoIDFromURL(videoURL),
		Title:        str(n, "title"),
		URL:          videoURL,
		Body:         str(n, "description"),
		Author:       str(n, "channel"),
		AuthorURL:    ytURL(str(n, "channel_url")),
		Community:    str(n, "channel"),
		CommunityURL: ytURL(str(n, "channel_url")),
		Kind:         "video",
		// `published` is the rendered upload date ("Feb 24, 2023"); ld+json's
		// uploadDate is the canonical ISO form, so prefer it and keep the
		// rendered one only when that is all there is.
		CreatedAt:  reddit.NormalizeTime(str(n, "upload_date")),
		CreatedRaw: firstNonEmpty(str(n, "published"), str(n, "info")),
	}
	if n, ok := ParseCount(str(n, "subscribers")); ok {
		p.Subscribers = reddit.I64(n)
	}
	if v, ok := ParseCount(str(n, "info")); ok {
		// "#info-container" reads "1.8B views  16y ago" — the first number is
		// the view count.
		p.Views = reddit.I64(v)
	}
	// The visible like label is abbreviated ("19M"); the aria-label carries
	// the exact figure ("like this video along with 19,352,172 other people").
	if v, ok := ParseCount(str(n, "like_label")); ok {
		p.Likes = reddit.I64(v)
		p.Score = reddit.I64(v)
	}
	// Dislikes stay nil ON PURPOSE. The label is "Dislike this video" with no
	// number — YouTube withdrew the count in December 2021 — so there is
	// nothing to record and nothing worth guessing.
	if v, ok := ParseCount(str(n, "comment_count")); ok {
		p.CommentCount = reddit.I64(v)
	}
	for _, k := range []string{"duration", "genre", "ld_views"} {
		if v := str(n, k); v != "" {
			if p.Extra == nil {
				p.Extra = map[string]any{}
			}
			p.Extra[k] = v
		}
	}
	if p.Title == "" && p.ID == "" {
		return nil
	}
	return p
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// ytURL absolutises a YouTube-relative href.
func ytURL(href string) string {
	h := strings.TrimSpace(href)
	if h == "" || strings.Contains(h, "://") {
		return h
	}
	if !strings.HasPrefix(h, "/") {
		h = "/" + h
	}
	return "https://www.youtube.com" + h
}
