package config

import (
	"os"
	"path/filepath"
	"testing"
)

func writeConfigDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	write := func(name, content string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("sources.yaml", "sources:\n  - platform: reddit\n    name: selfhosted\n    url: https://reddit.com/r/selfhosted\n")
	write("thresholds.yaml", `max_comments_per_thread: 500
min_upvotes: 1
dedup_threshold: 0.87
prefilter_min_chars: 40
prefilter_max_chars: 600
prefilter_max_emoji: 5
prefilter_max_mentions: 3
prefilter_min_words: 6
request_delay_ms: 2000
embedding_model: nomic-embed-text
models:
  extractor: llama3.1
  archivist: llama3.1
  synthesizer: llama3.1
  critic: llama3.1
`)
	write("scraper.yaml", "cdp_url: http://localhost:9222\nlicense_key: \"\"\nproxy: \"\"\ngeoip: us\nblocked_response_action: retry\n")
	return dir
}

func TestLoadConfigValid(t *testing.T) {
	cfg, err := LoadConfig(writeConfigDir(t))
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if len(cfg.Sources.Sources) != 1 {
		t.Fatalf("want 1 source, got %d", len(cfg.Sources.Sources))
	}
	if cfg.Thresholds.EmbeddingModel != "nomic-embed-text" {
		t.Fatalf("embedding model not parsed")
	}
}

func TestThresholdsRangeBounds(t *testing.T) {
	cases := []struct {
		name string
		mut  func(*Thresholds)
	}{
		{"min_chars_too_low", func(t *Thresholds) { t.PrefilterMinChars = 0 }},
		{"min_chars_gt_max", func(t *Thresholds) { t.PrefilterMinChars = 700 }},
		{"dedup_too_high", func(t *Thresholds) { t.DedupThreshold = 1.5 }},
		{"delay_too_low", func(t *Thresholds) { t.RequestDelayMs = 100 }},
		{"no_embedding", func(t *Thresholds) { t.EmbeddingModel = "" }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			th := DefaultThresholds()
			c.mut(&th)
			if err := th.Validate(); err == nil {
				t.Fatalf("expected validation error for %s", c.name)
			}
		})
	}
}

func TestDefaultThresholdsInBounds(t *testing.T) {
	th := DefaultThresholds()
	if err := th.Validate(); err != nil {
		t.Fatalf("defaults should validate: %v", err)
	}
}

