// Package siteprofile makes a portal adapter a config file instead of a Go
// package.
//
// WHY. Nine hand-written adapters cost 300 to 580 lines each plus an arm in a
// 390-line switch, and that was affordable because eight of the nine read a
// documented JSON API that does not move. Twenty-six property portals are
// neither: no API, and markup that changes whenever a design team feels like
// it. Written the same way they are roughly ten thousand lines of Go aimed at
// a moving target. The repo already contains the alternative — one Discourse
// connector serves 38 forums — and this generalises it.
//
// WHY NO CSS SELECTORS. A selector engine means golang.org/x/net/html, which
// is not currently in this module's graph, and this project has refused
// dependencies over smaller savings than that. It turned out not to be needed:
// the two South African portals — the only pair on the roster where both
// halves answer plain HTTP — are covered by two modes that the standard
// library already supports.
//
//	ldjson    parse <script type="application/ld+json">, keep the blocks whose
//	          @type matches, and read fields by path. Private Property publishes
//	          schema.org Residence this way: url, photos, address, geo, room
//	          counts, all machine-readable and stable because the format is a
//	          public standard rather than a class name.
//
//	anchored  split the document on a repeated marker, then read fields inside
//	          each block by pattern. Property24 hangs data-listing-number on
//	          every result tile, which is a far more stable hook than the
//	          presentational classes around it.
//
// WHERE THIS BREAKS, stated plainly: neither mode understands nesting, so a
// field that can only be located by "the third <span> inside the second <div>"
// is out of reach and the profile has to find a different anchor. That is a
// real limit. It is also the limit that keeps profiles readable, and if a
// portal genuinely needs a tree walk, that is the moment to weigh x/net on
// evidence rather than in advance.
package siteprofile

import (
	"encoding/json"
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"

	"jester/internal/store"
)

// Profile is one portal, as a file.
type Profile struct {
	Portal string `yaml:"portal"`
	Market string `yaml:"market"`
	// BaseURL resolves the relative hrefs most portals emit.
	BaseURL string `yaml:"base_url"`
	// Currency is the portal's own, which no listing page ever states twice.
	Currency string  `yaml:"currency"`
	List     List    `yaml:"list"`
	Extract  Extract `yaml:"extract"`
}

// List is where result pages live and how to walk them.
type List struct {
	URL string `yaml:"url"`
	// PageParam is the query parameter that advances the listing, empty when
	// pagination has not been worked out for this portal yet. Absent means one
	// page — which is the honest default, not a silent full crawl.
	PageParam string `yaml:"page_param"`
	MaxPages  int    `yaml:"max_pages"`
}

// Extract names the mode and carries its configuration.
type Extract struct {
	Mode string `yaml:"mode"` // "ldjson" | "anchored"

	// --- ldjson -------------------------------------------------------------
	Type string `yaml:"type"` // the @type to keep, e.g. "Residence"

	// --- anchored -----------------------------------------------------------
	// Anchor must contain a named group `id`; each match starts a block.
	Anchor string `yaml:"anchor"`
	// BlockLen bounds how far a block extends past its anchor. A block that
	// runs to the next anchor would swallow the last listing on the page into
	// the rest of the document.
	BlockLen int `yaml:"block_len"`
	// BlockStart backs the block up to its container. Without it a block
	// begins AT the anchor, and an anchor nested inside the container — Private
	// Property's listing number appears inside a <script> tag — leaves the
	// opening tag outside the block, so the structured data inside it is
	// invisible. The block begins at the last match at or before the anchor.
	BlockStart string `yaml:"block_start"`

	Fields  map[string]Field `yaml:"fields"`
	Gallery Field            `yaml:"gallery"`
}

// Field is one value, read either from a JSON path or a pattern.
type Field struct {
	// Path is a dotted path into an ldjson block. `photo.contentUrl` reaches
	// into arrays, collecting from every element.
	Path string `yaml:"path"`
	// Pattern is a regexp whose first capturing group is the value.
	Pattern string `yaml:"pattern"`
	// Const wins over both, for facts the page never states (currency).
	Const string `yaml:"const"`
	// Transform is applied in order: digits, unescape, trim, absolute.
	Transform []string `yaml:"transform"`
	// All collects every match rather than the first. Galleries need it.
	All bool `yaml:"all"`
	// DedupBy is a pattern whose first group is the identity two values share
	// when they are the same thing. A gallery is the reason it exists: one
	// photograph is published at several crop sizes, so the URLs differ while
	// the picture does not, and without this the same image is fingerprinted
	// twice and the gallery hash moves whenever a portal adds a size.
	DedupBy string `yaml:"dedup_by"`
}

