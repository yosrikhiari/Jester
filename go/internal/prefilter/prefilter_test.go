package prefilter

import "testing"

func TestAnalyze(t *testing.T) {
	s := Analyze("Check out @bob https://x.com 👍👍👍 nice idea here")
	if s.Mentions != 1 {
		t.Errorf("mentions: got %d", s.Mentions)
	}
	if s.Links != 1 {
		t.Errorf("links: got %d", s.Links)
	}
	if s.Emoji == 0 {
		t.Errorf("emoji: got %d, want >0", s.Emoji)
	}
	if !HasLetter("nice idea here") {
		t.Errorf("HasLetter should detect letters")
	}
}

func TestKeep(t *testing.T) {
	p := Params{MinChars: 40, MaxChars: 600, MaxEmoji: 5, MaxMentions: 3, MinWords: 6}
	cases := []struct {
		body   string
		keep   bool
		reason string
	}{
		{"I really wish there was a simpler way to self host my email without all the yaml", true, ""},
		{"too short", false, "too_short"},
		{"👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍 👍", false, "emoji_spam"},
		{"@a @b @c @d this is definitely mention spam from many handles here", false, "mention_spam"},
		{"1234 5678 9012 3456 7890 1234 5678 9012 3456 7890", false, "no_letters"},
	}
	for _, c := range cases {
		got, r := Keep(c.body, p)
		if got != c.keep || (c.keep == false && r != c.reason) {
			t.Errorf("Keep(%q)=(%v,%q) want (%v,%q)", c.body, got, r, c.keep, c.reason)
		}
	}
}

// Automated posts reach the archive as if they were people describing
// problems. Three texts in the real archive appear multiple times under
// different comment ids — the skip-list correctly keeps them, because they ARE
// distinct comments, and five bots agreeing is the exact shape of a strong
// signal.
func TestBoilerplateFromTheRealArchiveIsDropped(t *testing.T) {
	// Verbatim from data/jester.db, with the counts they appeared at.
	seen := []struct {
		times int
		body  string
	}{
		{5, "Expand the replies to this comment to learn how AI was used in this post/project."},
		{2, "AI usage disclosure provided by OP, see the reply to this comment for details."},
		{5, "Would you be willing to chat privately with a colleague of mine about this?"},
	}
	for _, c := range seen {
		if bot, _ := IsBoilerplate(c.body); !bot {
			t.Errorf("appeared %dx in the archive and was not caught: %.60q", c.times, c.body)
		}
	}
}

func TestCommonBotFootersAreDropped(t *testing.T) {
	for _, body := range []string{
		"I am a bot, and this action was performed automatically. Please contact the moderators.",
		"Beep boop. I am a helpful robot.",
		"Your submission has been removed because it broke rule 3.",
		"https://example.com/some/link",
	} {
		if bot, _ := IsBoilerplate(body); !bot {
			t.Errorf("not caught: %.60q", body)
		}
	}
}

// The far more important direction: a filter that eats real complaints is
// worse than no filter, because what it removed is invisible.
func TestRealComplaintsSurviveTheBoilerplateFilter(t *testing.T) {
	for _, body := range []string{
		"My backup script fails silently every time I upgrade and I never notice until it matters.",
		"I built a bot to monitor this and even that keeps breaking.",
		"The docs say this action was performed automatically but it absolutely was not.",
		"Has anyone automated this? I am a bot enthusiast but this defeats me.",
		"Check https://example.com/docs — it is wrong about the restore procedure and cost me a day.",
	} {
		if bot, why := IsBoilerplate(body); bot {
			t.Errorf("ate a real complaint (%s): %.70q", why, body)
		}
	}
}

func TestBoilerplateReasonIsReported(t *testing.T) {
	// A filter nobody can audit is a filter nobody should trust; the run's
	// funnel shows these counts.
	_, why := IsBoilerplate("I am a bot, and this action was performed automatically.")
	if why != "bot_phrase" {
		t.Errorf("want bot_phrase, got %q", why)
	}
	if _, why := IsBoilerplate("https://example.com/x"); why != "bot_pattern" {
		t.Errorf("want bot_pattern, got %q", why)
	}
}

func TestKeepDropsBoilerplateThroughTheNormalPath(t *testing.T) {
	p := Params{MinChars: 10, MaxChars: 4000, MaxEmoji: 5, MaxMentions: 3, MinWords: 3}
	ok, why := Keep(
		"Expand the replies to this comment to learn how AI was used in this post.", p)
	if ok {
		t.Fatal("boilerplate reached the archive")
	}
	if why != "bot_phrase" {
		t.Errorf("reason should name the rule, got %q", why)
	}
}

// The tightened rules must actually FIRE. An earlier version of these regexes
// contained a literal backspace byte where \b was intended: they compiled,
// matched nothing, and every "real complaints survive" test passed because the
// rules were dead rather than because they were correct.
func TestClauseAnchoredBotRulesActuallyFire(t *testing.T) {
	for _, body := range []string{
		"I am a bot. Here is a summary of that article.",
		"I'm a bot, and I converted the units for you.",
		"This action was performed automatically. Contact the moderator team.",
	} {
		if bot, why := IsBoilerplate(body); !bot {
			t.Errorf("clause-anchored rule did not fire on %.60q (why=%q)", body, why)
		}
	}
}
