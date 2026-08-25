// Package cloakbrowser wraps the CloakBrowser (cloakserve) CDP endpoint used
// for live, stealth Chromium ingestion (§35/§36). Phase B (§37.21) wires the
// real connection: resolve /json/version -> webSocketDebuggerUrl ->
// chromedp.NewRemoteAllocator.
//
// §4.3.2 identity: cloakserve is a CDP *multiplexer*. The `fingerprint` query
// param keys a separate stealth Chrome process — one stable canvas/WebGL/font
// identity per seed, with its own cookie jar, kept alive between runs by the
// container's idle timeout and its /profile volume. `timezone`, `locale`,
// `proxy` and `geoip` ride along on the same query string. Connecting without
// them lands every source in one shared, anonymous browser, which is the
// opposite of what the stealth story needs.
package cloakbrowser

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

// Options carry the per-connection identity cloakserve keys a browser off.
type Options struct {
	// Fingerprint is the seed. Empty means the shared default browser.
	Fingerprint string
	Timezone    string
	Locale      string
	Proxy       string
	// GeoIP asks cloakserve to derive timezone/locale/WebRTC IP from the
	// proxy's exit IP. It is ignored unless Proxy is also set.
	GeoIP bool
}

// Client represents a connection to a CloakBrowser CDP endpoint.
type Client struct {
	CDPURL string
	Opts   Options
	// Mock reports whether live fetch is disabled. When true, the worker uses
	// fixtures instead of opening a browser.
	Mock bool
}

// New constructs a Client against the shared default browser.
// Mock is true when JESTER_MOCK=1.
func New(cdpURL string) *Client {
	return &Client{CDPURL: cdpURL, Mock: os.Getenv("JESTER_MOCK") == "1"}
}

// NewWithOptions constructs a Client bound to one fingerprint identity.
func NewWithOptions(cdpURL string, opts Options) *Client {
	c := New(cdpURL)
	c.Opts = opts
	return c
}

// cloakserve rejects a seed it cannot use as a process key; keep it to the
// characters that survive a URL and a filename.
var (
	seedUnsafe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
	seedDashes = regexp.MustCompile(`-{2,}`)
)

// SeedFor builds a stable fingerprint seed for one curated source, so a
// subreddit or channel keeps the same identity — and the same warmed cookie
// jar — across runs.
func SeedFor(platform, name string) string {
	seed := seedUnsafe.ReplaceAllString(strings.ToLower(platform+"-"+name), "-")
	seed = strings.Trim(seedDashes.ReplaceAllString(seed, "-"), "-")
	if seed == "" {
		return "jester"
	}
	if len(seed) > 60 {
		seed = seed[:60]
	}
	return "jester-" + seed
}

// query renders the identity as cloakserve's connection query string.
func (c *Client) query() string {
	v := url.Values{}
	if c.Opts.Fingerprint != "" {
		v.Set("fingerprint", c.Opts.Fingerprint)
	}
	if c.Opts.Timezone != "" {
		v.Set("timezone", c.Opts.Timezone)
	}
	if c.Opts.Locale != "" {
		v.Set("locale", c.Opts.Locale)
	}
	if c.Opts.Proxy != "" {
		v.Set("proxy", c.Opts.Proxy)
		// geoip only means anything with a proxy to derive from.
		if c.Opts.GeoIP {
			v.Set("geoip", "true")
		}
	}
	if len(v) == 0 {
		return ""
	}
	return "?" + v.Encode()
}

// Session is a chromedp context bound to the remote stealth browser. Callers
// must invoke Cancel when done.
type Session struct {
	Ctx    context.Context
	Cancel context.CancelFunc
	// Seed is the fingerprint this session is pinned to ("" = shared default).
	Seed string
}