// Load reads a profile and rejects one that cannot work before it is used
// against a live site.
func Load(b []byte) (*Profile, error) {
	var p Profile
	if err := yaml.Unmarshal(b, &p); err != nil {
		return nil, fmt.Errorf("siteprofile: parse: %w", err)
	}
	if err := p.Validate(); err != nil {
		return nil, err
	}
	return &p, nil
}

// Validate fails loudly at load rather than quietly at 3am.
//
// The checks are the ones whose absence produces a profile that runs and
// returns nothing — which is indistinguishable from a portal that has no
// listings, and is the failure mode this whole package exists to avoid.
func (p *Profile) Validate() error {
	if p.Portal == "" {
		return fmt.Errorf("siteprofile: portal is required")
	}
	switch p.Extract.Mode {
	case "ldjson":
		if p.Extract.Type == "" {
			return fmt.Errorf("siteprofile %s: ldjson mode needs a type", p.Portal)
		}
	case "anchored":
		if p.Extract.Anchor == "" {
			return fmt.Errorf("siteprofile %s: anchored mode needs an anchor", p.Portal)
		}
		re, err := regexp.Compile(p.Extract.Anchor)
		if err != nil {
			return fmt.Errorf("siteprofile %s: anchor: %w", p.Portal, err)
		}
		if !hasGroup(re, "id") {
			return fmt.Errorf("siteprofile %s: anchor must capture a named group `id`", p.Portal)
		}
		if p.Extract.BlockLen <= 0 {
			return fmt.Errorf("siteprofile %s: anchored mode needs a positive block_len", p.Portal)
		}
		if p.Extract.BlockStart != "" {
			if _, err := regexp.Compile(p.Extract.BlockStart); err != nil {
				return fmt.Errorf("siteprofile %s: block_start: %w", p.Portal, err)
			}
		}
	default:
		return fmt.Errorf("siteprofile %s: unknown mode %q", p.Portal, p.Extract.Mode)
	}
	for name, f := range p.Extract.Fields {
		if err := f.validate(p.Portal, name); err != nil {
			return err
		}
	}
	if err := p.Extract.Gallery.validate(p.Portal, "gallery"); err != nil {
		return err
	}
	return nil
}

func (f Field) validate(portal, name string) error {
	if f.Const == "" && f.Path == "" && f.Pattern == "" {
		return nil // an unset field is allowed; an unusable one is not
	}
	if f.Pattern != "" {
		re, err := regexp.Compile(f.Pattern)
		if err != nil {
			return fmt.Errorf("siteprofile %s: field %s: %w", portal, name, err)
		}
		if re.NumSubexp() < 1 {
			return fmt.Errorf("siteprofile %s: field %s: pattern needs a capturing group", portal, name)
		}
	}
	for _, t := range f.Transform {
		switch t {
		case "digits", "unescape", "trim", "absolute", "striptags":
		default:
			return fmt.Errorf("siteprofile %s: field %s: unknown transform %q", portal, name, t)
		}
	}
	if f.DedupBy != "" {
		re, err := regexp.Compile(f.DedupBy)
		if err != nil {
			return fmt.Errorf("siteprofile %s: field %s: dedup_by: %w", portal, name, err)
		}
		if re.NumSubexp() < 1 {
			return fmt.Errorf("siteprofile %s: field %s: dedup_by needs a capturing group", portal, name)
		}
	}
	return nil
}

func hasGroup(re *regexp.Regexp, want string) bool {
	for _, n := range re.SubexpNames() {
		if n == want {
			return true
		}
	}
	return false
}

// Parse turns one fetched page into listings.
func (p *Profile) Parse(body string) ([]store.Listing, error) {
	switch p.Extract.Mode {
	case "ldjson":
		return p.parseLDJSON(body)
	case "anchored":
		return p.parseAnchored(body)
	}
	return nil, fmt.Errorf("siteprofile %s: unknown mode %q", p.Portal, p.Extract.Mode)
}

var tagRe = regexp.MustCompile(`<[^>]*>`)

var ldjsonRe = regexp.MustCompile(`(?is)<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>`)