func TestScraperExpandsEnvAndBlanksPlaceholders(t *testing.T) {
	dir := writeConfigDir(t)
	if err := os.WriteFile(filepath.Join(dir, "scraper.yaml"), []byte(
		"cdp_url: http://localhost:9222\n"+
			"license_key: ${JESTER_TEST_LICENCE}\n"+
			"proxy: ${JESTER_TEST_PROXY_UNSET}\n"+
			"geoip: ${JESTER_TEST_GEOIP}\n"+
			"timezone: America/New_York\n"+
			"locale: en-US\n"+
			"blocked_response_action: backoff\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("JESTER_TEST_LICENCE", "abc123")
	t.Setenv("JESTER_TEST_GEOIP", "true")

	cfg, err := LoadConfig(dir)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if cfg.Scraper.LicenseKey != "abc123" {
		t.Errorf("licence not expanded: %q", cfg.Scraper.LicenseKey)
	}
	// An unset placeholder must become empty, never the literal "${...}" —
	// Chrome takes that as a proxy address and fails every navigation with
	// ERR_PROXY_CONNECTION_FAILED.
	if cfg.Scraper.Proxy != "" {
		t.Errorf("unset proxy should be blank, got %q", cfg.Scraper.Proxy)
	}
	if !cfg.Scraper.GeoIPEnabled() {
		t.Error("geoip=true should enable geoip")
	}
	if cfg.Scraper.Timezone != "America/New_York" || cfg.Scraper.Locale != "en-US" {
		t.Errorf("identity not read: tz=%q locale=%q", cfg.Scraper.Timezone, cfg.Scraper.Locale)
	}
}

func TestGeoIPCountryCodeIsNotEnabled(t *testing.T) {
	// The old default was `geoip: us`, which cloakserve parses as false. Keep
	// that explicit so nobody re-adds a country code expecting it to work.
	if (Scraper{GeoIP: "us"}).GeoIPEnabled() {
		t.Error(`geoip:"us" must not count as enabled`)
	}
	for _, on := range []string{"true", "TRUE", "1", "yes", "on"} {
		if !(Scraper{GeoIP: on}).GeoIPEnabled() {
			t.Errorf("geoip:%q should be enabled", on)
		}
	}
}

// warmup_navigations was parsed into the Scraper struct and then read by
// nobody: the two Reddit call sites passed a hardcoded 2, so the documented
// "0 disables the warm-up" disabled nothing and raising the number did
// nothing either. These pin the resolution rule now that the worker uses it.
func TestWarmupsDistinguishesAbsentFromExplicitZero(t *testing.T) {
	var absent Scraper
	if got := absent.Warmups(); got != defaultWarmupNavigations {
		t.Errorf("absent key must keep warming (got %d, want %d)",
			got, defaultWarmupNavigations)
	}

	zero := 0
	if got := (Scraper{WarmupNavigations: &zero}).Warmups(); got != 0 {
		t.Errorf("an explicit 0 must disable the warm-up, got %d", got)
	}

	three := 3
	if got := (Scraper{WarmupNavigations: &three}).Warmups(); got != 3 {
		t.Errorf("a configured count must be honoured, got %d", got)
	}

	neg := -2
	if got := (Scraper{WarmupNavigations: &neg}).Warmups(); got != 0 {
		t.Errorf("a negative count must clamp to off, got %d", got)
	}
}

func TestScraperYAMLWarmupIsRead(t *testing.T) {
	cfg, err := Load("../../../config")
	if err != nil {
		t.Fatalf("load repo config: %v", err)
	}
	if cfg.Scraper.WarmupNavigations == nil {
		t.Fatal("config/scraper.yaml sets warmup_navigations; it must parse")
	}
	if cfg.Scraper.Warmups() != *cfg.Scraper.WarmupNavigations {
		t.Errorf("Warmups()=%d disagrees with the file's %d",
			cfg.Scraper.Warmups(), *cfg.Scraper.WarmupNavigations)
	}
}

// Per-platform depth: one number cannot serve a keyless unmetered API and a
// bot-detected browser scrape. These pin the fallback chain, because getting
// it wrong silently means either wasted depth or a flagged fingerprint.
func TestDepthForFallsBackToTheScalar(t *testing.T) {
	th := &Thresholds{MaxThreadsPerSource: 3}
	if got := th.DepthFor("reddit"); got != 3 {
		t.Errorf("no map => scalar; got %d", got)
	}
	th.MaxThreadsPerPlatform = map[string]int{"hackernews": 40}
	if got := th.DepthFor("hackernews"); got != 40 {
		t.Errorf("override ignored; got %d", got)
	}
	if got := th.DepthFor("reddit"); got != 3 {
		t.Errorf("unlisted platform must keep the scalar; got %d", got)
	}
	// A zero override is "not set", not "fetch nothing" — a depth of 0 would
	// silently turn a source off.
	th.MaxThreadsPerPlatform["youtube"] = 0
	if got := th.DepthFor("youtube"); got != 3 {
		t.Errorf("zero override must fall back, got %d", got)
	}
}

func TestPerPlatformDepthIsRangeChecked(t *testing.T) {
	th := DefaultThresholds()
	th.EmbeddingModel = "nomic-embed-text"
	th.MaxThreadsPerPlatform = map[string]int{"hackernews": 9999}
	if err := th.Validate(); err == nil {
		t.Fatal("an out-of-range override must be refused, not silently accepted")
	}
	th.MaxThreadsPerPlatform = map[string]int{"hackernews": 40}
	if err := th.Validate(); err != nil {
		t.Fatalf("a valid override must pass: %v", err)
	}
}

// A thresholds.yaml written before Steam existed sets max_threads_per_source
// and not max_reviews_per_app. Defaulting the two together left the second at
// 0 against a minimum of 1, which would have failed every file already on
// disk — including the one this repo ships.
func TestMaxReviewsPerAppDefaultsIndependently(t *testing.T) {
	th := &Thresholds{
		MaxCommentsPerThread: 500, MinUpvotes: 1, DedupThreshold: 0.87,
		PrefilterMinChars: 40, PrefilterMaxChars: 600, PrefilterMaxEmoji: 5,
		PrefilterMaxMentions: 3, PrefilterMinWords: 6, RequestDelayMs: 2000,
		// Set, so the older field's zero-guard does not fire.
		MaxThreadsPerSource: 3,
		EmbeddingModel:      "nomic-embed-text",
	}
	if err := th.Validate(); err != nil {
		t.Fatalf("a config predating this knob must still validate: %v", err)
	}
	if th.MaxReviewsPerApp != int(bounds["max_reviews_per_app"].def) {
		t.Fatalf("want the documented default, got %d", th.MaxReviewsPerApp)
	}
}

// The bound that pushed this onto its own knob: max_threads_per_platform is
// capped at 200 because its unit is threads, and a useful review count is not.
func TestSteamDepthIsNotSmuggledThroughTheThreadKnob(t *testing.T) {
	th := DefaultThresholds()
	th.MaxThreadsPerPlatform = map[string]int{"steam": 201}
	if err := th.Validate(); err == nil {
		t.Fatal("a review count must not pass as a thread count")
	}
	th.MaxThreadsPerPlatform = nil
	th.MaxReviewsPerApp = 150
	if err := th.Validate(); err != nil {
		t.Fatalf("150 reviews is in range on its own knob: %v", err)
	}
}

// TestCDPURLEnvironmentWinsOverTheFile pins the rule that fixed a false
// outage: scraper.yaml is bind-mounted into the worker container and says
// 127.0.0.1:9222, which is correct for a worker running ON the host and wrong
// inside a container, where it is that container's own loopback. cloakserve
// then reads as down while it is up and answering on the compose network.
//
// One file cannot be right for both callers, so the address comes from the
// environment, which differs, rather than the file, which does not.
func TestCDPURLEnvironmentWinsOverTheFile(t *testing.T) {
	dir := writeConfigDir(t)

	t.Setenv("JESTER_CDP_URL", "")
	cfg, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	fromFile := cfg.Scraper.CDPURL

	t.Setenv("JESTER_CDP_URL", "http://cloakbrowser:9222")
	cfg, err = Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Scraper.CDPURL != "http://cloakbrowser:9222" {
		t.Fatalf("env did not win: got %q, file had %q", cfg.Scraper.CDPURL, fromFile)
	}

	// Compose leaving a variable unset arrives as empty, and empty is not an
	// address — it must fall back rather than blank the URL.
	t.Setenv("JESTER_CDP_URL", "   ")
	cfg, err = Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Scraper.CDPURL != fromFile {
		t.Fatalf("a blank override should fall back to the file, got %q", cfg.Scraper.CDPURL)
	}
}

// Depth is a per-source override for the same reason min_comments is: one
// global number cannot serve a discussion room and a hiring room at once.
// Measured before this existed: at the Reddit default of 3, r/forhire -- one
// of the busiest hiring rooms on the site -- produced six adverts in three
// days, because an hour later the newest three are the same three.
func TestDepthOrPrefersTheSourceOverThePlatform(t *testing.T) {
	d := 25
	if got := (Source{Depth: &d}).DepthOr(3); got != 25 {
		t.Errorf("source depth must win over the platform floor; got %d", got)
	}
}

func TestDepthOrFallsBackWhenUnset(t *testing.T) {
	// The overwhelming majority of sources set nothing and must keep the
	// platform's own number.
	if got := (Source{}).DepthOr(3); got != 3 {
		t.Errorf("unset depth must fall back; got %d", got)
	}
}

func TestDepthOrIgnoresZeroAndNegative(t *testing.T) {
	// Unlike min_comments, 0 is NOT meaningful here: a depth of zero would
	// mean "fetch nothing from this source", which is what `enabled: false`
	// is for. Treating it as unset keeps a typo from silently muting a room.
	for _, n := range []int{0, -1} {
		v := n
		if got := (Source{Depth: &v}).DepthOr(3); got != 3 {
			t.Errorf("depth %d must fall back, got %d", n, got)
		}
	}
}
