package cloakbrowser

import "testing"

func TestSeedForIsStableAndURLSafe(t *testing.T) {
	cases := map[[2]string]string{
		{"reddit", "selfhosted"}:    "jester-reddit-selfhosted",
		{"reddit", "SelfHosted"}:    "jester-reddit-selfhosted",
		{"youtube", "fosdem talks"}: "jester-youtube-fosdem-talks",
		{"tiktok", "@build/public"}: "jester-tiktok-build-public",
	}
	for in, want := range cases {
		if got := SeedFor(in[0], in[1]); got != want {
			t.Errorf("SeedFor(%q,%q)=%q want %q", in[0], in[1], got, want)
		}
	}
}

func TestQueryCarriesIdentityAndDropsGeoIPWithoutProxy(t *testing.T) {
	c := NewWithOptions("http://x:9222", Options{
		Fingerprint: "jester-reddit-selfhosted", Timezone: "America/New_York",
		Locale: "en-US", GeoIP: true,
	})
	q := c.query()
	for _, want := range []string{"fingerprint=jester-reddit-selfhosted", "timezone=America%2FNew_York", "locale=en-US"} {
		if !contains(q, want) {
			t.Errorf("query %q missing %q", q, want)
		}
	}
	// geoip only means anything with a proxy to derive from.
	if contains(q, "geoip") {
		t.Errorf("geoip must not be sent without a proxy: %q", q)
	}
	c.Opts.Proxy = "http://user:pw@proxy.example:8080"
	if q2 := c.query(); !contains(q2, "geoip=true") || !contains(q2, "proxy=") {
		t.Errorf("with a proxy both must be sent: %q", q2)
	}
}

func TestEmptyOptionsMeanTheSharedBrowser(t *testing.T) {
	if q := New("http://x:9222").query(); q != "" {
		t.Errorf("no identity should mean no query string, got %q", q)
	}
}

func contains(h, n string) bool {
	for i := 0; i+len(n) <= len(h); i++ {
		if h[i:i+len(n)] == n {
			return true
		}
	}
	return false
}
