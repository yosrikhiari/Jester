// Live fetch path (§37.26): calibrated YouTube navigation over cloakserve.
// Comments lazy-load after scrolling; extraction is DOM-primary because
// cloakserve's CDP returns empty bodies for /youtubei/v1/next via
// GetResponseBody (verified live — see §37.26 findings).
package youtube

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/chromedp"
	"github.com/chromedp/cdproto/input"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
)

const backoffSeconds = 5.0

// evalRaw runs Runtime.evaluate with returnByValue, self-invoking arrow
// functions, and hands back the RAW result payload. Ported from §37.21:
// chromedp's typed decode mis-handles cloakserve responses.
func evalRaw(ctx context.Context, expr string) (json.RawMessage, error) {
	trimmed := strings.TrimSpace(expr)
	if strings.HasPrefix(trimmed, "() =>") || strings.HasPrefix(trimmed, "()=>") ||
		strings.HasPrefix(trimmed, "function") || strings.HasPrefix(trimmed, "(function") {
		expr = "(" + trimmed + ")()"
	}
	var out json.RawMessage
	err := chromedp.Run(ctx, chromedp.ActionFunc(func(actx context.Context) error {
		ro, exc, err := runtime.Evaluate(expr).WithReturnByValue(true).Do(actx)
		if err != nil {
			return err
		}
		if exc != nil && exc.Text != "" {
			return fmt.Errorf("js exception: %s", exc.Text)
		}
		out = json.RawMessage(string(ro.Value))
		return nil
	}))
	return out, err
}

// evalString is a thin typed helper over evalRaw for page-state probes.
func evalString(ctx context.Context, expr string) (string, error) {
	raw, err := evalRaw(ctx, expr)
	if err != nil {
		return "", err
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return "", err
	}
	return s, nil
}

// BlockedError is the D-19 signal for YouTube sources.
type BlockedError struct {
	URL    string
	Action string
}

func (e *BlockedError) Error() string {
	return fmt.Sprintf("blocked response at %s (action=%s)", e.URL, e.Action)
}

// isBlockRelevant scopes D-19 to YouTube-origin responses. Media CDN segments
// (googlevideo.com videoplayback) 403 routinely during normal playback —
// treating them as blocks false-fails every run.
func isBlockRelevant(url string) bool {
	return strings.Contains(url, "youtube.com") && !strings.Contains(url, "googlevideo.com")
}

// DOMJS mirrors python's comments_from_dom fallback: hydrated comment threads
// carry author in #author-text and markdown body in #content-text.
const DOMJS = `
() => [...document.querySelectorAll('ytd-comment-thread-renderer')].map(n => ({
  author: ((n.querySelector('#author-text') || {}).innerText || '').trim(),
  body: ((n.querySelector('#content-text') || {}).innerText || '').trim(),
})).filter(c => c.body)
`

// VIDEO_HREF_JS discovers the first video link on a channel /videos tab.
const VIDEO_HREF_JS = `() => { const a = document.querySelector('ytd-video-renderer a#video-title'); return a ? a.getAttribute('href') : null; }`

// VIDEO_HREFS_JS discovers every video link on a channel /videos tab, in grid
// order.
//
// Calibrated live through cloakserve (probe run, this milestone): the grid
// renders ytd-rich-item-renderer wrapping yt-lockup-view-model, and the legacy
// `a#video-title-link` / `a#video-title` ids are GONE (0 matches on a live
// channel). Those ids are kept first for older layouts, then the query falls
// through to any /watch?v= anchor in the grid. Duplicates are expected — each
// item links twice (thumbnail + title) — and the caller dedupes by video id.
const VIDEO_HREFS_JS = `() => {
  const pick = sel => [...document.querySelectorAll(sel)]
      .map(a => a.getAttribute('href'))
      .filter(h => h && h.includes('/watch?v='));
  const specific = pick([
    'ytd-rich-item-renderer a#video-title-link',
    'ytd-rich-grid-media a#video-title-link',
    'ytd-video-renderer a#video-title',
    'ytd-rich-item-renderer a[href*="/watch?v="]',
    'yt-lockup-view-model a[href*="/watch?v="]',
  ].join(', '));
  return specific.length ? specific : pick('a[href*="/watch?v="]');
}`

// ChannelVideosURL turns a channel URL into its /videos tab, tolerating a URL
// that already points there.
func ChannelVideosURL(channelURL string) string {
	trimmed := strings.TrimRight(channelURL, "/")
	if strings.HasSuffix(trimmed, "/videos") {
		return trimmed
	}
	return trimmed + "/videos"
}