// wsURL resolves the browser's webSocketDebuggerUrl from the CDP HTTP probe.
// The identity query rides on this request: cloakserve spawns (or reuses) the
// Chrome process for that seed and answers with *its* websocket URL.
func (c *Client) wsURL() (string, error) {
	// Callers may pass a query on CDPURL; identity comes from Opts alone.
	probe := c.base() + "/json/version" + c.query()
	// A cold seed has to launch a Chrome process before it can answer.
	client := &http.Client{Timeout: 90 * time.Second}
	resp, err := client.Get(probe)
	if err != nil {
		return "", fmt.Errorf("cdp probe %s: %w", probe, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if err != nil {
		return "", fmt.Errorf("read cdp version: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("cdp probe %s: %s: %s", probe, resp.Status,
			strings.TrimSpace(string(body[:min(200, len(body))])))
	}
	var v struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.Unmarshal(body, &v); err != nil {
		return "", fmt.Errorf("parse cdp version: %w", err)
	}
	if v.WebSocketDebuggerURL == "" {
		return "", fmt.Errorf("no webSocketDebuggerUrl in %s", probe)
	}
	return v.WebSocketDebuggerURL, nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// base returns the CDP root with any path/query stripped.
func (c *Client) base() string {
	b := strings.TrimSuffix(strings.TrimSuffix(c.CDPURL, "/"), "/json/version")
	if i := strings.IndexByte(b, '?'); i >= 0 {
		b = b[:i]
	}
	return b
}

// procState is one running seed as cloakserve reports it on GET /.
type procState struct {
	Seed     string `json:"seed"`
	Proxy    string `json:"proxy"`
	Timezone string `json:"timezone"`
	Locale   string `json:"locale"`
}

// poolState reads cloakserve's process table.
func (c *Client) poolState() (map[string]procState, error) {
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Get(c.base() + "/")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	var v struct {
		Processes map[string]procState `json:"processes"`
	}
	if err := json.Unmarshal(body, &v); err != nil {
		return nil, err
	}
	return v.Processes, nil
}

// CloseSeed terminates one seed's browser, freeing its slot. The persistent
// profile is kept, so reopening the seed restores its identity (and its warmed
// cookies). Idempotent.
func (c *Client) CloseSeed(seed string) error {
	if seed == "" {
		return nil
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Post(c.base()+"/fingerprint/"+url.PathEscape(seed)+"/close",
		"application/json", strings.NewReader("{}"))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

// recycleIfStale closes the seed when it is already running under a different
// identity than the one configured now.
//
// cloakserve fixes a seed's proxy/timezone/locale at FIRST launch and ignores
// the params on later connections. Without this, editing scraper.yaml appears
// to do nothing until the container is restarted — and a browser launched once
// with a bad proxy stays broken for the rest of the container's life.
func (c *Client) recycleIfStale() {
	if c.Opts.Fingerprint == "" {
		return
	}
	procs, err := c.poolState()
	if err != nil {
		return // best effort: never block a fetch on the status endpoint
	}
	running, ok := procs[c.Opts.Fingerprint]
	if !ok {
		return // cold seed: it will launch with the current identity
	}
	same := func(want, got string) bool {
		// cloakserve reports an unset value as null -> "".
		return want == got || (want == "" && got == "")
	}
	if same(c.Opts.Proxy, running.Proxy) &&
		same(c.Opts.Timezone, running.Timezone) &&
		same(c.Opts.Locale, running.Locale) {
		return
	}
	_ = c.CloseSeed(c.Opts.Fingerprint)
}

// NewSession binds a chromedp context to the remote stealth browser.
func (c *Client) NewSession(ctx context.Context) (*Session, error) {
	c.recycleIfStale()
	ws, err := c.wsURL()
	if err != nil {
		return nil, err
	}
	allocCtx, allocCancel := chromedp.NewRemoteAllocator(ctx, ws)
	browserCtx, browserCancel := chromedp.NewContext(allocCtx)
	// Force the connection now so failures surface here, not mid-action.
	if err := chromedp.Run(browserCtx); err != nil {
		allocCancel()
		browserCancel()
		return nil, fmt.Errorf("connect over cdp: %w", err)
	}
	cancel := func() {
		browserCancel()
		allocCancel()
	}
	return &Session{Ctx: browserCtx, Cancel: cancel, Seed: c.Opts.Fingerprint}, nil
}
