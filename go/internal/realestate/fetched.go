package realestate

import (
	"crypto/sha1"
	"encoding/hex"
)

// ListingFetched is one real-estate listing with everything the portal
// actually publishes about it.
type ListingFetched struct {
	// Identity.
	Portal    string            `json:"portal"`
	ListingID string            `json:"listing_id"`
	URL       string            `json:"url"`
	// Commercial details.
	Price    float64 `json:"price"`
	Currency string  `json:"currency"`
	Status   string  `json:"status"`
	// Media links (photos, floor plans, virtual tours).
	Media []string `json:"media"`
	// Payload is the raw portal-specific data map.
	Payload map[string]any `json:"payload"`
	// PageURL is the URL the listing was scraped from (for debugging).
	PageURL string `json:"-"`
}

// Fingerprint derives the cross-language dedup key: hex(sha1(portal:listingID))[:16],
// byte-identical to the reddit adapter pattern.
func Fingerprint(l ListingFetched) string {
	source := l.ListingID
	if source == "" {
		source = l.URL
	}
	sum := sha1.Sum([]byte(l.Portal + ":" + source))
	return hex.EncodeToString(sum[:])[:16]
}
