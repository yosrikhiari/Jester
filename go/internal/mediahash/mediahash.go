// Package mediahash turns a listing photograph into 8 bytes.
//
// WHY THIS EXISTS. A property listing carries ten to forty photographs, and
// the questions worth asking about them — is this the same property relisted
// by another agent, did the photo set change when the price did, is this
// listing duplicated on a competing portal — are all questions about whether
// two images are THE SAME, never about what either one depicts. That is a
// fingerprint problem, not a storage problem.
//
// The difference is four orders of magnitude. 100,000 listings at twenty
// photos is roughly 600 GB of files against 16 MB of hashes, on a machine with
// 383 GB free. Storing the files is not a preference this project gets to
// have.
//
// WHY dHash AND NOT SOMETHING BETTER. dHash compares each pixel with its right
// neighbour after downsampling to 9x8 grey. It is scale-invariant by
// construction — everything is resized to the same 72 pixels before a single
// comparison happens — which is the property that matters most here: a portal
// serves the same photograph at a dozen sizes, and all of them must hash
// alike. pHash (DCT-based) is more robust to rotation and gamma, and needs a
// DCT this package would have to carry. Listings are not rotated.
//
// WHAT IT DOES NOT SURVIVE. A crop, a watermark burned into a corner, a
// mirrored image, or a heavy colour grade. Agents do all four occasionally, so
// a miss is a real outcome and must read as "not proven identical" rather than
// "proven different" wherever this is used.
//
// NO NEW DEPENDENCY, DELIBERATELY. The whole package is standard library:
// image/jpeg and image/png decode, and the 9x8 downsample is written here
// rather than pulled in. golang.org/x/image would add WebP — see Decode.
package mediahash

import (
	"encoding/hex"
	"fmt"
	"image"
	"image/color"
	"io"
	"math/bits"

	// Decoders register themselves with image.Decode on import. JPEG and PNG
	// are the standard library's whole offering for photographs.
	_ "image/jpeg"
	_ "image/png"
)

// Width/height of the reduced grey image. 9 columns produce 8 comparisons per
// row, so 8 rows give exactly 64 bits.
const (
	hashW = 9
	hashH = 8
)

// ErrUnsupportedFormat reports a payload no standard-library decoder claims.
//
// In practice this is almost always WebP, which many portal CDNs prefer when
// the request advertises it. The cheap answer is to stop advertising it:
// sending `Accept: image/jpeg` makes most CDNs negotiate back to JPEG, and
// costs nothing. Adding golang.org/x/image/webp is the fallback, and is a
// dependency decision rather than a bug fix.
var ErrUnsupportedFormat = fmt.Errorf("mediahash: no standard-library decoder for this image format")

// Hash is a 64-bit difference hash. Stored as hex text rather than an INTEGER
// because SQLite integers are signed: a hash with the top bit set does not
// round-trip through an INTEGER column without wrapping negative.
type Hash uint64

// String renders the hash as 16 lowercase hex characters, which is the form
// written to the database.
func (h Hash) String() string {
	var b [8]byte
	for i := 7; i >= 0; i-- {
		b[i] = byte(h)
		h >>= 8
	}
	return hex.EncodeToString(b[:])
}

// ParseHash reads back what String wrote.
func ParseHash(s string) (Hash, error) {
	b, err := hex.DecodeString(s)
	if err != nil {
		return 0, fmt.Errorf("mediahash: parse %q: %w", s, err)
	}
	if len(b) != 8 {
		return 0, fmt.Errorf("mediahash: parse %q: want 8 bytes, got %d", s, len(b))
	}
	var h Hash
	for _, c := range b {
		h = h<<8 | Hash(c)
	}
	return h, nil
}

// Distance is the Hamming distance between two hashes: how many of the 64
// comparisons disagree.
//
// CALIBRATION, measured on this project's own images rather than taken from a
// blog post. Re-deliveries of one photograph — 320px thumbnail, 150px
// thumbnail, JPEG q40, WebP q60, and a 150px thumbnail recompressed at q50 —
// all landed at distance 0 or 1. Unrelated images landed at 33 and 36. The gap
// is wide, which is why a threshold anywhere in the middle works and why
// SameImage picks a conservative one.
func Distance(a, b Hash) int { return bits.OnesCount64(uint64(a ^ b)) }

// SameThreshold is the distance at or below which two hashes are treated as
// the same photograph.
//
// 12, RAISED FROM 5 after measuring real files rather than synthetic ones.
// The synthetic fixtures in this package's own tests are smooth gradients, and
// they flattered the hash badly: every re-delivery scored 0 or 1, which made 5
// look generous. Two real image sets said otherwise.
//
//	                        worst same-image   unrelated
//	textured photograph            5              33
//	flat logo art                  9              30
//
// The logo is the honest worst case and it is worth understanding rather than
// dismissing: 65% of its adjacent pixels are identical, and 16 of its 64
// dHash comparisons have a luminance margin below 2.0 with a minimum of
// exactly 0.00. A margin of zero is a coin flip — JPEG noise decides it. Flat
// artwork is therefore intrinsically unstable under this hash, and property
// galleries do contain flat artwork: floor plans, agency logo cards, EPC
// charts.
//
// 12 clears the measured worst case by 3 bits and still sits 18 below the
// closest unrelated pair. The gap is wide enough that the exact value is not
// delicate; what would have been delicate is leaving it at 5, which missed a
// real re-delivery at q40.
const SameThreshold = 12

