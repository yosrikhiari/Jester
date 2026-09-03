package realestate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"jester/internal/config"
	"jester/internal/siteprofile"
	"jester/internal/store"
)

// FetchListingList loads the siteprofile for src, walks up to perSource
// pages via cloakserve (FetchRenderedPage), and returns the parsed
// listings. Mirrors reddit.ListThreads.
func FetchListingList(ctx context.Context, src config.Source, delay time.Duration, perSource int, configDir string) ([]store.Listing, error) {
	profilePath := filepath.Join(configDir, "profiles", src.Name+".yaml")
	profileBytes, err := os.ReadFile(profilePath)
	if err != nil {
		return nil, fmt.Errorf("load profile %s: %w", profilePath, err)
	}
	profile, err := siteprofile.Load(profileBytes)
	if err != nil {
		return nil, fmt.Errorf("parse profile %s: %w", src.Name, err)
	}

	maxPages := profile.List.MaxPages
	if maxPages <= 0 {
		maxPages = 1
	}
	if maxPages > perSource {
		maxPages = perSource
	}

	var allListings []store.Listing
	for page := 1; page <= maxPages; page++ {
		pageURL := buildPageURL(profile.List.URL, profile.List.PageParam, page)

		body, err := FetchRenderedPage(ctx, pageURL, delay)
		if err != nil {
			fmt.Printf("[live]   realestate page %d failed: %v\n", page, err)
			continue
		}

		listings, err := profile.Parse(body)
		if err != nil {
			fmt.Printf("[live]   realestate page %d parse failed: %v\n", page, err)
			continue
		}

		allListings = append(allListings, listings...)
		if page < maxPages {
			time.Sleep(Pace(delay))
		}
	}

	if len(allListings) == 0 {
		return nil, fmt.Errorf("no listings found for %s", src.Name)
	}
	return allListings, nil
}

// buildPageURL constructs the page-N URL from the base listing URL,
// pageParam name, and page number.
func buildPageURL(baseURL, pageParam string, page int) string {
	if page <= 1 || pageParam == "" {
		return baseURL
	}
	sep := "?"
	if strings.Contains(baseURL, "?") {
		sep = "&"
	}
	return fmt.Sprintf("%s%s%s=%d", baseURL, sep, pageParam, page)
}
