// Live fetch path (§37.21 Phase B): calibrated shreddit navigation over a
// cloakserve session. Mirrors python/jester/fetchers/cloak.py — seconds-level
// pacing (§36 #7), D-19 blocked_response_action, SSR DOM extraction.
package reddit

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
	"github.com/chromedp/cdproto/runtime"
)

// ErrBlocked is the D-19 signal, mirroring Python's BlockedResponse.
type ErrBlocked struct {
	URL    string
	Action string
}

func (e *ErrBlocked) Error() string {
	return fmt.Sprintf("blocked response at %s (action=%s)", e.URL, e.Action)
}

var challengeRE = regexp.MustCompile(`(?i)captcha|challenge|access denied|blocked|unusual traffic`)

const backoffSeconds = 5.0

// evalRaw runs Runtime.evaluate with returnByValue and hands back the RAW
// result payload. Two quirks handled here: (1) chromedp.Evaluate mis-decodes
// against cloakserve, so we own decoding; (2) raw Runtime.evaluate does NOT
// call arrow-function expressions the way Playwright does — self-invoking
// wrapper added when the expression looks like a function.
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

// evalInto decodes an evaluate result into out, tolerating both direct values
// and a {"value": …} wrapper shape.
func evalInto(ctx context.Context, expr string, out any) error {
	raw, err := evalRaw(ctx, expr)
	if err != nil {
		return err
	}
	if len(raw) == 0 || string(raw) == "{}" {
		return fmt.Errorf("empty evaluate result for %s", expr[:min(60, len(expr))])
	}
	if err := json.Unmarshal(raw, out); err == nil {
		return nil
	}
	var wrapper struct {
		Value json.RawMessage `json:"value"`
	}
	if werr := json.Unmarshal(raw, &wrapper); werr == nil && len(wrapper.Value) > 0 {
		return json.Unmarshal(wrapper.Value, out)
	}
	return fmt.Errorf("cannot decode %s for %s", string(raw), expr[:min(40, len(expr))])
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// evalString / evalInt are thin typed helpers over evalInto.
func evalString(ctx context.Context, expr string) (string, error) {
	var s string
	err := evalInto(ctx, expr, &s)
	return s, err
}

func evalInt(ctx context.Context, expr string) (int, error) {
	var n int
	err := evalInto(ctx, expr, &n)
	return n, err
}

// waitPresent tolerates absence (empty/vanished threads, lazy hydration):
// a timeout is NOT an error — the subsequent Evaluate decides.
func waitPresent(sel string) chromedp.Action {
	return chromedp.ActionFunc(func(ctx context.Context) error {
		actionCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		_ = chromedp.WaitVisible(sel, chromedp.ByQueryAll).Do(actionCtx)
		return nil
	})
}

// FetchThread loads a listing URL, discovers the first post permalink,
// navigates to it and extracts comments from the server-rendered DOM.
// Returns the comments, the resolved thread URL and thread id.
//
// blockedAction is one of pass_through | backoff | fail (config/scraper.yaml).
func FetchThread(ctx context.Context, listingURL string, delay time.Duration,
	threadIDFrom func(permalink string) string, blockedAction string,
) ([]FetchedComment, string, string, error) {
	var permalink any
	if err := chromedp.Run(ctx,
		chromedp.Navigate(listingURL),
		chromedp.Sleep(delay),
		waitPresent(`shreddit-post[permalink]`),
		chromedp.ActionFunc(func(actx context.Context) error {
			return evalInto(actx, PERMALINK_JS, &permalink)
		}),
	); err != nil {
		return nil, "", "", fmt.Errorf("navigate listing: %w", err)
	}
	permalinkStr, _ := permalink.(string)
	if permalinkStr == "" {
		title, _ := evalString(ctx, `() => document.title`)
		posts, _ := evalInt(ctx, `() => document.querySelectorAll('shreddit-post').length`)
		href, _ := evalString(ctx, `() => location.href`)
		return nil, "", "", fmt.Errorf("no shreddit-post[permalink] on %s; href=%q title=%q posts=%d", listingURL, href, title, posts)
	}
	threadURL := permalinkStr
	if strings.HasPrefix(threadURL, "/") {
		threadURL = "https://www.reddit.com" + threadURL
	}
	threadID := threadIDFrom(threadURL)

	var raw []map[string]any
	if err := chromedp.Run(ctx,
		chromedp.Navigate(threadURL),
		chromedp.Sleep(delay),
		waitPresent(`shreddit-comment`),
		chromedp.ActionFunc(func(actx context.Context) error {
			return evalInto(actx, DOMJS, &raw)
		}),
	); err != nil {
		return nil, "", "", fmt.Errorf("navigate thread: %w", err)
	}

	// D-19 block detection on the rendered page text.
	pageText, _ := evalString(ctx, `() => document.body.innerText.slice(0, 1000)`)
	title, _ := evalString(ctx, `() => document.title`)
	if challengeRE.MatchString(pageText) || challengeRE.MatchString(title) {
		switch blockedAction {
		case "fail":
			return nil, "", "", &ErrBlocked{URL: threadURL, Action: "fail"}
		case "backoff":
			select {
			case <-time.After(backoffSeconds * time.Second):
			case <-ctx.Done():
				return nil, "", "", ctx.Err()
			}
			return nil, "", "", &ErrBlocked{URL: threadURL, Action: "backoff"}
		default: // pass_through
			return nil, threadURL, threadID, nil
		}
	}

	comments := CommentsFromNodes(raw)
	if len(comments) == 0 {
		// SSR stub fallback already covered by DOM; nothing here means an
		// empty/vanished thread — not an error.
		return nil, threadURL, threadID, nil
	}
	return comments, threadURL, threadID, nil
}

// ThreadIDFromPath pulls the base-36 post id out of a reddit permalink
// (/r/sub/comments/<id>/<slug>/...).
func ThreadIDFromPath(permalink string) string {
	parts := strings.Split(strings.Trim(permalink, "/"), "/")
	for i, p := range parts {
		if p == "comments" && i+1 < len(parts) {
			return parts[i+1]
		}
	}
	return ""
}
