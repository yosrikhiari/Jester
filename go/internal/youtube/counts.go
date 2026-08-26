package youtube

import (
	"regexp"
	"strconv"
	"strings"
)

// YouTube renders every number for humans and nothing for machines: "306K"
// likes, "1.8B views", "963 replies", "19,352,172" inside an aria-label. These
// turn that back into a count.

var (
	// A number with optional thousands separators and an optional magnitude
	// suffix, anywhere in a label.
	countRE = regexp.MustCompile(`(?i)(\d[\d,.\s\x{00A0}]*)\s*([KMB])?\b`)
	// Locale variants seen in the wild: "1,5 Mio." (de), "1.5M" (en).
	suffixes = map[string]float64{"K": 1e3, "M": 1e6, "B": 1e9}
)

// ParseCount reads the first count out of a rendered label.
//
// The bool is the whole point: "no number here" (a dislike button with no
// count, an absent element) must NOT come back as 0, or a video nobody
// disliked and a video whose dislikes YouTube stopped publishing become the
// same record.
//
// Abbreviated forms lose precision at the source — "306K" is somewhere in
// [305500, 306499] — so the value is the abbreviation's face value and no
// attempt is made to look more exact than the page was.
func ParseCount(raw string) (int64, bool) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return 0, false
	}
	m := countRE.FindStringSubmatch(s)
	if m == nil {
		return 0, false
	}
	digits := m[1]
	// Strip grouping separators. A trailing "." or "," directly before the
	// suffix is a decimal point in some locales ("1,5 Mio"), so the LAST
	// separator is kept as a decimal point when a magnitude suffix follows.
	digits = strings.Map(func(r rune) rune {
		if r == ' ' || r == '\u00A0' {
			return -1
		}
		return r
	}, digits)
	suffix := strings.ToUpper(m[2])
	if suffix != "" {
		if i := strings.LastIndexAny(digits, ".,"); i >= 0 && len(digits)-i-1 <= 2 {
			digits = strings.ReplaceAll(digits[:i], ",", "") + "." + digits[i+1:]
		} else {
			digits = strings.NewReplacer(",", "", ".", "").Replace(digits)
		}
	} else {
		digits = strings.NewReplacer(",", "", ".", "").Replace(digits)
	}
	digits = strings.TrimRight(digits, ".,")
	if digits == "" {
		return 0, false
	}
	f, err := strconv.ParseFloat(digits, 64)
	if err != nil {
		return 0, false
	}
	if mult, ok := suffixes[suffix]; ok {
		f *= mult
	}
	return int64(f), true
}
