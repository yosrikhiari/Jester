package realestate

import (
	"context"
	"fmt"
	"time"

	"github.com/chromedp/chromedp"
)

// FetchRenderedPage navigates to url via cloakserve (chromedp over the
// caller's session context), waits for the body to be visible, and returns
// the full rendered <html> outer so that siteprofile.Parse can find
// __NEXT_DATA__ and ldjson nodes.
func FetchRenderedPage(ctx context.Context, url string, delay time.Duration) (string, error) {
	var html string
	err := chromedp.Run(ctx,
		chromedp.Navigate(url),
		chromedp.Sleep(Pace(delay)),
		chromedp.WaitVisible(`body`, chromedp.ByQuery),
		chromedp.OuterHTML(`html`, &html, chromedp.ByQuery),
	)
	if err != nil {
		return "", fmt.Errorf("navigate %s: %w", url, err)
	}
	if html == "" {
		return "", fmt.Errorf("empty rendered html for %s", url)
	}
	return html, nil
}
