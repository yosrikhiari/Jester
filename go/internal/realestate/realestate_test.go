package realestate

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"jester/internal/siteprofile"
)

// A profile whose pagination has not been worked out must fetch ONE page.
//
// This is the regression that motivated pagePlan. buildPageURL has no
// parameter to advance, so every page number resolves to the same URL; the old
// loop ran max_pages times over that one URL and appended its listings again
// each time. The cost is not cosmetic — each repeat is a real headless
// navigation charged against the portal's rate limit.
func TestPagePlanHonoursUnsetPageParam(t *testing.T) {
	got := pagePlan(siteprofile.List{
		URL:       "https://www.tayara.tn/listing/c/real_estate/",
		PageParam: "",
		MaxPages:  5,
	}, 10)
	if len(got) != 1 {
		t.Fatalf("want 1 page when page_param is unset, got %d: %v", len(got), got)
	}
	if got[0] != "https://www.tayara.tn/listing/c/real_estate/" {
		t.Errorf("page URL rewritten: %s", got[0])
	}
}

func TestPagePlanWalksPagesWhenParamIsKnown(t *testing.T) {
	got := pagePlan(siteprofile.List{
		URL:       "https://www.seloger.com/listing/parametric",
		PageParam: "page",
		MaxPages:  3,
	}, 10)
	want := []string{
		"https://www.seloger.com/listing/parametric",
		"https://www.seloger.com/listing/parametric?page=2",
		"https://www.seloger.com/listing/parametric?page=3",
	}
	if len(got) != len(want) {
		t.Fatalf("want %d pages, got %d: %v", len(want), len(got), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("page %d = %s, want %s", i+1, got[i], want[i])
		}
	}
}

func TestPagePlanClampsToPerSource(t *testing.T) {
	got := pagePlan(siteprofile.List{URL: "https://x.test/s", PageParam: "p", MaxPages: 9}, 2)
	if len(got) != 2 {
		t.Fatalf("want perSource=2 to cap the walk, got %d pages", len(got))
	}
}

// A missing depth is "no ceiling given", not "no pages". Clamping to a
// non-positive perSource would walk nothing and report the source as empty,
// which is the same silent-nothing failure the profile validator exists to
// prevent.
func TestPagePlanTreatsNonPositivePerSourceAsNoCeiling(t *testing.T) {
	for _, depth := range []int{0, -1} {
		got := pagePlan(siteprofile.List{URL: "https://x.test/s", PageParam: "p", MaxPages: 3}, depth)
		if len(got) != 3 {
			t.Errorf("perSource=%d: want 3 pages, got %d", depth, len(got))
		}
	}
}

func TestPagePlanDefaultsToOnePageWhenMaxPagesUnset(t *testing.T) {
	got := pagePlan(siteprofile.List{URL: "https://x.test/s", PageParam: "p"}, 5)
	if len(got) != 1 {
		t.Fatalf("want 1 page when max_pages is unset, got %d", len(got))
	}
}

func TestPageURLAppendsToAnExistingQuery(t *testing.T) {
	got := PageURL("https://x.test/s?type=flat", siteprofile.List{PageParam: "page"}, 2)
	want := "https://x.test/s?type=flat&page=2"
	if got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

// The shipped profiles are the reason this matters, so they are the test.
// No profile may plan the same URL twice: that is a page fetched, parsed and
// appended for a second time, against a live portal, for nothing.
func TestShippedProfilesNeverPlanTheSameURLTwice(t *testing.T) {
	dir := filepath.Join("..", "..", "..", "config", "profiles")
	files, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no profiles found in %s", dir)
	}
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		p, err := siteprofile.Load(b)
		if err != nil {
			t.Fatalf("%s: %v", filepath.Base(f), err)
		}
		seen := map[string]bool{}
		for _, u := range pagePlan(p.List, 10) {
			if seen[u] {
				t.Errorf("%s: plans %s more than once", filepath.Base(f), u)
			}
			seen[u] = true
		}
	}
}

func TestPaceKeepsZeroAtZeroAndStaysInBand(t *testing.T) {
	if got := Pace(0); got != 0 {
		t.Errorf(`Pace(0) = %v; "no pacing" must not become "a little pacing"`, got)
	}
	if got := Pace(-time.Second); got != -time.Second {
		t.Errorf("Pace(-1s) = %v, want it left alone", got)
	}
	base := 2 * time.Second
	lo := time.Duration(float64(base) * (1 - JitterFraction))
	hi := time.Duration(float64(base) * (1 + JitterFraction))
	for i := 0; i < 500; i++ {
		got := Pace(base)
		if got < lo || got > hi {
			t.Fatalf("Pace(2s) = %v, outside [%v, %v]", got, lo, hi)
		}
	}
}

