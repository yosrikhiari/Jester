// Live fetch path (§37.21 Phase B): calibrated shreddit navigation over a
// cloakserve session. Mirrors python/jester/fetchers/cloak.py — seconds-level
// pacing (§36 #7), D-19 blocked_response_action, SSR DOM extraction.
package reddit

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

// ErrBlocked is the D-19 signal, mirroring Python's BlockedResponse.
type ErrBlocked struct {
	URL    string
	Action string
}

func (e *ErrBlocked) Error() string {
	return fmt.Sprintf("blocked response at %s (action=%s)", e.URL, e.Action)
}

// Interstitials seen live: Reddit answers a flagged fingerprint with a
// "Prove your humanity" page and Cloudflare with "Just a moment". Without these
// a block reads as "no posts found", which sends the operator hunting a
// selector bug that is not there.
var challengeRE = regexp.MustCompile(`(?i)captcha|challenge|access denied|blocked|unusual traffic|prove your humanity|verify you are human|just a moment|are you a robot|rate limit`)

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

// Challenged reports whether the currently loaded page is an interstitial
// rather than content.
func Challenged(ctx context.Context) bool {
	title, _ := evalString(ctx, `() => document.title`)
	if challengeRE.MatchString(title) {
		return true
	}
	text, _ := evalString(ctx, `() => document.body.innerText.slice(0, 1000)`)
	return challengeRE.MatchString(text)
}

// WarmUp absorbs the first-request challenge on a cold fingerprint.
//
// Calibrated live through cloakserve: Reddit answers the FIRST navigation from
// an unseen session with "Prove your humanity" regardless of which URL is
// requested, and serves real content from the second navigation onward. Dwell
// time on the challenge page does NOT help — only a second navigation does.
// Because cloakserve keys one Chrome process (and cookie jar) per fingerprint
// seed, this is a one-time cost per source, not per run.
//
// `attempts` comes from scraper.yaml's `warmup_navigations`. Zero disables the
// warm-up outright, which the config has always documented and the code never
// honoured — the value was parsed into Scraper.WarmupNavigations and then read
// by nobody, while the two call sites below passed a hardcoded 2. Turning the
// knob did nothing in either direction.
//
// Returns true when the session is clear, false when it is still challenged
// after `attempts` navigations (and when attempts is 0, i.e. never warmed).
func WarmUp(ctx context.Context, url string, delay time.Duration, attempts int) bool {
	if attempts < 1 {
		return false
	}
	for i := 0; i < attempts; i++ {
		if err := chromedp.Run(ctx,
			chromedp.Navigate(url),
			chromedp.Sleep(Pace(delay)),
		); err != nil {
			return false
		}
		if !Challenged(ctx) {
			return true
		}
	}
	return !Challenged(ctx)
}

// FetchThread loads a listing URL, discovers the first post permalink,
// navigates to it and extracts comments from the server-rendered DOM.
// Returns the comments, the resolved thread URL and thread id.
//
// blockedAction is one of pass_through | backoff | fail (config/scraper.yaml).
func FetchThread(ctx context.Context, listingURL string, delay time.Duration,
	threadIDFrom func(permalink string) string, blockedAction string, warmup int,
) ([]FetchedComment, string, string, error) {
	// FetchThread wants the single newest thread whatever its size, so no
	// comment floor is applied here — the caller asked for one thread, not
	// for one BUSY thread.
	threads, err := ListThreads(ctx, listingURL, delay, 1, warmup, 0)
	if err != nil {
		return nil, "", "", err
	}
	threadURL := threads[0]
	comments, _, err := FetchThreadURL(ctx, threadURL, delay, blockedAction, warmup)
	if err != nil {
		return nil, "", "", err
	}
	return comments, threadURL, threadIDFrom(threadURL), nil
}

// ListThreads loads a subreddit listing and returns up to `limit` absolute
// thread URLs in feed order.
// listingRow is one post as the listing advertises it: where it is, and how
// much discussion it claims to hold.
type listingRow struct {
	Permalink string `json:"permalink"`
	Comments  string `json:"comments"`
	// Fullname is the post's `t3_xxxxx` id, which is the cursor `?after=`
	// takes. It comes off the element's own id attribute.
	Fullname string `json:"fullname"`
}

