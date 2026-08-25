package config

import (
	"os"
	"path/filepath"
	"testing"
)

func loadSourcesYAML(t *testing.T, body string) []Source {
	t.Helper()
	dir := writeConfigDir(t)
	if err := os.WriteFile(filepath.Join(dir, "sources.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := LoadConfig(dir)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	return cfg.Sources.Sources
}

func TestSourceEnabledDefaultsToTrueWhenAbsent(t *testing.T) {
	got := loadSourcesYAML(t, `sources:
  - platform: reddit
    name: legacy
    url: https://www.reddit.com/r/selfhosted/
  - platform: reddit
    name: parked
    kind: subreddit
    url: https://www.reddit.com/r/homelab/
    enabled: false
`)
	if len(got) != 2 {
		t.Fatalf("want 2 sources, got %d", len(got))
	}
	if !got[0].IsEnabled() {
		t.Fatal("a source without an `enabled` key must count as enabled")
	}
	if got[1].IsEnabled() {
		t.Fatal("enabled: false must disable the source")
	}
}

func TestResolvedKindInfersFromURLShape(t *testing.T) {
	cases := []struct {
		platform, url, want string
	}{
		{"reddit", "https://www.reddit.com/r/selfhosted/", "subreddit"},
		{"reddit", "https://www.reddit.com/r/selfhosted/comments/1abc2d/x/", "thread"},
		{"youtube", "https://www.youtube.com/@fosdem", "channel"},
		{"youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video"},
		{"youtube", "https://youtu.be/dQw4w9WgXcQ", "video"},
		{"tiktok", "https://www.tiktok.com/@indiehackers", "profile"},
		{"tiktok", "https://www.tiktok.com/@x/video/7300000000000000000", "video"},
		{"tiktok", "https://www.tiktok.com/tag/buildinpublic", "hashtag"},
	}
	for _, c := range cases {
		s := Source{Platform: c.platform, URL: c.url}
		if got := s.ResolvedKind(); got != c.want {
			t.Errorf("%s %s: kind=%q want %q", c.platform, c.url, got, c.want)
		}
	}
}

func TestExplicitKindWinsOverInference(t *testing.T) {
	s := Source{Platform: "reddit", Kind: "thread", URL: "https://www.reddit.com/r/x/"}
	if s.ResolvedKind() != "thread" {
		t.Fatalf("explicit kind must win, got %q", s.ResolvedKind())
	}
}

func TestMaxThreadsPerSourceDefaultsWhenAbsent(t *testing.T) {
	cfg, err := LoadConfig(writeConfigDir(t))
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	// The fixture thresholds.yaml predates the key; validation fills the
	// documented default rather than failing the range check on 0.
	if cfg.Thresholds.MaxThreadsPerSource != 1 {
		t.Fatalf("want default 1, got %d", cfg.Thresholds.MaxThreadsPerSource)
	}
}
