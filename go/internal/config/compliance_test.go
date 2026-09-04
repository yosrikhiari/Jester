package config

import (
	"path/filepath"
	"strings"
	"testing"
)

// noScrapePortals are real-estate portals the compliance audit records as
// not-to-build, with the reason each was ruled out. Verified live on
// 2026-09-04; see docs/realestate-compliance-audit.md.
//
// WHY THIS IS A TEST. The audit is a markdown file, and a markdown file cannot
// stop anyone flipping `enabled: true` eighteen months from now when the reason
// has been forgotten. Two of these are refusals in the operator's own words -
// Leboncoin requires written permission, Rightmove enumerates AI crawlers and
// excludes them - and turning one on is a decision that should have to be
// argued with, not typed.
var noScrapePortals = map[string]string{
	"leboncoin": "robots.txt: forbidden to use search robots or other automatic " +
		"methods; access only with special permission from Leboncoin.fr",
	"rightmove": "robots.txt gives GPTBot and CCbot Disallow: /; the operator " +
		"enumerates AI crawlers and excludes them",
	"immobiliare": "robots.txt disallows /search-list, /search-map and " +
		"/ricerca-mappa/ - the search endpoints themselves",
	"zillow": "robots.txt disallows /homes/, allowing only $-anchored landing " +
		"pages; deep search URLs are out",
	"realtor": "robots.txt itself returns 403 (CloudFront request blocked)",
	"immoscout24": "server answers 401 with an 'Ich bin kein Roboter' " +
		"interstitial, though its robots.txt permits Claude agents by name",
	"idealista": "server answers 403 with a block page",
	"seloger": "server answers 403 with a captcha page; robots.txt also " +
		"disallows result pagination (*/?LISTING-LISTpg)",
}

// TestNoScrapePortalsAreNotEnabled fails if the shipped source list turns on a
// portal the compliance audit ruled out.
func TestNoScrapePortalsAreNotEnabled(t *testing.T) {
	cfg, err := Load(filepath.Join("..", "..", "..", "config"))
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	for _, src := range cfg.Sources.Sources {
		name := strings.ToLower(src.Name)
		reason, blocked := noScrapePortals[name]
		if !blocked {
			continue
		}
		if src.IsEnabled() {
			t.Errorf("source %q is enabled but the compliance audit rules it out: %s",
				src.Name, reason)
		}
	}
}

// A portal ruled out for one reason today may be ruled out for a different one
// tomorrow, so the reasons are required to be present and specific rather than
// a bare "blocked".
func TestEveryNoScrapePortalStatesItsReason(t *testing.T) {
	for portal, reason := range noScrapePortals {
		if len(reason) < 30 {
			t.Errorf("%s: reason too thin to act on later: %q", portal, reason)
		}
	}
}

// The detail tier is one request per listing on top of the result page, so it
// has to be asked for. Property24 answered 503 during development after a
// session that had pulled twenty result pages and some 2,400 photographs from
// it; a default that quietly multiplied every routine run by the number of
// listings on a page is how that happens by accident.
func TestDetailFetchingIsOffInTheShippedConfig(t *testing.T) {
	cfg, err := Load(filepath.Join("..", "..", "..", "config"))
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.Thresholds.RealestateDetailPerPage != 0 {
		t.Errorf("realestate_detail_per_page ships as %d; it must be opt-in",
			cfg.Thresholds.RealestateDetailPerPage)
	}
}
