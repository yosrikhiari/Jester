package realestate

import (
	"context"
	"fmt"
	"time"

	"github.com/chromedp/chromedp"
)

// scrollSettle is how long a lazy list is given to answer one scroll before
// the growth check runs. Short, because the check repeats: an unnecessary
// wait is paid once per round and there are at most Scroll rounds.
const scrollSettle = 1200 * time.Millisecond

// FetchRenderedPage navigates to url via cloakserve (chromedp over the
// caller's session context), waits for the body to be visible, and returns
// the full rendered <html> outer so that siteprofile.Parse can find
// __NEXT_DATA__ and ldjson nodes.
//
// scrolls > 0 treats the page as a LAZY LIST: it is scrolled to the bottom up
// to that many times and read only once the DOM stops growing.
//
// THE SCRAPE PATH PASSES 0 and no shipped profile asks for more, because no
// portal harvested so far lazy-loads. It exists for renderprobe, where it is
// how a client-rendered portal gets told apart from a gated one: a page that
// grows under scrolling was lazy, and a page that does not - Fotocasa holds
// 19 byte-identical empty card skeletons however long it is scrolled - is
// withholding its list for some other reason. That distinction decides
// whether a profile can be written at all, and guessing it wrong produces a
// profile that parses one row per page and reports success.
func FetchRenderedPage(ctx context.Context, url string, delay time.Duration, scrolls int) (string, error) {
	var html string
	err := chromedp.Run(ctx,
		chromedp.Navigate(url),
		chromedp.Sleep(Pace(delay)),
		chromedp.WaitVisible(`body`, chromedp.ByQuery),
	)
	if err != nil {
		return "", fmt.Errorf("navigate %s: %w", url, err)
	}

	// Growth is measured in DOM length rather than a card count because the
	// selector that identifies a card is the profile's business, not this
	// function's, and every portal spells it differently.
	prev := -1
	for i := 0; i < scrolls; i++ {
		var size int
		err := chromedp.Run(ctx,
			chromedp.Evaluate(`window.scrollTo(0, document.body.scrollHeight)`, nil),
			chromedp.Sleep(scrollSettle),
			chromedp.Evaluate(`document.documentElement.outerHTML.length`, &size),
		)
		if err != nil {
			// A scroll that fails is not a fetch that fails: whatever has
			// already rendered is still worth parsing, and the caller finds
			// out from the row count rather than from an error.
			break
		}
		if size <= prev {
			break
		}
		prev = size
	}

	if err := chromedp.Run(ctx,
		chromedp.OuterHTML(`html`, &html, chromedp.ByQuery),
	); err != nil {
		return "", fmt.Errorf("read %s: %w", url, err)
	}
	if html == "" {
		return "", fmt.Errorf("empty rendered html for %s", url)
	}
	return html, nil
}
