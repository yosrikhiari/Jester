package reddit

import (
	"math/rand"
	"time"
)

// JitterFraction is how far Pace may move a configured delay in either
// direction. ±30% keeps the average at the configured value — so
// `request_delay_ms` still means what it says and the politeness budget is
// unchanged — while removing the fixed period.
const JitterFraction = 0.30

// Pace spreads a configured delay so a run does not tick like a metronome.
//
// The delay used to be applied verbatim: every navigation exactly 2000ms after
// the last, for hours. Inter-request timing is one of the cheapest behavioural
// signals a platform has, and a constant interval is the one pattern no human
// reading a listing ever produces — it survives a perfect canvas/WebGL
// fingerprint, because it is not about what the browser claims to be.
//
// A non-positive delay stays non-positive: "no pacing" must not become "a
// little pacing".
func Pace(d time.Duration) time.Duration {
	if d <= 0 {
		return d
	}
	spread := float64(d) * JitterFraction
	// rand.Float64() in [0,1) -> offset in [-spread, +spread).
	out := float64(d) + (rand.Float64()*2-1)*spread
	if out < 0 {
		return 0
	}
	return time.Duration(out)
}
