// Package config loads and validates Jester runtime configuration.
//
// Schema version is the single source of truth for cross-language contract
// compatibility between the Go ingestion worker and the Python agent pipeline
// (see python/jester/config/schema_version.py). Bump it when the SQLite schema
// or the ingest_batch JSON contract changes in a breaking way.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// SchemaVersion is the contract version shared with the Python side.
const SchemaVersion = 1

// Config is the merged runtime configuration.
type Config struct {
	Sources    Sources    `yaml:"sources"`
	Thresholds Thresholds `yaml:"thresholds"`
	Scraper    Scraper    `yaml:"scraper"`
	DataDir    string     `yaml:"data_dir"`
}

// Source describes one curated channel to ingest.
//
// Kind tells the worker how to reach it: a subreddit/channel is a listing to
// walk, a thread/video is a page to read directly. Enabled is a pointer so an
// entry written before the field existed (nil) still counts as enabled.
type Source struct {
	Platform string `yaml:"platform"`
	Name     string `yaml:"name"`
	Kind     string `yaml:"kind"`
	URL      string `yaml:"url"`
	Enabled  *bool  `yaml:"enabled"`
	Notes    string `yaml:"notes"`
}

// IsEnabled reports whether the source should be fetched this run.
func (s Source) IsEnabled() bool { return s.Enabled == nil || *s.Enabled }

// ResolvedKind returns Kind, falling back to a URL-shape guess for legacy
// entries that predate the field. Mirrors python/jester/sources.py.
func (s Source) ResolvedKind() string {
	if s.Kind != "" {
		return s.Kind
	}
	switch s.Platform {
	case "reddit":
		if strings.Contains(s.URL, "/comments/") {
			return "thread"
		}
		return "subreddit"
	case "youtube":
		if strings.Contains(s.URL, "watch?v=") || strings.Contains(s.URL, "/shorts/") ||
			strings.Contains(s.URL, "youtu.be/") {
			return "video"
		}
		return "channel"
	case "tiktok":
		if strings.Contains(s.URL, "/video/") {
			return "video"
		}
		if strings.Contains(s.URL, "/tag/") {
			return "hashtag"
		}
		return "profile"
	case "hackernews":
		if strings.Contains(s.URL, "item?id=") {
			return "story"
		}
		return "feed"
	case "discourse":
		if strings.Contains(s.URL, "/t/") {
			return "topic"
		}
		return "forum"
	}
	return ""
}

// Sources holds the curated channel list.
type Sources struct {
	Sources []Source `yaml:"sources"`
}

// Thresholds holds tunable pipeline thresholds with documented defaults and
// hard range bounds. Validate enforces the bounds and fails fast.
type Thresholds struct {
	MaxCommentsPerThread int64   `yaml:"max_comments_per_thread"`
	MinUpvotes           int64   `yaml:"min_upvotes"`
	DedupThreshold       float64 `yaml:"dedup_threshold"`
	PrefilterMinChars    int     `yaml:"prefilter_min_chars"`
	PrefilterMaxChars    int     `yaml:"prefilter_max_chars"`
	PrefilterMaxEmoji    int     `yaml:"prefilter_max_emoji"`
	PrefilterMaxMentions int     `yaml:"prefilter_max_mentions"`
	PrefilterMinWords    int     `yaml:"prefilter_min_words"`
	RequestDelayMs       int64   `yaml:"request_delay_ms"`
	MaxThreadsPerSource  int     `yaml:"max_threads_per_source"`
	// Per-platform override of MaxThreadsPerSource. One number cannot serve
	// all four adapters: Hacker News answers 1,000 stories from a keyless,
	// unmetered, documented API in a single request, while Reddit is a
	// bot-detected browser scrape running on one concurrent session with no
	// licence key. Holding HN at the value that keeps Reddit safe throws away
	// the cheapest depth in the system; raising Reddit to HN's is the one
	// change here that could actually get an identity flagged.
	//
	// Absent platform => MaxThreadsPerSource. Absent map => today's behaviour.
	MaxThreadsPerPlatform map[string]int `yaml:"max_threads_per_platform"`
	// RealestateDetailPerPage bounds the second fetch a real-estate profile
	// may make per result page, for fields the result page does not carry.
	//
	// OFF BY DEFAULT (0), and deliberately. It is one request per listing on
	// top of the result page, so a portal that answers 33 tiles becomes 34
	// requests instead of 1 - the difference between reading a page and
	// crawling a portal. Only two profiles declare a `detail` block at all,
	// and each one earned it: Houni publishes no price on its tiles (two
	// patterns were tried and both reported fiction), and Property24 puts the
	// street address only on the listing page, which is the field that decides
	// a cross-portal duplicate.
	//
	// Property24 answered 503 during development after a session that had
	// pulled twenty result pages and some 2,400 photographs from it, which is
	// the argument for this being a number someone chooses rather than a
	// default they inherit.
	RealestateDetailPerPage int `yaml:"realestate_detail_per_page"`
	// MinCommentsPerThread skips threads too quiet to be worth a fetch. Only
	// the API-backed adapters (Hacker News, Discourse) can filter on it before
	// fetching, because only they get a comment count in the listing.
	MinCommentsPerThread int `yaml:"min_comments_per_thread"`
	// MaxReviewsPerApp is Steam's depth, and it needs its own knob because a
	// Steam source IS one app: "threads to walk per source" has nothing to
	// walk there, and the number that actually varies is how many of the app's
	// reviews come back.
	//
	// Overloading max_threads_per_platform for it was the obvious shortcut and
	// the wrong one — that knob is validated to [1,50] precisely because its
	// unit is threads, so a review count large enough to be useful is rejected
	// by a bound that was never about reviews.
	MaxReviewsPerApp int    `yaml:"max_reviews_per_app"`
	EmbeddingModel   string `yaml:"embedding_model"`
	Models           Models `yaml:"models"`
}

