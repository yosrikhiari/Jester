package mediahash

import (
	"bytes"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"math"
	"testing"
)

// scene renders the same continuous image at whatever resolution is asked for.
//
// Sampling one function at two sizes is a fairer test than resizing a bitmap:
// it is exactly what a portal's own pipeline does when it publishes a
// photograph as a 150px grid thumbnail and a 1600px detail view. If the hash
// depends on the resolution it was handed, this is where it shows.
func scene(size int, seed float64) image.Image {
	img := image.NewRGBA(image.Rect(0, 0, size, size))
	for y := 0; y < size; y++ {
		v := float64(y) / float64(size)
		for x := 0; x < size; x++ {
			u := float64(x) / float64(size)
			// Smooth, asymmetric, varied local contrast — a stand-in for a
			// room with a window in it, not a test card.
			f := 0.5 + 0.25*math.Sin(6.0*(u+seed)) + 0.15*math.Cos(9.0*v+seed) +
				0.10*math.Sin(14.0*(u*v+seed))
			g := uint8(math.Max(0, math.Min(255, f*255)))
			img.Set(x, y, color.RGBA{R: g, G: g, B: uint8(math.Min(255, float64(g)*1.05)), A: 255})
		}
	}
	return img
}

func encodeJPEG(t *testing.T, img image.Image, q int) []byte {
	t.Helper()
	var b bytes.Buffer
	if err := jpeg.Encode(&b, img, &jpeg.Options{Quality: q}); err != nil {
		t.Fatalf("encode jpeg q%d: %v", q, err)
	}
	return b.Bytes()
}

func encodePNG(t *testing.T, img image.Image) []byte {
	t.Helper()
	var b bytes.Buffer
	if err := png.Encode(&b, img); err != nil {
		t.Fatalf("encode png: %v", err)
	}
	return b.Bytes()
}

// The property the whole package rests on: one photograph, many deliveries,
// one hash. If this fails, cross-portal matching cannot work at all.
func TestSamePhotographAcrossDeliveries(t *testing.T) {
	full := FromImage(scene(768, 0))

	cases := []struct {
		name string
		hash Hash
	}{
		{"320px thumbnail", FromImage(scene(320, 0))},
		{"150px thumbnail", FromImage(scene(150, 0))},
		{"96px thumbnail", FromImage(scene(96, 0))},
	}
	for _, c := range cases {
		if d := Distance(full, c.hash); d > SameThreshold {
			t.Errorf("%s: distance %d exceeds SameThreshold %d — scale invariance is broken",
				c.name, d, SameThreshold)
		}
	}

	// Lossy re-encoding is the other half of "delivered differently".
	for _, q := range []int{90, 70, 40} {
		h, err := Decode(bytes.NewReader(encodeJPEG(t, scene(768, 0), q)))
		if err != nil {
			t.Fatalf("decode jpeg q%d: %v", q, err)
		}
		if d := Distance(full, h); d > SameThreshold {
			t.Errorf("jpeg q%d: distance %d exceeds SameThreshold %d", q, d, SameThreshold)
		}
	}

	// The realistic worst case: a small thumbnail that was also recompressed.
	h, err := Decode(bytes.NewReader(encodeJPEG(t, scene(150, 0), 50)))
	if err != nil {
		t.Fatalf("decode small jpeg: %v", err)
	}
	if d := Distance(full, h); d > SameThreshold {
		t.Errorf("150px + jpeg q50: distance %d exceeds SameThreshold %d", d, SameThreshold)
	}
}

// The negative control. A hash that matches everything is worse than no hash,
// because it reports duplicates that are not there.
func TestDifferentPhotographsSeparate(t *testing.T) {
	base := FromImage(scene(768, 0))
	for _, seed := range []float64{0.7, 1.9, 3.3} {
		other := FromImage(scene(768, seed))
		d := Distance(base, other)
		if d <= SameThreshold {
			t.Errorf("seed %.1f: distance %d — unrelated images must not collide within %d",
				seed, d, SameThreshold)
		}
	}
	// A flat image shares no structure with a textured one.
	flat := image.NewRGBA(image.Rect(0, 0, 400, 400))
	for y := 0; y < 400; y++ {
		for x := 0; x < 400; x++ {
			flat.Set(x, y, color.RGBA{R: 128, G: 128, B: 128, A: 255})
		}
	}
	if d := Distance(base, FromImage(flat)); d <= SameThreshold {
		t.Errorf("flat grey: distance %d — must not match a photograph", d)
	}
}

