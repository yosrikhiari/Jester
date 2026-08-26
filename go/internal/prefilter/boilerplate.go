package prefilter

import (
	"regexp"
	"strings"
)

// Automated and templated posts reach the archive as if they were people
// describing problems.
//
// This is not hypothetical. Three texts in this archive appear multiple times
// under different comment ids — the fingerprint skip-list correctly keeps them,
// because they ARE distinct comments, and they then read as several independent
// reports of the same pain:
//
//	x5  "expand the replies to this comment to learn how ai was used…"
//	x5  "would you be willing to chat privately with a colleague of m…"
//	x2  "ai usage disclosure provided by op, see the reply to this co…"
//
// Five bot posts agreeing with each other is exactly the shape of a strong
// signal, and it is the shape a clustering pipeline is most likely to promote.
//
// WHY NOT A USERNAME BLOCKLIST. The same text arrives under many accounts, and
// the author is unknown for most of the archive anyway. Matching on the TEXT
// catches the behaviour rather than the actor.

// botPhrases are stock sentences that mark a post as automated or templated
// rather than authored. Matched case-insensitively as substrings, so each must
// be long and specific enough that a person would not write it by accident.
//
// Two shorter entries lived here in an earlier version and ate real
// complaints, which the tests caught before this shipped:
//
//	"this action was performed automatically" matched someone WRITING
//	    "the docs say this action was performed automatically but it was not"
//	"i am a bot"                              matched "I am a bot enthusiast"
//
// A filter that eats genuine material is worse than no filter, because what it
// removed is invisible — there is no funnel entry for "a real complaint we
// mistook for a robot". Both moved to clause-anchored patterns below.
var botPhrases = []string{
	// Moderation / disclosure bots
	"expand the replies to this comment to learn how ai was used",
	"ai usage disclosure provided by op",
	"i am a bot, and this action was performed automatically",
	"please contact the moderators of this subreddit",
	"your submission has been removed",
	"your post has been removed",
	"has been automatically removed",
	"beep boop",
	// Recruiting / solicitation templates
	"would you be willing to chat privately with a colleague",
	// Reposting and mirroring bots
	"here is a mirror of the",
}

// botPatterns catch shapes rather than exact wording.
var botPatterns = []*regexp.Regexp{
	// Reddit's small-text bot footer: ^(text like this).
	regexp.MustCompile(`(?i)\^\(.*bot.*\)`),
	// "***Beep boop***"-style emphasis around an automation notice.
	regexp.MustCompile(`(?i)\*{2,}\s*(beep|bot|automated)\b`),
	// A bare link and nothing else. Not automation exactly, but never someone
	// describing a problem either.
	regexp.MustCompile(`^\s*https?://\S+\s*$`),
	// "I am a bot." as a COMPLETE clause — a bot identifying itself — rather
	// than the opening of "I am a bot enthusiast". The trailing punctuation
	// class is what separates the two.
	regexp.MustCompile(`(?i)\bi(?:'m| am) a bot\s*[.,!:;]`),
	// The moderator footer, which attributes the action to the speaker. A
	// person complaining ABOUT that behaviour does not.
	regexp.MustCompile(`(?i)\bthis action was performed automatically\b.{0,40}\bmoderator`),
}

// IsBoilerplate reports whether a body reads as automated or templated, and
// says which rule fired.
//
// The reason is returned rather than swallowed so the run's prefilter funnel
// can show WHY things were dropped — a filter nobody can audit is a filter
// nobody should trust.
func IsBoilerplate(body string) (bool, string) {
	low := strings.ToLower(strings.TrimSpace(body))
	if low == "" {
		return false, ""
	}
	for _, phrase := range botPhrases {
		if strings.Contains(low, phrase) {
			return true, "bot_phrase"
		}
	}
	for _, rx := range botPatterns {
		if rx.MatchString(body) {
			return true, "bot_pattern"
		}
	}
	return false, ""
}
