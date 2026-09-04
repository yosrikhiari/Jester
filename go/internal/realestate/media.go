package realestate

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"jester/internal/mediahash"
	"jester/internal/store"
)

// maxImageBytes bounds one download. mediahash.Decode warns that it will
// happily decode a 60 MB image it was never meant to see, and a listing
// gallery is served by a CDN nobody here controls.
const maxImageBytes = 12 << 20

// MediaStats counts what a hashing pass did, split by outcome because the
// outcomes call for different responses: a decode this build cannot do is a
// dependency decision, a 404 is a dead image, a timeout is worth retrying.
type MediaStats struct {
	Hashed      int
	Unsupported int // WebP, almost always
	Failed      int
	Skipped     int // beyond the per-listing budget, or already hashed
}

// Add folds another pass's counts in.
func (s *MediaStats) Add(o MediaStats) {
	s.Hashed += o.Hashed
	s.Unsupported += o.Unsupported
	s.Failed += o.Failed
	s.Skipped += o.Skipped
}

func (s MediaStats) String() string {
	return fmt.Sprintf("hashed=%d unsupported=%d failed=%d skipped=%d",
		s.Hashed, s.Unsupported, s.Failed, s.Skipped)
}

// HashMedia fetches a listing's photographs and records a dHash for each.
//
// WHY THIS HAD TO EXIST. listing_media.phash was documented as "a
// 16-character dHash, or empty when the image was not fetched" and was empty
// on every row ever written, because nothing computed one: mediahash was
// referenced only for READING in the store. That is not a cosmetic gap. The
// Python dedup harness finds its candidates exclusively through
// find_candidates_by_phash, so with no hashes it compared every listing
// against nothing and reported zero duplicates over a 1000-listing archive -
// a result that looks like a clean bill of health and is actually a no-op.
//
// perListing bounds the work: galleries run to forty photographs and the
// questions being asked (is this the same property, did the photo set change)
// are answered by the first few. A non-positive value hashes the whole gallery.
func HashMedia(ctx context.Context, l *store.Listing, perListing int, delay time.Duration) MediaStats {
	var st MediaStats
	budget := perListing
	for i := range l.Media {
		if l.Media[i].PHash != "" {
			st.Skipped++
			continue
		}
		if perListing > 0 && budget <= 0 {
			st.Skipped++
			continue
		}
		h, err := hashImage(ctx, l.Media[i].URL)
		if err != nil {
			if errors.Is(err, mediahash.ErrUnsupportedFormat) {
				st.Unsupported++
			} else {
				st.Failed++
			}
		} else {
			l.Media[i].PHash = h.String()
			st.Hashed++
		}
		budget--
		if delay > 0 {
			time.Sleep(Pace(delay))
		}
	}
	return st
}

// hashImage downloads one photograph and hashes it.
func hashImage(ctx context.Context, url string) (mediahash.Hash, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, err
	}
	req.Header.Set("User-Agent", UserAgent)
	// Ask for what this build can actually decode. mediahash is standard
	// library only - image/jpeg and image/png - and several CDNs will
	// negotiate away from WebP when told the client cannot read it. Several
	// will not: Tayara's serves image/webp whatever it is asked for, and that
	// is a dependency decision (golang.org/x/image/webp), not a bug.
	req.Header.Set("Accept", "image/jpeg,image/png;q=0.9,*/*;q=0.1")

	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, &StatusError{URL: url, Code: resp.StatusCode}
	}
	return mediahash.Decode(io.LimitReader(resp.Body, maxImageBytes))
}
