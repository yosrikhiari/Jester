package siteprobe

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Every classification is exercised against a server that behaves the way the
// real thing does. Hermetic: no portal is contacted by `go test`.
func serve(t *testing.T, h http.HandlerFunc) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	return s
}

func probe(t *testing.T, s *httptest.Server) Result {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return Probe(ctx, s.Client(), "t", s.URL)
}

func TestTierJSON(t *testing.T) {
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Write([]byte(`{"listings":[{"id":1,"price":450000}]}`))
	})
	if got := probe(t, s); got.Tier != TierJSON {
		t.Fatalf("tier=%s want %s (%+v)", got.Tier, TierJSON, got)
	}
}

func TestTierHTMLAndEmbeddedJSON(t *testing.T) {
	body := `<html><body>` + longFiller + `
	  <script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
	  <img src="https://cdn.example-portal.com/photos/a1.jpg">
	  <img data-src="https://cdn.example-portal.com/photos/a2.webp">
	  <img src="https://img2.other-cdn.net/b.png">
	</body></html>`
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(body))
	})
	got := probe(t, s)
	if got.Tier != TierHTML {
		t.Fatalf("tier=%s want %s", got.Tier, TierHTML)
	}
	if !got.NextJSON {
		t.Error("an embedded JSON blob is the most useful thing the probe can find; it was missed")
	}
	// The image host decides whether photographs can bypass the browser.
	if len(got.ImageHosts) < 2 ||
		got.ImageHosts[0] != "cdn.example-portal.com" ||
		got.ImageHosts[1] != "img2.other-cdn.net" {
		t.Errorf("image hosts = %v, want the CDN hosts in first-seen order", got.ImageHosts)
	}
}

// Naming the vendor is the point: a Cloudflare challenge and a DataDome block
// call for different responses.
func TestNamedBotWalls(t *testing.T) {
	cases := []struct {
		name   string
		status int
		header [2]string
		body   string
		vendor string
	}{
		{"cloudflare header", 403, [2]string{"cf-mitigated", "challenge"}, "blocked", "Cloudflare"},
		{"cloudflare interstitial", 200, [2]string{"X-Y", "z"}, "<title>Just a moment...</title>" + longFiller, "Cloudflare challenge"},
		{"datadome", 403, [2]string{"X-DataDome", "protected"}, "blocked", "DataDome"},
		{"perimeterx", 200, [2]string{"X-Y", "z"}, "<div id='px-captcha'></div>" + longFiller, "PerimeterX/HUMAN"},
		{"kasada", 429, [2]string{"X-Y", "z"}, "kpsdk loaded", "Kasada"},
		{"incapsula", 200, [2]string{"X-Y", "z"}, "Incapsula incident id" + longFiller, "Imperva/Incapsula"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := serve(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set(c.header[0], c.header[1])
				w.Header().Set("Content-Type", "text/html")
				w.WriteHeader(c.status)
				w.Write([]byte(c.body))
			})
			got := probe(t, s)
			if got.Tier != TierWalled {
				t.Fatalf("tier=%s want %s", got.Tier, TierWalled)
			}
			if got.Wall != c.vendor {
				t.Errorf("wall=%q want %q — an unnamed wall tells the operator nothing actionable", got.Wall, c.vendor)
			}
		})
	}
}

// A 200 carrying nothing is a client-rendered shell. It must not be reported
// as HTML tier, because an adapter written against it would fetch a page with
// no listings in it and look merely unlucky.
func TestEmptyShellIsNotHTMLTier(t *testing.T) {
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(`<html><body><div id="root"></div></body></html>`))
	})
	got := probe(t, s)
	if got.Tier != TierWalled {
		t.Fatalf("tier=%s want %s (%s)", got.Tier, TierWalled, got.Note)
	}
	if got.Note == "" {
		t.Error("the shell case needs its note, or it reads as an ordinary block")
	}
}

func TestAuthWall(t *testing.T) {
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(`<html><body>Please log in to view this property` + longFiller + `</body></html>`))
	})
	if got := probe(t, s); got.Tier != TierAuth {
		t.Fatalf("tier=%s want %s", got.Tier, TierAuth)
	}
}

func TestDeadAndUnreachable(t *testing.T) {
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
		w.Write([]byte("not found"))
	})
	if got := probe(t, s); got.Tier != TierDead {
		t.Fatalf("404: tier=%s want %s", got.Tier, TierDead)
	}

	// A host that refuses the connection is dead, and must carry the error
	// rather than silently classifying as something reachable.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	got := Probe(ctx, &http.Client{Timeout: time.Second}, "x", "http://127.0.0.1:9/nothing")
	if got.Tier != TierDead || got.Err == nil {
		t.Fatalf("unreachable host: tier=%s err=%v, want dead with an error", got.Tier, got.Err)
	}
}

// The probe must not become the heavy thing it exists to avoid.
func TestBodyReadIsBounded(t *testing.T) {
	huge := make([]byte, 4*1024*1024)
	for i := range huge {
		huge[i] = 'a'
	}
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write(huge)
	})
	if got := probe(t, s); got.Bytes > 512*1024 {
		t.Fatalf("read %d bytes; the probe must stay a light touch", got.Bytes)
	}
}

// Go's stdlib cannot decode WebP, so the production fetcher asks for JPEG
// first. The probe has to send the same header or it measures a capability the
// real fetcher will not have.
func TestAcceptHeaderPrefersJPEGOverWebP(t *testing.T) {
	var accept string
	s := serve(t, func(w http.ResponseWriter, r *http.Request) {
		accept = r.Header.Get("Accept")
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte("<html>" + longFiller + "</html>"))
	})
	probe(t, s)
	if !contains(accept, "image/jpeg") {
		t.Errorf("Accept = %q, must advertise image/jpeg", accept)
	}
	if contains(accept, "image/webp") {
		t.Errorf("Accept = %q, must NOT advertise webp — stdlib cannot decode it", accept)
	}
}

func contains(s, sub string) bool { return len(s) >= len(sub) && (len(sub) == 0 || indexOf(s, sub) >= 0) }

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

// Bodies must clear the 2 KB "near-empty shell" floor to be judged on content.
var longFiller = func() string {
	b := make([]byte, 0, 3000)
	for len(b) < 3000 {
		b = append(b, []byte("<p>a property description paragraph. </p>")...)
	}
	return string(b)
}()
