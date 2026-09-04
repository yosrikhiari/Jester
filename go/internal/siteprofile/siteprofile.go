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
	"sort"
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
	Currency string `yaml:"currency"`
	// Fetch is "http" or "browser" (default). It belongs to the PORTAL, not
	// the platform: Property24, Private Property and Tayara all serve their
	// listings - Tayara's __NEXT_DATA__ included - to an ordinary GET, and
	// requiring cloakserve for them made a licensed commercial container a
	// prerequisite for reading a public page.
	Fetch   string  `yaml:"fetch"`
	List    List    `yaml:"list"`
	Extract Extract `yaml:"extract"`
}

// List is where result pages live and how to walk them.
type List struct {
	URL string `yaml:"url"`
	// URLs are additional starting points for the same portal, and for some
	// portals they are the ONLY way to reach more inventory.
	//
	// Tunisie Annonce is the clearest case: it has no pagination a GET can
	// drive - nine candidate parameters were tried and every one re-served the
	// same 25 rows - but its own page links to 38 different region and locality
	// searches. Houni is the same story by category rather than geography: one
	// URL covers apartments for sale and nothing else. A profile limited to a
	// single search URL is limited to whatever that one search returns, which
	// on these two portals was 25 and 18 listings respectively.
	URLs []string `yaml:"urls"`
	// PageParam is the query parameter that advances the listing, empty when
	// pagination has not been worked out for this portal yet. Absent means one
	// page — which is the honest default, not a silent full crawl.
	PageParam string `yaml:"page_param"`
	// PageTemplate handles portals that page by PATH rather than by query
	// parameter. `{url}` is the list URL and `{page}` the number, so Mubawab's
	// second page is "{url}:p:{page}". Without it a portal like that has no way
	// to say how it paginates and is stuck on page one - page_param cannot
	// express a suffix, and guessing one produces a profile that silently walks
	// the wrong URLs.
	PageTemplate string `yaml:"page_template"`
	MaxPages     int    `yaml:"max_pages"`
}

// Extract names the mode and carries its configuration.
type Extract struct {
	Mode string `yaml:"mode"` // "ldjson" | "anchored" | "nextdata"

	// --- ldjson -------------------------------------------------------------
	Type string `yaml:"type"` // the @type to keep, e.g. "Residence"

	// --- nextdata -----------------------------------------------------------
	// DataPath is a dotted JSON path inside __NEXT_DATA__ that points to the
	// array of listing objects, e.g. "props.pageProps.searchedListingsAction.newHits"
	DataPath string `yaml:"data_path"`
	// URLTemplate builds a listing URL for a portal that publishes an id but
	// no href - or one whose href is not worth keeping. Tunisie Annonce links
	// to "Details_Annonces_Immobilier.asp?cod_ann=3398050&titre=Terrain entre
	// el haouria et kelibia": unencoded spaces and cp1252 accents in a query
	// string, which reached the archive as undecodable bytes and took the whole
	// CSV export to zero rows once. The id alone addresses the same page.
	//
	// Applies in every parse mode. `{id}` is substituted; a value beginning "/" resolves against
	// base_url. Before it existed the URL was hardcoded to Tayara's
	// "/listing/i/{id}" shape for EVERY nextdata portal, so any other one got
	// a plausible-looking address pointing nowhere.
	URLTemplate string `yaml:"url_template"`

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
	// Filter drops records that sit on the result page without being what the
	// profile came for. Tayara's real-estate listing carries boosted ads and
	// the occasional car. The alternative to saying so here was the arm this
	// replaced: `if p.Portal == "tayara"` plus a keyword list, inside the
	// shared parser — a portal adapter written in Go, in the package whose
	// premise is that a portal adapter is a config file.
	Filter Filter `yaml:"filter"`
	// Classify derives a field the portal does not publish as one. It exists
	// for deal_type: Tayara's real-estate feed carries lettings beside sales
	// and says so only in the title ("location s+1 aux jardins de Carthage" is
	// a month's rent at 1550 TND), so a pipeline that compares prices across
	// listings is otherwise comparing a monthly rent with a purchase price.
	Classify Classify `yaml:"classify"`
	// Detail names fields that live on a listing's OWN page rather than on the
	// result page. It is a SECOND fetch per listing and is why it is opt-in:
	// a hundred listings is a hundred more requests at a portal, which is a
	// different order of traffic from reading five result pages.
	//
	// It exists because two fields could not be reached any other way. Property24
	// publishes a street address only on the listing page, and it is the field
	// that separates a real cross-portal duplicate from two different flats at
	// one price in one suburb - both confirmed false positives in the dedup
	// validation would be settled by it. Houni publishes no price on its result
	// tile at all: two patterns were tried against the tile and both produced
	// fiction, because the number is simply not there.
	Detail Detail `yaml:"detail"`
}

