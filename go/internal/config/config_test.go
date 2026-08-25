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
