// Package prefilter implements the cheap, deterministic pre-filter heuristics
// that run before any LLM call. These are pure functions so they can be unit
// tested without external services (§7.1 Unit layer).
package prefilter

import (
	"strings"
	"unicode"
)

// Stats summarises a candidate comment for filtering decisions.
type Stats struct {
	Chars    int
	Words    int
	Emoji    int
	Mentions int
	Links    int
}

// Analyze computes Stats for s.
func Analyze(s string) Stats {
	st := Stats{Chars: len([]rune(s))}
	fields := strings.Fields(s)
	st.Words = len(fields)
	for _, r := range s {
		if isEmoji(r) {
			st.Emoji++
		}
	}
	st.Mentions = strings.Count(s, "@")
	st.Links = strings.Count(strings.ToLower(s), "http")
	return st
}

// isEmoji reports whether r is outside the basic multilingual plane or a
// common pictographic range. Conservative: catches most emoji without a full
// table.
func isEmoji(r rune) bool {
	if r > 0xFFFF {
		return true
	}
	switch {
	case r >= 0x1F300 && r <= 0x1FAFF:
		return true
	case r >= 0x2600 && r <= 0x27BF:
		return true
	case r >= 0xFE00 && r <= 0xFE0F:
		return true
	}
	return false
}

// Params are the tunable thresholds for Keep.
type Params struct {
	MinChars    int
	MaxChars    int
	MaxEmoji    int
	MaxMentions int
	MinWords    int
}

// Keep reports whether a comment passes the pre-filter and is worth sending to
// the LLM. A kept comment is not necessarily a signal; it is simply not
// obviously junk.
func Keep(body string, p Params) (bool, string) {
	s := Analyze(body)
	if s.Chars < p.MinChars {
		return false, "too_short"
	}
	if s.Chars > p.MaxChars {
		return false, "too_long"
	}
	if s.Words < p.MinWords {
		return false, "too_few_words"
	}
	if s.Emoji > p.MaxEmoji {
		return false, "emoji_spam"
	}
	if s.Mentions > p.MaxMentions {
		return false, "mention_spam"
	}
	if !HasLetter(body) {
		return false, "no_letters"
	}
	// Checked LAST, and only on text that already looks like prose: the
	// phrase list is a substring scan, and running it on every scrap before
	// the cheap length checks would be wasted work on things already dropped.
	if bot, why := IsBoilerplate(body); bot {
		return false, why
	}
	return true, ""
}

// HasLetter reports whether s contains at least one unicode letter.
func HasLetter(s string) bool {
	for _, r := range s {
		if unicode.IsLetter(r) {
			return true
		}
	}
	return false
}
