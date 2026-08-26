// Package youtube: M3.1 Go port (§37.26) of the calibrated YouTube extraction
// (python/jester/fetchers/youtube.py). Comments lazy-load via /youtubei/v1/next
// XHRs carrying commentEntityPayload mutations; DOM extraction is the fallback.
package youtube

import (
	"regexp"
	"strings"

	"jester/internal/reddit"
)

// FetchedComment aliases the shared batch comment shape so both platform
// packages (and the store) speak one dialect.
type FetchedComment = reddit.FetchedComment

// Fingerprint delegates to the shared sha1(id-or-body)[:16] implementation —
// byte-identical to Python by construction (§37.21 lesson: one dialect only).
func Fingerprint(c FetchedComment) string { return reddit.Fingerprint(c) }

// NEXT_XHR_MARK selects the lazy-load comment responses.
const NEXT_XHR_MARK = "/youtubei/v1/next"

var challengeRE = regexp.MustCompile(`(?i)captcha|challenge|access denied|blocked|unusual traffic|prove your humanity|verify you are human|just a moment|are you a robot|rate limit`)

// WalkCommentPayloads collects every commentEntityPayload dict nested anywhere
// under frameworkUpdates.entityBatchUpdate.mutations[].payload (and tolerates
// deeper nesting).
func WalkCommentPayloads(node any, out *[]map[string]any) {
	switch v := node.(type) {
	case map[string]any:
		if payload, ok := v["payload"].(map[string]any); ok {
			if cep, ok := payload["commentEntityPayload"].(map[string]any); ok {
				*out = append(*out, cep)
			}
		}
		for _, child := range v {
			WalkCommentPayloads(child, out)
		}
	case []any:
		for _, child := range v {
			WalkCommentPayloads(child, out)
		}
	}
}

// CommentsFromPayloads maps raw entity payloads into FetchedComments.
//
// The XHR route is the fallback; the DOM is primary (cloakserve's CDP returns
// empty bodies for /youtubei/v1/next — see the package doc). It reads the same
// detail the DOM path does, so a fall back does not quietly downgrade the
// archive to bodies-only.
//
// Like counts come from the toolbar entity and are recorded when present. A
// missing toolbar leaves them nil rather than zero (R29): "nobody liked it"
// and "the payload did not carry it" are different facts.
func CommentsFromPayloads(payload map[string]any) []FetchedComment {
	var ceps []map[string]any
	WalkCommentPayloads(payload, &ceps)
	out := make([]FetchedComment, 0, len(ceps))
	for _, cep := range ceps {
		key, _ := cep["key"].(string)
		props, _ := cep["properties"].(map[string]any)
		var body, published string
		replyLevel := 0
		if props != nil {
			if content, ok := props["content"].(map[string]any); ok {
				body, _ = content["content"].(string)
			}
			published, _ = props["publishedTime"].(string)
			if lvl, ok := props["replyLevel"].(float64); ok {
				replyLevel = int(lvl)
			}
		}
		body = strings.TrimSpace(body)
		if body == "" {
			continue
		}
		author, _ := cep["author"].(map[string]any)
		name, _ := author["displayName"].(string)
		id := key
		if id == "" {
			id = name + body
		}
		c := FetchedComment{
			ID:         id,
			PlatformID: key,
			Body:       body,
			Author:     strings.TrimSpace(name),
			CreatedRaw: strings.TrimSpace(published),
			Depth:      replyLevel,
		}
		if author != nil {
			if ch, ok := author["channelId"].(string); ok && ch != "" {
				c.AuthorURL = "https://www.youtube.com/channel/" + ch
			}
			if creator, ok := author["isCreator"].(bool); ok && creator {
				c.AuthorIsOP = true
			}
		}
		if toolbar, ok := cep["toolbar"].(map[string]any); ok {
			if likes, ok := toolbar["likeCountNotliked"].(string); ok {
				if n, ok := ParseCount(likes); ok {
					c.Likes = reddit.I64(n)
					c.Score = n
				}
			}
			if replies, ok := toolbar["replyCount"].(string); ok {
				if n, ok := ParseCount(replies); ok {
					c.Replies = reddit.I64(n)
				}
			}
		}
		out = append(out, c)
	}
	return out
}
