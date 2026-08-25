// Package cloakbrowser wraps the CloakBrowser (cloakserve) CDP endpoint used
// for live, stealth Chromium ingestion (§35/§36). Phase B (§37.21) wires the
// real connection: resolve /json/version -> webSocketDebuggerUrl ->
// chromedp.NewRemoteAllocator. Free tier caps concurrent sessions at one.
package cloakbrowser

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

// Client represents a connection to a CloakBrowser CDP endpoint.
type Client struct {
	CDPURL string
	// Mock reports whether live fetch is disabled. When true, the worker uses
	// fixtures instead of opening a browser.
	Mock bool
}

// New constructs a Client. Mock is true when JESTER_MOCK=1.
func New(cdpURL string) *Client {
	return &Client{CDPURL: cdpURL, Mock: os.Getenv("JESTER_MOCK") == "1"}
}

// Session is a chromedp context bound to the remote stealth browser. Callers
// must invoke Cancel when done.
type Session struct {
	Ctx    context.Context
	Cancel context.CancelFunc
}

// wsURL resolves the browser's webSocketDebuggerUrl from the CDP HTTP probe.
func (c *Client) wsURL() (string, error) {
	url := strings.TrimSuffix(c.CDPURL, "/") + "/json/version"
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return "", fmt.Errorf("cdp probe %s: %w", url, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if err != nil {
		return "", fmt.Errorf("read cdp version: %w", err)
	}
	var v struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.Unmarshal(body, &v); err != nil {
		return "", fmt.Errorf("parse cdp version: %w", err)
	}
	if v.WebSocketDebuggerURL == "" {
		return "", fmt.Errorf("no webSocketDebuggerUrl in %s", url)
	}
	return v.WebSocketDebuggerURL, nil
}

// NewSession binds a chromedp context to the remote stealth browser.
func (c *Client) NewSession(ctx context.Context) (*Session, error) {
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
	return &Session{Ctx: browserCtx, Cancel: cancel}, nil
}