// Mubawab pages by PATH: ":p:2" returns 33 adids disjoint from page one,
// while ?page=2 re-serves page one. No page_param can express a suffix, so
// without a template the portal is stuck on its first page forever - which is
// exactly where it had been sitting.
func TestPageTemplateHandlesPathPagination(t *testing.T) {
	list := siteprofile.List{
		URL:          "https://www.mubawab.tn/fr/ct/tunis/immobilier-a-vendre",
		PageTemplate: "{url}:p:{page}",
		MaxPages:     3,
	}
	if got := PageURL(list.URL, list, 1); got != list.URL {
		t.Errorf("page 1 rewritten: %s", got)
	}
	want := "https://www.mubawab.tn/fr/ct/tunis/immobilier-a-vendre:p:2"
	if got := PageURL(list.URL, list, 2); got != want {
		t.Errorf("got %s, want %s", got, want)
	}
	plan := pagePlan(list, 10)
	if len(plan) != 3 {
		t.Fatalf("want 3 pages, got %d: %v", len(plan), plan)
	}
	seen := map[string]bool{}
	for _, u := range plan {
		if seen[u] {
			t.Errorf("planned %s twice", u)
		}
		seen[u] = true
	}
}

// A profile that declares neither is still one page, and a template must not
// be mistaken for a query parameter.
func TestPagePlanTreatsTemplateAsPagination(t *testing.T) {
	neither := pagePlan(siteprofile.List{URL: "https://x.test/s", MaxPages: 5}, 10)
	if len(neither) != 1 {
		t.Errorf("no page_param and no page_template must mean one page, got %d", len(neither))
	}
}

// Some portals cannot be paged at all, and for those the seed list IS the
// coverage. Tunisie Annonce re-serves the same 25 rows for every pagination
// parameter that exists, but answers a different 25 per region: 25 listings
// became 519 by walking its own region links.
func TestPagePlanWalksEverySeed(t *testing.T) {
	list := siteprofile.List{
		URL:      "https://x.test/a",
		URLs:     []string{"https://x.test/b", "https://x.test/c"},
		MaxPages: 5, // no page_param and no template: one fetch per seed
	}
	got := pagePlan(list, 100)
	if len(got) != 3 {
		t.Fatalf("want one fetch per seed (3), got %d: %v", len(got), got)
	}
	seen := map[string]bool{}
	for _, u := range got {
		if seen[u] {
			t.Errorf("planned %s twice", u)
		}
		seen[u] = true
	}
}

// With pagination, every seed gets the full walk.
func TestPagePlanCrossesSeedsWithPages(t *testing.T) {
	list := siteprofile.List{
		URL:       "https://x.test/a",
		URLs:      []string{"https://x.test/b"},
		PageParam: "page",
		MaxPages:  3,
	}
	got := pagePlan(list, 100)
	if len(got) != 6 {
		t.Fatalf("want 2 seeds x 3 pages = 6, got %d: %v", len(got), got)
	}
	if got[0] != "https://x.test/a" || got[3] != "https://x.test/b" {
		t.Errorf("seed-major ordering broken: %v", got)
	}
	if got[1] != "https://x.test/a?page=2" {
		t.Errorf("page param not applied per seed: %s", got[1])
	}
}

// A profile written the old way - one `url`, no `urls` - must behave exactly
// as it did before seeds existed.
func TestSeedsAreBackwardCompatibleAndDeduplicated(t *testing.T) {
	one := siteprofile.List{URL: "https://x.test/a"}
	if s := one.Seeds(); len(s) != 1 || s[0] != "https://x.test/a" {
		t.Errorf("single-url profile changed shape: %v", s)
	}
	// A url repeated in urls is one seed, not two fetches of the same page.
	dup := siteprofile.List{URL: "https://x.test/a", URLs: []string{"https://x.test/a", ""}}
	if s := dup.Seeds(); len(s) != 1 {
		t.Errorf("want the repeat and the blank dropped, got %v", s)
	}
}

// Habitaclia pages by rewriting the FILENAME - viviendas-madrid.htm becomes
// viviendas-madrid-2.htm - and ?pagina=2 silently re-serves page one, which
// cost 47 duplicate rows out of 112 before this existed. {url} alone cannot
// express it: it would produce "viviendas-madrid.htm-2.htm".
func TestPageTemplateCanRewriteTheFilename(t *testing.T) {
	list := siteprofile.List{
		URL:          "https://www.habitaclia.com/viviendas-madrid.htm",
		PageTemplate: "{url_base}-{page}.htm",
		MaxPages:     3,
	}
	if got := PageURL(list.URL, list, 1); got != list.URL {
		t.Errorf("page 1 rewritten: %s", got)
	}
	want := "https://www.habitaclia.com/viviendas-madrid-2.htm"
	if got := PageURL(list.URL, list, 2); got != want {
		t.Errorf("got %s, want %s", got, want)
	}
}

// Only a dot in the last path segment is an extension. The dots in a hostname
// are not, and a URL with no extension must come back untouched.
func TestTrimURLExtLeavesHostnamesAndExtensionlessURLsAlone(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"https://www.habitaclia.com/viviendas-madrid.htm", "https://www.habitaclia.com/viviendas-madrid"},
		{"https://www.example.co.uk/for-sale", "https://www.example.co.uk/for-sale"},
		{"https://www.example.co.uk", "https://www.example.co.uk"},
		{"https://x.test/a/b.html", "https://x.test/a/b"},
	} {
		if got := trimURLExt(c.in); got != c.want {
			t.Errorf("%s -> %s, want %s", c.in, got, c.want)
		}
	}
}
