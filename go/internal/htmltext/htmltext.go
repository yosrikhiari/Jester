// Package htmltext turns the HTML bodies forum APIs return into the plain
// text the pre-filter and extractor expect.
//
// Both Hacker News (Algolia `text`) and Discourse (`cooked`) hand back rendered
// HTML. Feeding that straight into the pipeline would let markup inflate the
// character counts §5's heuristics gate on, and would put tag soup in front of
// the extractor — so it is stripped once, here, rather than in each adapter.
package htmltext

import (
	"html"
	"regexp"
	"strings"
)

var (
	// Dropped with their contents: they are never comment prose. Written as an
	// explicit alternation because Go's RE2 has no backreferences, so the
	// tidier `<(tag)>…</\1>` form does not compile.
	dropBlocks = regexp.MustCompile(`(?is)` +
		`<script\b[^>]*>.*?</\s*script\s*>` +
		`|<style\b[^>]*>.*?</\s*style\s*>` +
		`|<pre\b[^>]*>.*?</\s*pre\s*>` +
		`|<code\b[^>]*>.*?</\s*code\s*>`)
	// Block-level boundaries become newlines so sentences do not run together.
	breaks = regexp.MustCompile(`(?i)<(br\s*/?|/p|/div|/li|/h[1-6]|/blockquote)\s*>`)
	tags   = regexp.MustCompile(`(?s)<[^>]*>`)
	spaces = regexp.MustCompile(`[ \t\x{00a0}]+`)
	blanks = regexp.MustCompile(`\n{3,}`)
)

// Plain renders HTML as text: entities decoded, markup gone, whitespace sane.
func Plain(s string) string {
	if s == "" {
		return ""
	}
	s = dropBlocks.ReplaceAllString(s, " ")
	s = breaks.ReplaceAllString(s, "\n")
	s = tags.ReplaceAllString(s, "")
	s = html.UnescapeString(s)
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = spaces.ReplaceAllString(s, " ")
	// Trim each line before collapsing runs, so indentation does not survive
	// as leading spaces the char-count heuristics would then count.
	lines := strings.Split(s, "\n")
	for i, ln := range lines {
		lines[i] = strings.TrimSpace(ln)
	}
	s = blanks.ReplaceAllString(strings.Join(lines, "\n"), "\n\n")
	return strings.TrimSpace(s)
}