// Bands splits the hash into 8 one-byte bands for indexed near-match lookup.
//
// WHY THIS EXISTS. Equality is useless for finding the same photograph twice:
// the measurements above show real re-deliveries landing 4 to 9 bits apart, so
// a SQL `WHERE phash = ?` finds almost nothing. Scanning every row and
// computing distance is correct but is a full table scan per photograph.
//
// Banding makes it an index lookup. Two hashes at distance d must share at
// least one identical band whenever d < 8, because 8 differing bands need at
// least 8 differing bits. So candidate retrieval by "any band matches" has
// GUARANTEED recall up to distance 7, and merely very good recall from 8 to
// 12 — a miss there needs the differing bits spread across all eight bands
// with none to spare.
//
// The hole this leaves is real and worth naming rather than glossing: at 8 or
// more differing bits spread one-per-band, every band differs and retrieval
// returns nothing even though Distance would have accepted the pair. Recall is
// guaranteed to 7, good but not certain from 8 to 12.
//
// It bites where it matters least. 8+ bit drift was measured only on flat
// artwork — floor plans, agency logo cards, EPC charts — which is the least
// useful content in a property gallery for identifying a house. Textured
// photographs, the ones that actually identify a property, stayed within 5.
//
// Callers must still verify each candidate with Distance: a shared band is a
// reason to compare, never a match on its own.
func (h Hash) Bands() [8]string {
	var out [8]string
	for i := 0; i < 8; i++ {
		b := byte(h >> (8 * (7 - i)))
		out[i] = fmt.Sprintf("%d:%02x", i, b)
	}
	return out
}

// SameImage reports whether two hashes describe the same photograph.
//
// Note the asymmetry in what a result means: true is strong evidence (two
// unrelated photographs colliding within 5 bits is vanishingly unlikely),
// while false only means "not proven identical" — a crop or a watermark
// defeats dHash entirely.
func SameImage(a, b Hash) bool { return Distance(a, b) <= SameThreshold }

// Decode reads an image and returns its difference hash.
//
// The reader is consumed fully. Callers holding a listing thumbnail in memory
// should wrap it in a bytes.Reader; callers streaming from a CDN should bound
// the body first — this function will happily decode a 60 MB image it was
// never meant to see.
func Decode(r io.Reader) (Hash, error) {
	img, _, err := image.Decode(r)
	if err != nil {
		// image.Decode reports "unknown format" for anything no registered
		// decoder recognises. Naming it is the difference between "this portal
		// serves WebP" and "this download was truncated", which call for
		// completely different responses.
		if err == image.ErrFormat {
			return 0, ErrUnsupportedFormat
		}
		return 0, fmt.Errorf("mediahash: decode: %w", err)
	}
	return FromImage(img), nil
}

// FromImage hashes an already-decoded image.
func FromImage(img image.Image) Hash {
	grid := reduce(img)
	// Bit (y, x) is set when the pixel to the RIGHT is brighter, walked in
	// row-major order, most significant bit first.
	//
	// A SECOND IMPLEMENTATION MUST COPY reduce() TOO, not just this loop.
	// Measured: a Pillow reference using LANCZOS to reach 9x8 produced
	// 17cc60b2b2718e60 for the same file this package hashes to
	// d2cce0b2b2b2dc8e. The bit convention matched; the downsample did not,
	// and the downsample is most of the hash. Area averaging and LANCZOS
	// disagree on real detail, so hashes are comparable only between
	// implementations that reduce identically.
	var h Hash
	for y := 0; y < hashH; y++ {
		for x := 0; x < hashW-1; x++ {
			h <<= 1
			if grid[y][x+1] > grid[y][x] {
				h |= 1
			}
		}
	}
	return h
}

// reduce downsamples to a 9x8 grid of average luminance.
//
// Area averaging, not nearest-neighbour. Nearest sampling of a photograph at
// 1/100th scale is effectively reading 72 random pixels: two deliveries of the
// same image at different sizes land on different source pixels and produce
// different hashes, which defeats the one property this package exists for.
// Averaging every source pixel in each cell is what makes a 150px thumbnail
// and a 768px original agree.
func reduce(img image.Image) [hashH][hashW]float64 {
	var grid [hashH][hashW]float64
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	if w <= 0 || h <= 0 {
		return grid
	}
	var sum [hashH][hashW]float64
	var count [hashH][hashW]int
	for py := 0; py < h; py++ {
		// Which output row this source row belongs to. Integer maths keeps the
		// cell boundaries identical regardless of source size.
		cy := py * hashH / h
		if cy >= hashH {
			cy = hashH - 1
		}
		for px := 0; px < w; px++ {
			cx := px * hashW / w
			if cx >= hashW {
				cx = hashW - 1
			}
			g := color.GrayModel.Convert(img.At(b.Min.X+px, b.Min.Y+py)).(color.Gray)
			sum[cy][cx] += float64(g.Y)
			count[cy][cx]++
		}
	}
	for y := 0; y < hashH; y++ {
		for x := 0; x < hashW; x++ {
			if count[y][x] > 0 {
				grid[y][x] = sum[y][x] / float64(count[y][x])
			}
		}
	}
	return grid
}