// listingPageURL is the listing with a pagination cursor applied.
//
// MEASURED, not assumed. This plan said Reddit's listing renders "roughly 25
// posts" and that the adapter merely failed to scroll. Neither half held up: a
// paint renders exactly THREE posts, scrolling adds none however tall the
// viewport (verified at 1440x2400, where the document is shorter than the
// window), and old.reddit.com answers this session with a "Welcome to Reddit"
// interstitial. The number was invisible because max_threads_per_platform.reddit
// is also 3 — the adapter looked like it was honouring its depth setting while
// actually being pinned at the platform's floor, and raising the knob would
// have changed nothing.
//
// What does work is `?after=`, which is server-side and does not depend on the
// client rendering more of a virtualised feed: three posts a page, no overlap,
// twelve unique across four pages.
func listingPageURL(base, after string, seen int) string {
	if after == "" {
		return base
	}
	sep := "?"
	if strings.Contains(base, "?") {
		sep = "&"
	}
	// `count` is how many the caller has already been shown. Reddit uses it
	// for the "prev" link and for consistent slicing; omitting it works but
	// leaving it accurate costs nothing.
	return fmt.Sprintf("%s%safter=%s&count=%d", base, sep, url.QueryEscape(after), seen)
}

// ListThreads returns up to `limit` thread URLs from a subreddit listing,
// skipping threads whose advertised comment count is below `minComments`.
//
// Skipping BEFORE the fetch is the whole value: a thread with 0 comments costs
// a full browser navigation, a slot in the run's post budget, and yields
// nothing. The count is right there in the listing markup.
func ListThreads(ctx context.Context, listingURL string, delay time.Duration,
	limit, warmup, minComments int) ([]string, error) {
	if limit < 1 {
		limit = 1
	}
	var permalinks []listingRow
	if err := chromedp.Run(ctx,
		chromedp.Navigate(listingURL),
		chromedp.Sleep(Pace(delay)),
		waitPresent(`shreddit-post[permalink]`),
		chromedp.ActionFunc(func(actx context.Context) error {
			return evalInto(actx, PERMALINKS_JS, &permalinks)
		}),
	); err != nil {
		return nil, fmt.Errorf("navigate listing: %w", err)
	}
	// A cold fingerprint burns its first navigation on the challenge; one more
	// clears it. Re-read the listing rather than reporting an empty feed.
	if len(permalinks) == 0 && Challenged(ctx) {
		if WarmUp(ctx, listingURL, delay, warmup) {
			if err := chromedp.Run(ctx,
				waitPresent(`shreddit-post[permalink]`),
				chromedp.ActionFunc(func(actx context.Context) error {
					return evalInto(actx, PERMALINKS_JS, &permalinks)
				}),
			); err != nil {
				return nil, fmt.Errorf("re-read listing after warm-up: %w", err)
			}
		}
	}
	out := make([]string, 0, limit)
	seen := map[string]bool{}
	quiet := 0
	// collect drains one rendered page into `out`, and reports the cursor to
	// page from next. Returns false once `limit` is met.
	collect := func(rows []listingRow) (after string, room bool) {
		for _, row := range rows {
			if row.Fullname != "" {
				after = row.Fullname
			}
			p := row.Permalink
			if p == "" {
				continue
			}
			// An ABSENT count is not a zero: only a published number may
			// exclude a thread. Reddit renders comment-count on every post,
			// but a layout change that dropped it must degrade to fetching
			// everything rather than silently skipping the whole feed.
			if minComments > 0 && row.Comments != "" {
				if n, err := strconv.Atoi(strings.TrimSpace(row.Comments)); err == nil && n < minComments {
					quiet++
					continue
				}
			}
			if strings.HasPrefix(p, "/") {
				p = "https://www.reddit.com" + p
			}
			if seen[p] {
				continue
			}
			seen[p] = true
			out = append(out, p)
			if len(out) == limit {
				return after, false
			}
		}
		return after, true
	}

	after, room := collect(permalinks)
	// PAGINATE. One paint gives three posts, so a depth setting above three
	// used to be silently unreachable — the knob said 10 and the run got 3
	// with nothing anywhere saying so. Each page is a full navigation through
	// the stealth browser, which is the expensive, fingerprint-visible path,
	// so the walk stops the moment it has enough and never speculates ahead.
	//
	// maxListingPages bounds the damage if Reddit ever serves a cursor that
	// does not advance: without it a stationary `after` is an infinite loop of
	// navigations against a bot detector.
	const maxListingPages = 12
	for page := 1; room && len(out) < limit && after != "" && page < maxListingPages; page++ {
		var next []listingRow
		if err := chromedp.Run(ctx,
			chromedp.Navigate(listingPageURL(listingURL, after, len(seen)+quiet)),
			chromedp.Sleep(Pace(delay)),
			waitPresent(`shreddit-post[permalink]`),
			chromedp.ActionFunc(func(actx context.Context) error {
				return evalInto(actx, PERMALINKS_JS, &next)
			}),
		); err != nil {
			// A page that will not load is the end of the walk, not a failed
			// run: whatever was already collected is real and usable.
			fmt.Printf("[live]   listing page %d unavailable, stopping: %v\n", page+1, err)
			break
		}
		before := len(out)
		nextAfter, more := collect(next)
		room = more
		if len(out) == before || nextAfter == after || nextAfter == "" {
			// Nothing new, or a cursor that did not move. Either way the feed
			// is exhausted for this session.
			break
		}
		after = nextAfter
	}
	if len(out) < limit {
		// R55. "Asked for 10, got 3" is a fact about the platform, and it was
		// invisible for as long as the configured depth happened to equal the
		// number Reddit hands over.
		fmt.Printf("[live]   listing offered %d of %d thread(s) asked for\n", len(out), limit)
	}
	if quiet > 0 {
		// R55: a skipped thread is reported, never silent. "0 comments queued"
		// and "we declined to fetch 12 empty threads" are different runs.
		fmt.Printf("[live]   skipped %d thread(s) advertising fewer than %d comment(s)\n",
			quiet, minComments)
	}
	if len(out) == 0 && quiet > 0 {
		// Every thread was too quiet. That is a real, benign outcome and must
		// not be reported as a block — they call for opposite responses.
		return nil, fmt.Errorf(
			"no thread at %s has %d+ comments (%d skipped as too quiet)",
			listingURL, minComments, quiet)
	}
	if len(out) == 0 {
		title, _ := evalString(ctx, `() => document.title`)
		posts, _ := evalInt(ctx, `() => document.querySelectorAll('shreddit-post').length`)
		href, _ := evalString(ctx, `() => location.href`)
		// A challenge page has no posts either. Say "blocked", not "selector
		// found nothing" — they call for completely different responses.
		pageText, _ := evalString(ctx, `() => document.body.innerText.slice(0, 1000)`)
		if challengeRE.MatchString(title) || challengeRE.MatchString(pageText) {
			return nil, &ErrBlocked{URL: listingURL, Action: "listing"}
		}
		return nil, fmt.Errorf("no shreddit-post[permalink] on %s; href=%q title=%q posts=%d",
			listingURL, href, title, posts)
	}
	return out, nil
}