// Models names the LLM roles used by the Python agent pipeline.
type Models struct {
	Extractor   string `yaml:"extractor"`
	Archivist   string `yaml:"archivist"`
	Synthesizer string `yaml:"synthesizer"`
	Critic      string `yaml:"critic"`
}

// Scraper holds CloakBrowser integration config. Live fetch (M1.1+) reads
// these; mock mode ignores them.
//
// §4.3.2: cloakserve is a CDP multiplexer that keys a separate stealth Chrome
// process off the `fingerprint` query param, and accepts timezone / locale /
// proxy / geoip alongside it. Those are per-connection identity, which is why
// they live here rather than being baked into the container.
type Scraper struct {
	CDPURL        string `yaml:"cdp_url"`
	LicenseKey    string `yaml:"license_key"`
	Proxy         string `yaml:"proxy"`
	GeoIP         string `yaml:"geoip"`
	Timezone      string `yaml:"timezone"`
	Locale        string `yaml:"locale"`
	BlockedAction string `yaml:"blocked_response_action"`
	// WarmupNavigations is how many throwaway navigations a fresh fingerprint
	// makes before its first real fetch. Calibrated live: Reddit challenges the
	// FIRST request from an unseen session whatever the URL, and serves real
	// content from the second onward — dwell time alone does not help.
	//
	// A POINTER so that "absent" and "explicitly 0" stay distinguishable: 0
	// means the operator turned the warm-up off, while a config file written
	// before this key existed must keep warming rather than silently lose its
	// only answer to Reddit's cold-session challenge. `sanitize` resolves it.
	WarmupNavigations *int `yaml:"warmup_navigations"`
}

// : Warm-ups when the key is absent. One extra navigation is what the live
// : calibration showed clears Reddit's first-request challenge.
const defaultWarmupNavigations = 1

// Warmups is the resolved warm-up count: the configured value, or the default
// when the key is absent.
func (s Scraper) Warmups() int {
	if s.WarmupNavigations == nil {
		return defaultWarmupNavigations
	}
	if *s.WarmupNavigations < 0 {
		return 0
	}
	return *s.WarmupNavigations
}

// GeoIPEnabled reports whether geoip auto-matching is on. cloakserve parses
// this as a boolean and only honours it when a proxy is also set, so the older
// habit of putting a country code here ("us") silently meant "off".
func (s Scraper) GeoIPEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(s.GeoIP)) {
	case "true", "1", "yes", "on":
		return true
	}
	return false
}

// bounds documents default + min/max for each numeric threshold.
type bound struct {
	def, min, max float64
	isInt         bool
}

