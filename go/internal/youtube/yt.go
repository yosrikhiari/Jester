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
// Like counts are NOT fabricated (R29 spirit) — Score stays 0.
func CommentsFromPayloads(payload map[string]any) []FetchedComment {
	var ceps []map[string]any
	WalkCommentPayloads(payload, &ceps)
	out := make([]FetchedComment, 0, len(ceps))
	for _, cep := range ceps {
		key, _ := cep["key"].(string)
		props, _ := cep["properties"].(map[string]any)
		var body string
		if props != nil {
			if content, ok := props["content"].(map[string]any); ok {
				body, _ = content["content"].(string)
			}
		}
		body = strings.TrimSpace(body)
		if body == "" {
			continue
		}
		id := key
		if id == "" {
			author, _ := cep["author"].(map[string]any)
			name, _ := author["displayName"].(string)
			id = name + body
		}
		out = append(out, FetchedComment{ID: id, Body: body})
	}
	return out
}
