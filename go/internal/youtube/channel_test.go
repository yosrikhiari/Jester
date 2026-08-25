package youtube

import (
	"strings"
	"testing"
)

func TestChannelVideosURLAppendsOnce(t *testing.T) {
	cases := map[string]string{
		"https://www.youtube.com/@fosdem":         "https://www.youtube.com/@fosdem/videos",
		"https://www.youtube.com/@fosdem/":        "https://www.youtube.com/@fosdem/videos",
		"https://www.youtube.com/@fosdem/videos":  "https://www.youtube.com/@fosdem/videos",
		"https://www.youtube.com/@fosdem/videos/": "https://www.youtube.com/@fosdem/videos",
	}
	for in, want := range cases {
		if got := ChannelVideosURL(in); got != want {
			t.Errorf("ChannelVideosURL(%q)=%q want %q", in, got, want)
		}
	}
}

// A channel source used to be handed straight to FetchVideoComments, which
// scraped the listing page (no comments there). The listing selector is what
// closes that gap, so keep it pinned.
//
// Calibrated against live YouTube through cloakserve: the grid renders
// ytd-rich-item-renderer wrapping yt-lockup-view-model and the legacy
// a#video-title-link id matches NOTHING, so the generic /watch?v= fallback is
// the part that actually finds videos. Losing it silently breaks every
// channel source again.
func TestVideoHrefsJSTargetsCalibratedSelectors(t *testing.T) {
	for _, needle := range []string{
		"ytd-rich-item-renderer",
		"yt-lockup-view-model",
		"ytd-video-renderer",
		`a[href*="/watch?v="]`,
	} {
		if !strings.Contains(VIDEO_HREFS_JS, needle) {
			t.Errorf("VIDEO_HREFS_JS missing %q", needle)
		}
	}
}

func TestChannelChallengeREMatchesLiveInterstitials(t *testing.T) {
	for _, page := range []string{"Just a moment...", "Prove your humanity", "CAPTCHA"} {
		if !challengeRE.MatchString(page) {
			t.Errorf("challengeRE missed %q", page)
		}
	}
	if challengeRE.MatchString("FOSDEM - YouTube") {
		t.Error("challengeRE false-positived on a normal channel title")
	}
}