func (p *Profile) parseLDJSON(body string) ([]store.Listing, error) {
	var out []store.Listing
	for _, m := range ldjsonRe.FindAllStringSubmatch(body, -1) {
		var any1 any
		if err := json.Unmarshal([]byte(strings.TrimSpace(m[1])), &any1); err != nil {
			// A portal that emits one malformed block still publishes the
			// others; a parse error here is not a failed page.
			continue
		}
		for _, node := range flatten(any1) {
			obj, ok := node.(map[string]any)
			if !ok || obj["@type"] != p.Extract.Type {
				continue
			}
			l := store.Listing{Portal: p.Portal, Currency: p.Currency}
			payload := map[string]any{}
			for name, f := range p.Extract.Fields {
				v := p.apply(f, jsonPath(obj, f.Path))
				p.assign(&l, payload, name, v)
			}
			for i, u := range p.apply(p.Extract.Gallery, jsonPath(obj, p.Extract.Gallery.Path)) {
				l.Media = append(l.Media, store.Media{Position: i, URL: u})
			}
			if l.ListingID == "" {
				continue // a listing with no identity cannot be observed twice
			}
			l.Payload = mustJSON(payload)
			out = append(out, l)
		}
	}
	return out, nil
}

func (p *Profile) parseAnchored(body string) ([]store.Listing, error) {
	anchor := regexp.MustCompile(p.Extract.Anchor)
	idx := anchor.SubexpIndex("id")
	locs := anchor.FindAllStringSubmatchIndex(body, -1)

	var startRe *regexp.Regexp
	var startAt []int
	if p.Extract.BlockStart != "" {
		startRe = regexp.MustCompile(p.Extract.BlockStart)
		for _, m := range startRe.FindAllStringIndex(body, -1) {
			startAt = append(startAt, m[0])
		}
	}

	seen := map[string]bool{}
	var out []store.Listing
	for _, loc := range locs {
		id := body[loc[2*idx]:loc[2*idx+1]]
		// A portal that repeats its anchor inside the tile (Property24 puts
		// data-listing-number on both the container and an inner div) would
		// otherwise yield the same listing twice.
		if seen[id] {
			continue
		}
		seen[id] = true

		begin := loc[0]
		if startRe != nil {
			// The nearest container opening at or before the anchor.
			for _, s0 := range startAt {
				if s0 <= loc[0] {
					begin = s0
				} else {
					break
				}
			}
		}

		// A block ends where the next listing begins, capped by block_len.
		//
		// A fixed length alone was wrong and the real page proved it: tiles run
		// about 5 KB, block_len was 7 KB, and the first listing picked up the
		// SECOND listing's title — a farm in Fisantekraal reported as a two-bed
		// flat in Tamboerskloof. Wrong data that looks right is the worst
		// outcome available here, so the boundary is structural and the length
		// is only a backstop for the last block on the page.
		end := begin + p.Extract.BlockLen
		for _, nxt := range locs {
			if nxt[0] <= loc[0] {
				continue
			}
			if body[nxt[2*idx]:nxt[2*idx+1]] == id {
				continue // same listing's repeated anchor, not a boundary
			}
			if nxt[0] < end {
				end = nxt[0]
			}
			break
		}
		if end > len(body) {
			end = len(body)
		}
		if end < begin {
			end = begin
		}
		block := body[begin:end]

		// A block may carry its own structured data. Private Property puts a
		// schema.org Residence inside every result, which is both richer and
		// steadier than the markup around it — but the PRICE is not in it, and
		// only the block boundary says which price belongs to which listing.
		// Reading both from inside one block is what makes them line up.
		embedded := ldjsonIn(block)

		l := store.Listing{Portal: p.Portal, ListingID: id, Currency: p.Currency}
		payload := map[string]any{"listing_id": id}
		for name, f := range p.Extract.Fields {
			p.assign(&l, payload, name, p.apply(f, valuesFor(f, block, embedded)))
		}
		for i, u := range p.apply(p.Extract.Gallery, valuesFor(p.Extract.Gallery, block, embedded)) {
			l.Media = append(l.Media, store.Media{Position: i, URL: u})
		}
		l.Payload = mustJSON(payload)
		out = append(out, l)
	}
	return out, nil
}

// valuesFor reads a field from a block: by JSON path when the block carries
// structured data and the field names one, by pattern otherwise.
func valuesFor(f Field, block string, embedded []any) []string {
	if f.Path != "" && len(embedded) > 0 {
		var out []string
		for _, node := range embedded {
			out = append(out, jsonPath(node, f.Path)...)
		}
		if len(out) > 0 {
			return out
		}
	}
	return patternValues(f, block)
}

// ldjsonIn decodes every ld+json block inside a fragment.
func ldjsonIn(block string) []any {
	var out []any
	for _, m := range ldjsonRe.FindAllStringSubmatch(block, -1) {
		var v any
		if err := json.Unmarshal([]byte(strings.TrimSpace(m[1])), &v); err != nil {
			continue
		}
		out = append(out, flatten(v)...)
	}
	return out
}

