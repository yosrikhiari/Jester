package reddit

import (
	"testing"
	"time"
)

func TestPaceStaysInBandAndVaries(t *testing.T) {
	const base = 2 * time.Second
	lo := time.Duration(float64(base) * (1 - JitterFraction))
	hi := time.Duration(float64(base) * (1 + JitterFraction))

	seen := map[time.Duration]bool{}
	for i := 0; i < 200; i++ {
		got := Pace(base)
		if got < lo || got > hi {
			t.Fatalf("Pace(%v)=%v outside [%v,%v]", base, got, lo, hi)
		}
		seen[got] = true
	}
	// The whole point is that it is not a constant.
	if len(seen) < 50 {
		t.Errorf("Pace produced only %d distinct delays in 200 draws", len(seen))
	}
}

func TestPaceLeavesNonPositiveDelaysAlone(t *testing.T) {
	// "No pacing" is a deliberate setting (tests, fixtures); it must not turn
	// into "a little pacing".
	for _, d := range []time.Duration{0, -time.Second} {
		if got := Pace(d); got != d {
			t.Errorf("Pace(%v)=%v, want %v", d, got, d)
		}
	}
}
