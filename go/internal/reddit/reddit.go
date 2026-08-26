// Package reddit defines the Reddit ingestion contract. Live fetch (M1.1+)
// drives a CloakBrowser session against Reddit's internal GraphQL endpoint
// (§35/§36); mock mode (M1.0) loads fixtures instead. The GraphQL query string
// is defined here so it is versioned with the code.
package reddit

import (
	"encoding/json"
	"fmt"
	"os"
)

// GraphQLQuery is the internal Reddit query used by the CloakBrowser session.
// Documented here; exact variable shape is validated by integration tests.
const GraphQLQuery = `
query ThreadComments($permalink: String!, $after: String, $limit: Int!) {
  post(permalink: $permalink) {
    title
    comments(after: $after, first: $limit) {
      edges { node { id body score createdAt } }
      pageInfo { hasNextPage endCursor }
    }
  }
}`

// MockLoad reads a fixture file (testdata/*.json) containing a list of
// FetchedComment and returns them. Used in mock mode so the full prefilter ->
// enqueue path runs without a live browser.
func MockLoad(path string) ([]FetchedComment, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read fixture %s: %w", path, err)
	}
	var out []FetchedComment
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("parse fixture %s: %w", path, err)
	}
	return out, nil
}