var bounds = map[string]bound{
	"max_comments_per_thread": {def: 500, min: 1, max: 5000, isInt: true},
	"min_upvotes":             {def: 1, min: 0, max: 1000, isInt: true},
	"dedup_threshold":         {def: 0.87, min: 0.5, max: 1.0},
	"prefilter_min_chars":     {def: 40, min: 1, max: 1000, isInt: true},
	"prefilter_max_chars":     {def: 600, min: 50, max: 10000, isInt: true},
	"prefilter_max_emoji":     {def: 5, min: 0, max: 100, isInt: true},
	"prefilter_max_mentions":  {def: 3, min: 0, max: 50, isInt: true},
	"prefilter_min_words":     {def: 6, min: 1, max: 200, isInt: true},
	"request_delay_ms":        {def: 2000, min: 500, max: 60000, isInt: true},
	"max_threads_per_source":  {def: 1, min: 1, max: 50, isInt: true},
	"max_threads_per_platform": {def: 1, min: 1, max: 200, isInt: true},
	"min_comments_per_thread": {def: 0, min: 0, max: 10000, isInt: true},
	// Steam is keyless and unmetered, so the ceiling is about BALANCE, not
	// quota: every other platform yields roughly 160-210 comments per turn,
	// and an app that took 500 would stop the rotation rotating.
	"max_reviews_per_app": {def: 150, min: 1, max: 5000, isInt: true},
}

