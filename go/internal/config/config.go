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

	"gopkg.in/yaml.v3"
)

// SchemaVersion is the contract version shared with the Python side.
const SchemaVersion = 1

// Config is the merged runtime configuration.
type Config struct {
	Sources   Sources          `yaml:"sources"`
	Thresholds Thresholds       `yaml:"thresholds"`
	Scraper   Scraper           `yaml:"scraper"`
	DataDir   string            `yaml:"data_dir"`
}

// Source describes one curated channel to ingest.
type Source struct {
	Platform string `yaml:"platform"`
	Name     string `yaml:"name"`
	URL      string `yaml:"url"`
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
	EmbeddingModel       string  `yaml:"embedding_model"`
	Models               Models  `yaml:"models"`
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
type Scraper struct {
	CDPURL            string `yaml:"cdp_url"`
	LicenseKey        string `yaml:"license_key"`
	Proxy             string `yaml:"proxy"`
	GeoIP             string `yaml:"geoip"`
	BlockedAction     string `yaml:"blocked_response_action"`
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
	if err := unmarshalFile(filepath.Join(dir, "scraper.yaml"), &cfg.Scraper); err != nil {
		return nil, fmt.Errorf("scraper.yaml: %w", err)
	}
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
	t.EmbeddingModel = "nomic-embed-text"
	return t
}