func patternValues(f Field, block string) []string {
	if f.Pattern == "" {
		return nil
	}
	re, err := regexp.Compile(f.Pattern)
	if err != nil {
		return nil
	}
	if f.All {
		var vs []string
		for _, m := range re.FindAllStringSubmatch(block, -1) {
			vs = append(vs, m[1])
		}
		return vs
	}
	if m := re.FindStringSubmatch(block); m != nil {
		return []string{m[1]}
	}
	return nil
}

// assign routes a named field onto the typed struct, and keeps everything in
// the payload regardless — the struct names the fields the pipeline reasons
// about, the payload keeps what the portal actually said.
func (p *Profile) assign(l *store.Listing, payload map[string]any, name string, vals []string) {
	if len(vals) == 0 {
		return
	}
	v := vals[0]
	switch name {
	case "listing_id":
		l.ListingID = v
	case "url":
		l.URL = v
	case "status":
		l.Status = v
	case "currency":
		l.Currency = v
	case "price":
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			l.Price = &n
		}
		// A price that will not parse is left nil rather than zeroed: "not
		// published" and "free" must stay different facts.
	}
	if len(vals) == 1 {
		payload[name] = v
	} else {
		payload[name] = vals
	}
}

func (p *Profile) apply(f Field, vals []string) []string {
	if f.Const != "" {
		return []string{f.Const}
	}
	// Path AND pattern together means "pull this out of that value". Needed
	// more often than it looks: Private Property's Residence block has no id
	// field, and the only place the listing number appears is inside the photo
	// URL. Without this the id would have to be the whole URL, which changes
	// whenever the image does — and an identity that moves is not an identity.
	if f.Path != "" && f.Pattern != "" {
		re, err := regexp.Compile(f.Pattern)
		if err == nil {
			var picked []string
			for _, v := range vals {
				if m := re.FindStringSubmatch(v); m != nil {
					picked = append(picked, m[1])
					if !f.All {
						break
					}
				}
			}
			vals = picked
		}
	}
	out := make([]string, 0, len(vals))
	for _, v := range vals {
		for _, t := range f.Transform {
			switch t {
			case "unescape":
				// &nbsp; between digit groups is why a price regex that looks
				// right returns "R 585" and nothing else.
				v = html.UnescapeString(v)
				v = strings.ReplaceAll(v, " ", " ")
			case "digits":
				var b strings.Builder
				for _, r := range v {
					if r >= '0' && r <= '9' {
						b.WriteRune(r)
					}
				}
				v = b.String()
			case "striptags":
				// p24_description holds "2 Bedroom Apartment in <span>Sea
				// Point</span>", so a pattern that stops at the first tag
				// returns half a sentence and looks like it worked.
				v = tagRe.ReplaceAllString(v, "")
			case "trim":
				v = strings.TrimSpace(v)
			case "absolute":
				if strings.HasPrefix(v, "/") {
					v = strings.TrimRight(p.BaseURL, "/") + v
				}
			}
		}
		if v != "" {
			out = append(out, v)
		}
	}
	if f.DedupBy != "" {
		re, err := regexp.Compile(f.DedupBy)
		if err == nil {
			seen := map[string]bool{}
			kept := out[:0]
			for _, v := range out {
				key := v
				if m := re.FindStringSubmatch(v); m != nil {
					key = m[1]
				}
				if seen[key] {
					continue
				}
				seen[key] = true
				kept = append(kept, v)
			}
			out = kept
		}
	}
	return out
}

// flatten walks arbitrary decoded JSON so a @type can be found at any depth.
func flatten(v any) []any {
	out := []any{v}
	switch t := v.(type) {
	case []any:
		for _, e := range t {
			out = append(out, flatten(e)...)
		}
	case map[string]any:
		for _, e := range t {
			out = append(out, flatten(e)...)
		}
	}
	return out
}

// jsonPath reads a dotted path, collecting across arrays.
func jsonPath(node any, path string) []string {
	if path == "" {
		return nil
	}
	cur := []any{node}
	for _, seg := range strings.Split(path, ".") {
		var next []any
		for _, n := range cur {
			switch t := n.(type) {
			case map[string]any:
				if v, ok := t[seg]; ok {
					next = append(next, v)
				}
			case []any:
				for _, e := range t {
					if m, ok := e.(map[string]any); ok {
						if v, ok := m[seg]; ok {
							next = append(next, v)
						}
					}
				}
			}
		}
		cur = next
	}
	var out []string
	for _, n := range cur {
		switch t := n.(type) {
		case string:
			out = append(out, t)
		case float64:
			out = append(out, strconv.FormatFloat(t, 'f', -1, 64))
		case []any:
			for _, e := range t {
				if s, ok := e.(string); ok {
					out = append(out, s)
				}
			}
		}
	}
	return out
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
}
