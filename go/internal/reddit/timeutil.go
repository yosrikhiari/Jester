package reddit

import (
	"strings"
	"time"
)

// timeLayouts are the shapes these platforms actually emit. Reddit's
// shreddit-comment carries `created="2026-08-26T07:27:21.991000+0000"` — a
// six-digit fraction and a +0000 offset with no colon, which is NOT RFC3339
// and which time.Parse(time.RFC3339) rejects outright. The inner <time
// datetime> uses the Z form instead, and the JSON APIs use milliseconds.
var timeLayouts = []string{
	time.RFC3339Nano,
	time.RFC3339,
	"2006-01-02T15:04:05.999999-0700",
	"2006-01-02T15:04:05-0700",
	"2006-01-02T15:04:05.999999Z0700",
	"2006-01-02 15:04:05 -0700 MST",
	"2006-01-02T15:04:05",
	"2006-01-02",
}

// NormalizeTime renders a published timestamp as RFC3339 UTC.
//
// Returns "" for anything it cannot parse, and that is the point: a timestamp
// nobody can read is not a timestamp, and guessing one (now(), the run's start
// time) would file the comment under a time the platform never claimed. The
// caller keeps the platform's own words in CreatedRaw instead.
func NormalizeTime(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	for _, layout := range timeLayouts {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC().Format(time.RFC3339)
		}
	}
	return ""
}

// normalizeTime is the package-internal spelling used by the DOM mappers.
func normalizeTime(raw string) string { return NormalizeTime(raw) }

// FromUnix renders a Unix second count as RFC3339 UTC. Zero is treated as
// "not published" rather than 1970.
func FromUnix(sec int64) string {
	if sec <= 0 {
		return ""
	}
	return time.Unix(sec, 0).UTC().Format(time.RFC3339)
}