// Detail is the field set read from a listing's own page.
type Detail struct {
	Fields map[string]Field `yaml:"fields"`
}

// Wanted reports whether this profile has anything to fetch a detail page for.
func (d Detail) Wanted() bool { return len(d.Fields) > 0 }

// Classify turns a phrase in one extracted field into a value in another.
//
// It is deliberately not a general rules engine. It answers one question -
// "which of these buckets is this listing in" - because the alternative for
// deal_type was either a hardcoded language check in the shared parser (the
// mistake the tayara filter already made once) or a column the portals do not
// publish.
type Classify struct {
	// Field is the name of the derived field, e.g. "deal_type".
	Field string `yaml:"field"`
	// From is the extracted field whose text is read, e.g. "title".
	From string `yaml:"from"`
	// Default is the value when no rule matches. A portal reached through a
	// for-sale search should say "sale" here rather than leave it blank:
	// "unknown" and "not stated" are different facts from "sale".
	Default string `yaml:"default"`
	// Rules are tried in order; the first match wins.
	Rules []ClassifyRule `yaml:"rules"`
}

// ClassifyRule is one bucket and the phrases that put a listing in it.
// Matching is case-insensitive substring, as in Filter.
type ClassifyRule struct {
	Value string   `yaml:"value"`
	Match []string `yaml:"match"`
}