// ListChannelVideos loads a channel's /videos tab and returns up to `limit`
// absolute watch URLs.
//
// Without this step a `kind: channel` source scraped the listing page itself,
// which has no comments — the channel's videos were never reached.
func ListChannelVideos(ctx context.Context, channelURL string, delay time.Duration, limit int) ([]string, error) {
	if limit < 1 {
		limit = 1
	}
	listing := ChannelVideosURL(channelURL)
	if err := chromedp.Run(ctx,
		chromedp.Navigate(listing),
		chromedp.Sleep(delay),
		// The grid is lazy: it only mounts in a visible tab, after a gesture.
		chromedp.ActionFunc(func(actx context.Context) error {
			return page.BringToFront().Do(actx)
		}),
		chromedp.ActionFunc(func(actx context.Context) error {
			return input.DispatchMouseEvent(input.MouseWheel, 640, 400).WithDeltaY(1200).Do(actx)
		}),
		chromedp.Sleep(2*time.Second),
	); err != nil {
		return nil, fmt.Errorf("navigate channel: %w", err)
	}
	// A dead handle renders a 404 page with an empty grid — name that, rather
	// than reporting it as "no videos" and sending the operator after selectors.
	if title, err := evalString(ctx, `() => document.title`); err == nil {
		if strings.Contains(title, "404") || challengeRE.MatchString(title) {
			return nil, fmt.Errorf("channel page unusable at %s (title %q)", listing, title)
		}
	}
	raw, err := evalRaw(ctx, VIDEO_HREFS_JS)
	if err != nil {
		return nil, fmt.Errorf("channel extract: %w", err)
	}
	var hrefs []string
	if err := json.Unmarshal(raw, &hrefs); err != nil {
		return nil, fmt.Errorf("channel decode: %w", err)
	}
	out := make([]string, 0, limit)
	seen := map[string]bool{}
	for _, h := range hrefs {
		id := VideoIDFromURL(h)
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		out = append(out, "https://www.youtube.com/watch?v="+id)
		if len(out) == limit {
			break
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no videos found on %s", listing)
	}
	return out, nil
}

// VideoIDFromURL extracts the v= parameter or /shorts/ path segment.
func VideoIDFromURL(videoURL string) string {
	marker := "v="
	if i := strings.Index(videoURL, marker); i >= 0 {
		rest := videoURL[i+len(marker):]
		if j := strings.IndexAny(rest, "&?#"); j >= 0 {
			return rest[:j]
		}
		return rest
	}
	parts := strings.Split(strings.Trim(videoURL, "/"), "/")
	for i, p := range parts {
		if p == "shorts" && i+1 < len(parts) {
			return parts[i+1]
		}
	}
	return ""
}

type capturedResponse struct {
	status int64
	url    string
}

// FetchVideoComments navigates a video page, scrolls with real wheel events to
// trigger lazy comment mounting, then extracts threads from the hydrated DOM.
// D-19: block-relevant 403/429 responses honor blocked_action; media-CDN noise
// (googlevideo.com) is ignored.
// The caller owns the session: §4.3.2 pins one stealth identity (and cookie
// jar) per source, and opening a second, unseeded session here would quietly
// fetch through the shared anonymous browser instead.
func FetchVideoComments(
	ctx context.Context,
	videoURL string,
	delay time.Duration,
	scrolls int,
	blockedAction string,
) ([]FetchedComment, error) {

	var mu sync.Mutex
	var captured []capturedResponse
	chromedp.ListenTarget(ctx, func(ev any) {
		if resp, ok := ev.(*network.EventResponseReceived); ok {
			mu.Lock()
			captured = append(captured, capturedResponse{
				status: resp.Response.Status, url: resp.Response.URL,
			})
			mu.Unlock()
		}
	})

	tasks := []chromedp.Action{
		chromedp.Navigate(videoURL),
		chromedp.Sleep(delay),
		// Lazy-loaded modules only mount in a visible, foreground tab.
		chromedp.ActionFunc(func(actx context.Context) error {
			return page.BringToFront().Do(actx)
		}),
	}
	for i := 0; i < scrolls; i++ {
		tasks = append(tasks,
			// Real wheel events (not window.scrollBy): YouTube mounts the
			// comments surface on user gestures.
			chromedp.ActionFunc(func(actx context.Context) error {
				return input.DispatchMouseEvent(input.MouseWheel, 640, 400).
					WithDeltaY(1600).Do(actx)
			}),
			chromedp.Sleep(time.Second),
		)
	}
	if err := chromedp.Run(ctx, tasks...); err != nil {
		return nil, fmt.Errorf("navigate video: %w", err)
	}

	blocks := 0
	mu.Lock()
	for _, c := range captured {
		if isBlockRelevant(c.url) && (c.status == 403 || c.status == 429) {
			blocks++
		}
	}
	mu.Unlock()
	if blocks > 0 && blockedAction != "pass_through" {
		action := blockedAction
		if action != "fail" && action != "backoff" {
			action = "pass_through"
		}
		if action == "fail" || action == "backoff" {
			return nil, &BlockedError{URL: videoURL, Action: action}
		}
	}

	// PRIMARY: hydrated comment threads straight from the live DOM.
	raw, err := evalRaw(ctx, DOMJS)
	if err != nil {
		return nil, fmt.Errorf("dom extract: %w", err)
	}
	var rows []map[string]any
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, fmt.Errorf("dom decode: %w", err)
	}
	comments := make([]FetchedComment, 0, len(rows))
	for _, r := range rows {
		author, _ := r["author"].(string)
		bodyTxt, _ := r["body"].(string)
		bodyTxt = strings.TrimSpace(bodyTxt)
		if bodyTxt == "" {
			continue
		}
		comments = append(comments, FetchedComment{ID: author + bodyTxt, Body: bodyTxt})
	}

	if len(comments) == 0 {
		return nil, fmt.Errorf("no comments found on %s (domRows=%d)", videoURL, len(rows))
	}
	return comments, nil
}