func TestDecodeFormats(t *testing.T) {
	img := scene(300, 0)
	fromPNG, err := Decode(bytes.NewReader(encodePNG(t, img)))
	if err != nil {
		t.Fatalf("png: %v", err)
	}
	fromJPEG, err := Decode(bytes.NewReader(encodeJPEG(t, img, 95)))
	if err != nil {
		t.Fatalf("jpeg: %v", err)
	}
	if d := Distance(fromPNG, fromJPEG); d > SameThreshold {
		t.Errorf("png vs jpeg of one image: distance %d", d)
	}
}

// WebP and friends must be distinguishable from a truncated download: one says
// "ask this CDN for JPEG instead", the other says "retry".
func TestUnsupportedFormatIsNamed(t *testing.T) {
	// RIFF/WEBP magic, which no standard-library decoder registers.
	webpish := []byte("RIFF\x00\x00\x00\x00WEBPVP8 ")
	if _, err := Decode(bytes.NewReader(webpish)); err != ErrUnsupportedFormat {
		t.Fatalf("webp payload: err = %v, want ErrUnsupportedFormat", err)
	}
	// A truncated JPEG has a format the decoder DOES recognise, so it must
	// report a decode failure rather than an unsupported format.
	full := encodeJPEG(t, scene(200, 0), 80)
	if _, err := Decode(bytes.NewReader(full[:len(full)/3])); err == nil {
		t.Fatal("truncated jpeg: want an error")
	} else if err == ErrUnsupportedFormat {
		t.Fatal("truncated jpeg reported as unsupported format — that would send the caller after the wrong fix")
	}
}

func TestHashTextRoundTrip(t *testing.T) {
	for _, h := range []Hash{0, 1, 0x0123456789abcdef, ^Hash(0)} {
		s := h.String()
		if len(s) != 16 {
			t.Errorf("%016x: String() = %q, want 16 hex chars", uint64(h), s)
		}
		back, err := ParseHash(s)
		if err != nil {
			t.Fatalf("%016x: ParseHash(%q): %v", uint64(h), s, err)
		}
		if back != h {
			t.Errorf("round trip: got %016x, want %016x", uint64(back), uint64(h))
		}
	}
	// The top-bit case is the reason hashes are stored as text at all.
	if _, err := ParseHash("nothex"); err == nil {
		t.Error("ParseHash(\"nothex\"): want an error")
	}
	if _, err := ParseHash("abcd"); err == nil {
		t.Error("ParseHash(short): want an error")
	}
}

func TestDistanceAndSameImage(t *testing.T) {
	if d := Distance(0, 0); d != 0 {
		t.Errorf("identical: %d, want 0", d)
	}
	if d := Distance(0, ^Hash(0)); d != 64 {
		t.Errorf("opposite: %d, want 64", d)
	}
	// Expressed against SameThreshold rather than a literal. The first version
	// of this test hard-coded 5 and 6, and when the threshold moved to 12 on
	// real-file evidence it failed for the wrong reason — the boundary is the
	// contract, the number behind it is calibration.
	var atLimit, overLimit Hash
	for i := 0; i < SameThreshold; i++ {
		atLimit |= 1 << uint(i)
	}
	overLimit = atLimit | 1<<uint(SameThreshold)

	if Distance(0, atLimit) != SameThreshold {
		t.Fatalf("fixture: %d bits, want %d", Distance(0, atLimit), SameThreshold)
	}
	if !SameImage(0, atLimit) {
		t.Errorf("%d bits apart (exactly SameThreshold) must count as the same image", SameThreshold)
	}
	if SameImage(0, overLimit) {
		t.Errorf("%d bits apart (one over SameThreshold) must not count as the same image", SameThreshold+1)
	}
}