// classify returns the derived value, or "" when nothing is configured.
func (c Classify) classify(payload map[string]any) string {
	if c.Field == "" {
		return ""
	}
	raw, ok := payload[c.From]
	if !ok {
		return c.Default
	}
	v := strings.ToLower(fmt.Sprintf("%v", raw))
	for _, r := range c.Rules {
		for _, m := range r.Match {
			if m != "" && strings.Contains(v, strings.ToLower(m)) {
				return r.Value
			}
		}
	}
	return c.Default
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

// Filter keeps or drops a parsed record by what one of its fields says.
// Matching is case-insensitive substring rather than regexp: the values it
// tests are portal prose ("Appartement S+2 haut standing"), and an author
// listing the property types a market uses should not have to escape them.
type Filter struct {
	// Field names the extracted field to test, e.g. "title".
	Field string `yaml:"field"`
	// Exclude drops a record when any entry matches. Checked before Include.
	Exclude []string `yaml:"exclude"`
	// Include keeps ONLY records where some entry matches. Empty means keep
	// whatever Exclude did not drop.
	Include []string `yaml:"include"`
}

// keep reports whether a parsed record survives the filter.
func (f Filter) keep(payload map[string]any) bool {
	if len(f.Include) == 0 && len(f.Exclude) == 0 {
		return true
	}
	raw, ok := payload[f.Field]
	if !ok {
		// A record that does not carry the field cannot be judged by it. An
		// Include list drops it, because "keep only titles that say villa"
		// cannot admit a record with no title; an Exclude-only filter keeps
		// it, because nothing matched.
		return len(f.Include) == 0
	}
	v := strings.ToLower(fmt.Sprintf("%v", raw))
	for _, ex := range f.Exclude {
		if ex != "" && strings.Contains(v, strings.ToLower(ex)) {
			return false
		}
	}
	if len(f.Include) == 0 {
		return true
	}
	for _, in := range f.Include {
		if in != "" && strings.Contains(v, strings.ToLower(in)) {
			return true
		}
	}
	return false
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

// Seeds returns every starting URL for this list, `url` first.
//
// Always at least one entry when a URL is set anywhere, so a profile written
// either way behaves the same.
func (l List) Seeds() []string {
	out := make([]string, 0, len(l.URLs)+1)
	seen := map[string]bool{}
	for _, u := range append([]string{l.URL}, l.URLs...) {
		if u == "" || seen[u] {
			continue
		}
		seen[u] = true
		out = append(out, u)
	}
	return out
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
	if p.List.PageTemplate != "" {
		if p.List.PageParam != "" {
			return fmt.Errorf("siteprofile %s: set page_param OR page_template, not both", p.Portal)
		}
		if !strings.Contains(p.List.PageTemplate, "{page}") {
			return fmt.Errorf("siteprofile %s: page_template must contain {page}", p.Portal)
		}
	}
	switch strings.ToLower(p.Fetch) {
	case "", "http", "browser":
	default:
		return fmt.Errorf("siteprofile %s: unknown fetch %q, want http or browser", p.Portal, p.Fetch)
	}
	switch p.Extract.Mode {
	case "ldjson":
		if p.Extract.Type == "" {
			return fmt.Errorf("siteprofile %s: ldjson mode needs a type", p.Portal)
		}
	case "nextdata":
		if p.Extract.DataPath == "" {
			return fmt.Errorf("siteprofile %s: nextdata mode needs a data_path", p.Portal)
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
	// Every mode but anchored must be told where identity lives. Both parsers
	// drop a record whose listing_id came out empty, so a profile that never
	// names one walks a full page of results and emits nothing - which reads
	// downstream as "that portal had no listings today", and is precisely the
	// silent nothing this function exists to prevent. anchored is exempt: its
	// id comes from the anchor's `id` group, already checked above.
	if p.Extract.Mode != "anchored" {
		id, ok := p.Extract.Fields["listing_id"]
		if !ok || (id.Path == "" && id.Pattern == "" && id.Const == "") {
			return fmt.Errorf("siteprofile %s: %s mode needs a listing_id field; without one every record is dropped and a full page yields nothing", p.Portal, p.Extract.Mode)
		}
	}
	if p.Extract.Classify.Field != "" {
		if p.Extract.Classify.From == "" {
			return fmt.Errorf("siteprofile %s: classify needs a `from` field to read", p.Portal)
		}
		if _, ok := p.Extract.Fields[p.Extract.Classify.From]; !ok {
			return fmt.Errorf("siteprofile %s: classify reads field %q, which this profile does not extract", p.Portal, p.Extract.Classify.From)
		}
		if p.Extract.Classify.Default == "" && len(p.Extract.Classify.Rules) == 0 {
			return fmt.Errorf("siteprofile %s: classify has neither rules nor a default, so it can only ever produce nothing", p.Portal)
		}
	}
	// A filter naming a field nobody extracts silently keeps or drops
	// everything, depending which list it is on. Both are wrong quietly.
	if len(p.Extract.Filter.Include) > 0 || len(p.Extract.Filter.Exclude) > 0 {
		if p.Extract.Filter.Field == "" {
			return fmt.Errorf("siteprofile %s: filter needs a field to test", p.Portal)
		}
		if _, ok := p.Extract.Fields[p.Extract.Filter.Field]; !ok {
			return fmt.Errorf("siteprofile %s: filter tests field %q, which this profile does not extract", p.Portal, p.Extract.Filter.Field)
		}
	}
	for name, f := range p.Extract.Fields {
		if err := f.validate(p.Portal, name); err != nil {
			return err
		}
	}
	for name, f := range p.Extract.Detail.Fields {
		if err := f.validate(p.Portal, "detail."+name); err != nil {
			return err
		}
		if f.Path == "" && f.Pattern == "" && f.Const == "" {
			return fmt.Errorf("siteprofile %s: detail field %s has no path, pattern or const, so a second fetch would be spent for nothing", p.Portal, name)
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
	case "nextdata":
		return p.parseNextData(body)
	}
	return nil, fmt.Errorf("siteprofile %s: unknown mode %q", p.Portal, p.Extract.Mode)
}

var tagRe = regexp.MustCompile(`<[^>]*>`)

var ldjsonRe = regexp.MustCompile(`(?is)<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>`)
var nextDataRe = regexp.MustCompile(`(?is)<script[^>]+id=["']__(?:NEXT|NUXT)_DATA__["'][^>]*>(.*?)</script>`)
var nuxtJsonRe = regexp.MustCompile(`(?is)<script[^>]+type=["']application/json["'][^>]*data-nuxt-data[^>]*>(.*?)</script>`)

func (p *Profile) parseNextData(body string) ([]store.Listing, error) {
	m := nextDataRe.FindStringSubmatch(body)
	if m == nil {
		m = nuxtJsonRe.FindStringSubmatch(body)
		if m == nil {
			// Returning nothing quietly was the problem: a bot-check page, a
			// redirect to a consent wall and a genuine markup change all came
			// back as "no listings, no error", which the caller could only
			// report as a portal with nothing to sell.
			return nil, fmt.Errorf("siteprofile %s: no __NEXT_DATA__ or __NUXT_DATA__ script in the page", p.Portal)
		}
	}
	var root any
	if err := json.Unmarshal([]byte(strings.TrimSpace(m[1])), &root); err != nil {
		return nil, fmt.Errorf("siteprofile %s: __NEXT_DATA__ is not valid JSON: %w", p.Portal, err)
	}
	// Navigate to the array via data_path (dotted, supports only map traversal).
	nodes := jsonPathRaw(root, p.Extract.DataPath)
	if len(nodes) == 0 {
		// A path that resolves to NOTHING means the portal moved its schema.
		// A path resolving to an EMPTY ARRAY is a different fact - a result
		// page with no results - and falls through to return no listings and
		// no error, which is the honest answer for it.
		return nil, fmt.Errorf("siteprofile %s: data_path %q matched nothing in __NEXT_DATA__", p.Portal, p.Extract.DataPath)
	}
	// The path should resolve to an array; jsonPathRaw collects across arrays,
	// so we need to flatten one level if the result is a single slice.
	var listings []any
	for _, n := range nodes {
		if arr, ok := n.([]any); ok {
			listings = append(listings, arr...)
		} else {
			listings = append(listings, n)
		}
	}
	var out []store.Listing
	for _, node := range listings {
		obj, ok := node.(map[string]any)
		if !ok {
			continue
		}
		l := store.Listing{Portal: p.Portal, Currency: p.Currency}
		payload := p.newPayload()
		for name, f := range p.Extract.Fields {
			v := p.apply(f, jsonPath(obj, f.Path))
			p.assign(&l, payload, name, v)
		}
		if !p.Extract.Filter.keep(payload) {
			continue
		}
		if v := p.Extract.Classify.classify(payload); v != "" {
			payload[p.Extract.Classify.Field] = v
		}
		for i, u := range p.apply(p.Extract.Gallery, jsonPath(obj, p.Extract.Gallery.Path)) {
			l.Media = append(l.Media, store.Media{Position: i, URL: u})
		}
		if l.ListingID == "" {
			continue
		}
		if l.URL == "" && p.Extract.URLTemplate != "" {
			l.URL = p.listingURL(l.ListingID)
			payload["url"] = l.URL
		}
		// A row with no link is not a listing. Mubawab interleaves promoted
		// DEVELOPMENT banners among its result tiles: they carry no href, no
		// price and no property type, and the anchored parse scraped an id out
		// of their media path (/promotion/4/083F/ -> "4083"), so a banner
		// entered the archive as a listing and was re-recorded every time its
		// carousel rotated a photograph. Requiring the link drops exactly those
		// rows: over a 4141-listing run across ten portals, nine were at 100%
		// URL coverage and the only ten blanks were these.
		if l.URL == "" {
			continue
		}
		l.Payload = mustJSON(payload)
		out = append(out, l)
	}
	return out, nil
}

// ApplyDetail merges a listing's own page into a listing already parsed from
// the result page. It only ever ADDS: a field the result page filled is left
// alone, because that page is the one whose block boundaries were checked.
//
// Returns the names of the fields the detail page actually supplied, so a
// caller can report what the second fetch bought.
func (p *Profile) ApplyDetail(l *store.Listing, body string) []string {
	if !p.Extract.Detail.Wanted() {
		return nil
	}
	var payload map[string]any
	if l.Payload != "" {
		_ = json.Unmarshal([]byte(l.Payload), &payload)
	}
	if payload == nil {
		payload = map[string]any{}
	}
	embedded := ldjsonIn(body)
	var filled []string
	for name, f := range p.Extract.Detail.Fields {
		if existing, ok := payload[name]; ok {
			if s, isStr := existing.(string); !isStr || s != "" {
				continue // the result page already answered this
			}
		}
		vals := p.apply(f, valuesFor(f, body, embedded))
		if len(vals) == 0 {
			continue
		}
		p.assign(l, payload, name, vals)
		filled = append(filled, name)
	}
	if len(filled) == 0 {
		return nil
	}
	sort.Strings(filled)
	l.Payload = mustJSON(payload)
	return filled
}

// newPayload starts a listing's payload with the facts the PROFILE knows and
// the page never states.
//
// market is the one that bit: every profile declares it (market: tn, market:
// za, market: us) and it reached the JSONL export, because the harvester read
// it off the profile in memory. Nothing wrote it to the archive, so the
// database-driven CSV export had an empty market column on all 995 rows - the
// column that tells a reader whether a price is dinars or dollars.
func (p *Profile) newPayload() map[string]any {
	m := map[string]any{}
	if p.Market != "" {
		m["market"] = p.Market
	}
	return m
}

// listingURL renders url_template for one listing id.
func (p *Profile) listingURL(id string) string {
	u := strings.ReplaceAll(p.Extract.URLTemplate, "{id}", id)
	if strings.HasPrefix(u, "/") {
		u = strings.TrimRight(p.BaseURL, "/") + u
	}
	return u
}

// jsonPathRaw is like jsonPath but returns raw values (not stringified) for traversal.
func jsonPathRaw(node any, path string) []any {
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
	return cur
}

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
			payload := p.newPayload()
			for name, f := range p.Extract.Fields {
				v := p.apply(f, jsonPath(obj, f.Path))
				p.assign(&l, payload, name, v)
			}
			if !p.Extract.Filter.keep(payload) {
				continue
			}
			if v := p.Extract.Classify.classify(payload); v != "" {
				payload[p.Extract.Classify.Field] = v
			}
			for i, u := range p.apply(p.Extract.Gallery, jsonPath(obj, p.Extract.Gallery.Path)) {
				l.Media = append(l.Media, store.Media{Position: i, URL: u})
			}
			if l.ListingID == "" {
				continue // a listing with no identity cannot be observed twice
			}
			if l.URL == "" && p.Extract.URLTemplate != "" {
				l.URL = p.listingURL(l.ListingID)
				payload["url"] = l.URL
			}
			// A row with no link is not a listing. Mubawab interleaves promoted
			// DEVELOPMENT banners among its result tiles: they carry no href, no
			// price and no property type, and the anchored parse scraped an id out
			// of their media path (/promotion/4/083F/ -> "4083"), so a banner
			// entered the archive as a listing and was re-recorded every time its
			// carousel rotated a photograph. Requiring the link drops exactly those
			// rows: over a 4141-listing run across ten portals, nine were at 100%
			// URL coverage and the only ten blanks were these.
			if l.URL == "" {
				continue
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
		payload := p.newPayload()
		payload["listing_id"] = id
		for name, f := range p.Extract.Fields {
			p.assign(&l, payload, name, p.apply(f, valuesFor(f, block, embedded)))
		}
		if !p.Extract.Filter.keep(payload) {
			continue
		}
		if v := p.Extract.Classify.classify(payload); v != "" {
			payload[p.Extract.Classify.Field] = v
		}
		for i, u := range p.apply(p.Extract.Gallery, valuesFor(p.Extract.Gallery, block, embedded)) {
			l.Media = append(l.Media, store.Media{Position: i, URL: u})
		}
		if l.URL == "" && p.Extract.URLTemplate != "" {
			l.URL = p.listingURL(l.ListingID)
			payload["url"] = l.URL
		}
		// A row with no link is not a listing. Mubawab interleaves promoted
		// DEVELOPMENT banners among its result tiles: they carry no href, no
		// price and no property type, and the anchored parse scraped an id out
		// of their media path (/promotion/4/083F/ -> "4083"), so a banner
		// entered the archive as a listing and was re-recorded every time its
		// carousel rotated a photograph. Requiring the link drops exactly those
		// rows: over a 4141-listing run across ten portals, nine were at 100%
		// URL coverage and the only ten blanks were these.
		if l.URL == "" {
			continue
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
		// Zero is not a price. It is the same question the nil case answers
		// from the other side: Tayara publishes price 0 for "contact for
		// price" - a terrain à vendre with no figure, a villa whose asking
		// price appears only in the title - and nothing on these portals is
		// genuinely free. Recording it as 0 puts a number into every average,
		// median and price-history series that no seller ever asked for.
		if n, err := strconv.ParseInt(v, 10, 64); err == nil && n != 0 {
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
				// A portal publishes one of three shapes and this has to
				// handle all of them. Tunisie Annonce emits
				// "Details_Annonces_Immobilier.asp?cod_ann=..." with no
				// leading slash, and prefixing only paths that begin "/" left
				// all 25 of its listings carrying a URL that resolves nowhere
				// - the export flagged them as off-host, which is what a
				// relative path looks like once it leaves the page it came
				// from.
				switch {
				case strings.HasPrefix(v, "http://"), strings.HasPrefix(v, "https://"):
					// already absolute; leave it alone
				case strings.HasPrefix(v, "//"):
					v = "https:" + v
				case strings.HasPrefix(v, "/"):
					v = strings.TrimRight(p.BaseURL, "/") + v
				default:
					v = strings.TrimRight(p.BaseURL, "/") + "/" + v
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
		case bool:
			out = append(out, strconv.FormatBool(t))
		case int:
			out = append(out, strconv.Itoa(t))
		case []any:
			for _, e := range t {
				if s, ok := e.(string); ok {
					out = append(out, s)
				} else if f, ok := e.(float64); ok {
					out = append(out, strconv.FormatFloat(f, 'f', -1, 64))
				} else if b, ok := e.(bool); ok {
					out = append(out, strconv.FormatBool(b))
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