// FetchThreadURL extracts comments from one already-resolved thread URL.
// This is the path a `kind: thread` source takes — no listing walk at all.
func FetchThreadURL(ctx context.Context, threadURL string, delay time.Duration,
	blockedAction string, warmup int,
) ([]FetchedComment, *FetchedPost, error) {
	var raw []map[string]any
	if err := chromedp.Run(ctx,
		chromedp.Navigate(threadURL),
		chromedp.Sleep(Pace(delay)),
		waitPresent(`shreddit-comment`),
		chromedp.ActionFunc(func(actx context.Context) error {
			return evalInto(actx, DOMJS, &raw)
		}),
	); err != nil {
		return nil, nil, fmt.Errorf("navigate thread: %w", err)
	}

	// A cold fingerprint burns its first navigation on the challenge (see
	// WarmUp); retry once before treating this as a real block.
	if len(raw) == 0 && Challenged(ctx) && WarmUp(ctx, threadURL, delay, warmup) {
		if err := chromedp.Run(ctx,
			waitPresent(`shreddit-comment`),
			chromedp.ActionFunc(func(actx context.Context) error {
				return evalInto(actx, DOMJS, &raw)
			}),
		); err != nil {
			return nil, nil, fmt.Errorf("re-read thread after warm-up: %w", err)
		}
	}

	// D-19 block detection on the rendered page text.
	pageText, _ := evalString(ctx, `() => document.body.innerText.slice(0, 1000)`)
	title, _ := evalString(ctx, `() => document.title`)
	if challengeRE.MatchString(pageText) || challengeRE.MatchString(title) {
		switch blockedAction {
		case "fail":
			return nil, nil, &ErrBlocked{URL: threadURL, Action: "fail"}
		case "backoff":
			select {
			case <-time.After(backoffSeconds * time.Second):
			case <-ctx.Done():
				return nil, nil, ctx.Err()
			}
			return nil, nil, &ErrBlocked{URL: threadURL, Action: "backoff"}
		default: // pass_through
			return nil, nil, nil
		}
	}

	// The post's own metadata is on the page we already loaded — title,
	// author, score, comment count and the upvote ratio. Reading it here costs
	// one extra evaluate against a document that is already in memory, and it
	// is the only chance to capture it: nothing downstream can recover what
	// the thread was about from a bare id.
	var postRow map[string]any
	post := (*FetchedPost)(nil)
	if err := evalInto(ctx, POST_META_JS, &postRow); err == nil {
		post = PostFromNode(postRow)
	}
	if post != nil && post.URL == "" {
		post.URL = threadURL
	}

	// An empty comment list means an empty/vanished thread — not an error.
	return CommentsFromNodes(raw), post, nil
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
