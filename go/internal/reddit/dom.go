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
const DOMJS = `() => [...document.querySelectorAll('shreddit-comment')].map(n => ({
    id: n.getAttribute('thingid') || '',
    body: ((n.querySelector('.md') || {}).innerText || '').trim(),
    score: n.getAttribute('score') || '0',
})).filter(c => c.body)`

// PERMALINK_JS discovers the first post permalink on a listing page.
const PERMALINK_JS = `() => { const p = document.querySelector('shreddit-post[permalink]'); return p ? p.getAttribute('permalink') : null; }`

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

// CommentsFromNodes maps raw chromedp-evaluated rows into FetchedComment.
// Tolerates both attribute spellings seen in the wild (id/thingid).
func CommentsFromNodes(nodes []map[string]any) []FetchedComment {
	out := make([]FetchedComment, 0, len(nodes))
	for _, n := range nodes {
		id, _ := n["id"].(string)
		if id == "" {
			id, _ = n["thingid"].(string)
		}
		body, _ := n["body"].(string)
		body = strings.TrimSpace(body)
		if body == "" {
			continue
		}
		score := 0
		switch v := n["score"].(type) {
		case string:
			score, _ = strconv.Atoi(strings.TrimSpace(v))
		case float64:
			score = int(v)
		case int:
			score = v
		}
		out = append(out, FetchedComment{ID: id, Body: body, Score: int64(score)})
	}
	return out
}