// Load reads and validates the three YAML configs from dir.
func Load(dir string) (*Config, error) {
	cfg := &Config{DataDir: filepath.Join(dir, "..", "data")}
	if err := unmarshalFile(filepath.Join(dir, "sources.yaml"), &cfg.Sources); err != nil {
		return nil, fmt.Errorf("sources.yaml: %w", err)
	}
	if err := unmarshalFile(filepath.Join(dir, "thresholds.yaml"), &cfg.Thresholds); err != nil {
		return nil, fmt.Errorf("thresholds.yaml: %w", err)
	}
	// scraper.yaml carries ${ENV} placeholders for secrets (licence key, proxy).
	// Python's loader expands them; Go used to hand the raw "${CLOAKBROWSER_PROXY}"
	// string straight through, which reached Chrome as a proxy address and
	// failed every navigation with ERR_PROXY_CONNECTION_FAILED.
	if err := unmarshalFileExpanded(filepath.Join(dir, "scraper.yaml"), &cfg.Scraper); err != nil {
		return nil, fmt.Errorf("scraper.yaml: %w", err)
	}
	cfg.Scraper.sanitize()
	if err := cfg.Thresholds.Validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

// LoadConfig loads config from a config directory containing the YAML files.
func LoadConfig(configDir string) (*Config, error) { return Load(configDir) }

func unmarshalFile(path string, out any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return yaml.Unmarshal(raw, out)
}

// unmarshalFileExpanded mirrors Python's os.path.expandvars on the way in, so
// both languages read the same values out of the same file. An unset variable
// expands to empty rather than staying a literal "${NAME}".
func unmarshalFileExpanded(path string, out any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return yaml.Unmarshal([]byte(os.ExpandEnv(string(raw))), out)
}

var unexpandedRE = regexp.MustCompile(`^\$\{[^}]*\}$|^\$[A-Za-z_][A-Za-z0-9_]*$`)

// sanitize blanks any field still holding an unexpanded placeholder. Passing
// one on as a real value is worse than having no value at all — an unset proxy
// means "direct", a literal "${CLOAKBROWSER_PROXY}" means every request fails.
func (s *Scraper) sanitize() {
	for _, f := range []*string{&s.LicenseKey, &s.Proxy, &s.GeoIP, &s.Timezone, &s.Locale} {
		if unexpandedRE.MatchString(strings.TrimSpace(*f)) {
			*f = ""
		}
	}
	s.Proxy = strings.TrimSpace(s.Proxy)

	// JESTER_CDP_URL wins over the file, because the file cannot be right for
	// both callers at once. scraper.yaml says 127.0.0.1:9222 and explains why:
	// the container publishes on IPv4 only and Windows resolves localhost to
	// ::1 first. That is correct for a worker running ON the host.
	//
	// Inside a container it is wrong in a way that looks like an outage.
	// 127.0.0.1 there is the container's own loopback, so cloakserve appears
	// down while it is up and answering on the compose network. The console
	// reported exactly that, and told the operator to start a container that
	// had been running for forty hours.
	//
	// The same file is bind-mounted into both, so the address has to come from
	// the environment that differs, not the file that does not.
	if v := strings.TrimSpace(os.Getenv("JESTER_CDP_URL")); v != "" {
		s.CDPURL = v
	}
}

// Validate enforces documented range bounds on every numeric threshold.
func (t *Thresholds) Validate() error {
	checks := map[string]float64{
		"max_comments_per_thread": float64(t.MaxCommentsPerThread),
		"min_upvotes":             float64(t.MinUpvotes),
		"dedup_threshold":         t.DedupThreshold,
		"prefilter_min_chars":     float64(t.PrefilterMinChars),
		"prefilter_max_chars":     float64(t.PrefilterMaxChars),
		"prefilter_max_emoji":     float64(t.PrefilterMaxEmoji),
		"prefilter_max_mentions":  float64(t.PrefilterMaxMentions),
		"prefilter_min_words":     float64(t.PrefilterMinWords),
		"request_delay_ms":        float64(t.RequestDelayMs),
	}
	// 0 means "not set in YAML" for a field added after the file was written;
	// fall back to the documented default rather than failing validation.
	if t.MaxThreadsPerSource == 0 {
		t.MaxThreadsPerSource = int(bounds["max_threads_per_source"].def)
	}
	// Its own guard, not a branch of the one above: a thresholds.yaml written
	// before Steam existed sets max_threads_per_source and not this, so
	// defaulting them together would leave this at 0 and fail every such file
	// against a minimum of 1.
	if t.MaxReviewsPerApp == 0 {
		t.MaxReviewsPerApp = int(bounds["max_reviews_per_app"].def)
	}
	checks["max_threads_per_source"] = float64(t.MaxThreadsPerSource)
	// Every override is bounded by the same rule as the scalar it overrides,
	// so a typo cannot smuggle an unbounded crawl past validation. Checked
	// here rather than added to `checks`, because the loop below skips keys it
	// does not recognise — a dotted key would have validated vacuously.
	b := bounds["max_threads_per_platform"]
	for platform, n := range t.MaxThreadsPerPlatform {
		if float64(n) < b.min || float64(n) > b.max {
			return fmt.Errorf(
				"max_threads_per_platform.%s=%d out of range [%v,%v]",
				platform, n, b.min, b.max)
		}
	}
	checks["min_comments_per_thread"] = float64(t.MinCommentsPerThread)
	checks["max_reviews_per_app"] = float64(t.MaxReviewsPerApp)
	for k, v := range checks {
		b, ok := bounds[k]
		if !ok {
			continue
		}
		if v < b.min || v > b.max {
			return fmt.Errorf("threshold %s=%v out of range [%v,%v]", k, v, b.min, b.max)
		}
	}
	if t.PrefilterMinChars > t.PrefilterMaxChars {
		return fmt.Errorf("prefilter_min_chars (%d) must be <= prefilter_max_chars (%d)", t.PrefilterMinChars, t.PrefilterMaxChars)
	}
	if t.EmbeddingModel == "" {
		return fmt.Errorf("thresholds.embedding_model must be set")
	}
	return nil
}

// DepthFor is how many threads/videos/topics to walk for one platform in one
// run. Falls back to MaxThreadsPerSource when the platform has no override,
// so a config written before this field existed behaves exactly as it did.
func (t *Thresholds) DepthFor(platform string) int {
	if n, ok := t.MaxThreadsPerPlatform[platform]; ok && n > 0 {
		return n
	}
	if t.MaxThreadsPerSource > 0 {
		return t.MaxThreadsPerSource
	}
	return int(bounds["max_threads_per_source"].def)
}

// DefaultThresholds returns a Thresholds populated with documented defaults.
func DefaultThresholds() Thresholds {
	t := Thresholds{}
	t.MaxCommentsPerThread = int64(bounds["max_comments_per_thread"].def)
	t.MinUpvotes = int64(bounds["min_upvotes"].def)
	t.DedupThreshold = bounds["dedup_threshold"].def
	t.PrefilterMinChars = int(bounds["prefilter_min_chars"].def)
	t.PrefilterMaxChars = int(bounds["prefilter_max_chars"].def)
	t.PrefilterMaxEmoji = int(bounds["prefilter_max_emoji"].def)
	t.PrefilterMaxMentions = int(bounds["prefilter_max_mentions"].def)
	t.PrefilterMinWords = int(bounds["prefilter_min_words"].def)
	t.RequestDelayMs = int64(bounds["request_delay_ms"].def)
	t.MaxThreadsPerSource = int(bounds["max_threads_per_source"].def)
	t.MaxReviewsPerApp = int(bounds["max_reviews_per_app"].def)
	t.EmbeddingModel = "nomic-embed-text"
	return t
}
