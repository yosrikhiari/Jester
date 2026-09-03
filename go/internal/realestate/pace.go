package realestate

import (
	"math/rand"
	"time"
)

// JitterFraction is how far Pace may move a configured delay in either
// direction. ±30% keeps the average at the configured value while
// removing the fixed period that is one of the cheapest behavioural
// signals a platform has.
const JitterFraction = 0.30

// Pace spreads a configured delay so a run does not tick like a metronome.
// A non-positive delay stays non-positive: "no pacing" must not become
// "a little pacing".
func Pace(d time.Duration) time.Duration {
	if d <= 0 {
		return d
	}
	spread := float64(d) * JitterFraction
	out := float64(d) + (rand.Float64()*2-1)*spread
	if out < 0 {
		return 0
	}
	return time.Duration(out)
}
