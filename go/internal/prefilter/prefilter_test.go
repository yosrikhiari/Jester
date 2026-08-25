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
