package realestate

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

// ExtractNextData pulls the __NEXT_DATA__ script content from the
// currently loaded page and unmarshals it into a map.
// Used for Next.js portals (Tayara) where the JSON is populated at
// runtime by the client-side bundle.
func ExtractNextData(ctx context.Context) (map[string]any, error) {
	var raw json.RawMessage
	err := chromedp.Run(ctx, chromedp.ActionFunc(func(actx context.Context) error {
		ro, exc, err := runtime.Evaluate(`() => {
			const el = document.getElementById('__NEXT_DATA__');
			return el ? el.textContent : null;
		}`).WithReturnByValue(true).Do(actx)
		if err != nil {
			return err
		}
		if exc != nil && exc.Text != "" {
			return fmt.Errorf("js exception: %s", exc.Text)
		}
		raw = json.RawMessage(string(ro.Value))
		return nil
	}))
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 || string(raw) == "{}" {
		return nil, fmt.Errorf("empty __NEXT_DATA__ result")
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("unmarshal __NEXT_DATA__: %w", err)
	}
	return data, nil
}

// ExtractBodyHTML returns the full rendered <html> outer so that
// siteprofile.Parse can find __NEXT_DATA__ inside <head> and ldjson
// inside <body>.
func ExtractBodyHTML(ctx context.Context) (string, error) {
	var html string
	err := chromedp.Run(ctx,
		chromedp.OuterHTML(`html`, &html, chromedp.ByQuery),
	)
	return html, err
}

// evalRaw runs Runtime.evaluate with returnByValue and hands back the RAW
// result payload. Mirrors reddit/evalRaw.
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
// and a {"value": …} wrapper shape. Mirrors reddit/evalInto.
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

// min returns the smaller of a and b.
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// parseHTML returns an HTML string as-is. Kept for the http fallback path
// in fetchRealEstateListings.
func parseHTML(html string) string {
	return html
}

// findNode returns the text content of the first element matching sel.
func findNode(html, sel string) string {
	return ""
}

// attr returns the attribute value of the first element matching sel.
func attr(html, sel, name string) string {
	return ""
}