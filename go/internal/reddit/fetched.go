package reddit

// The shared "one dialect" shapes (§37.21): every adapter — Reddit, YouTube,
// Hacker News, Discourse — fills these, and the store serialises them into
// ingest_batch. Defined here because reddit is the package the others already
// alias for FetchedComment and Fingerprint.
//
// WHY POINTERS on every count: zero and "this platform does not publish it"
// are different facts, and collapsing them is exactly the fabrication R29
// forbids. A YouTube comment with no likes and a Hacker News comment whose
// score is simply not public would both have read `0` in a plain int64, and a
// downstream ranker would treat the second as unpopular rather than unknown.
// nil serialises to an absent JSON key, so the database records the difference.
//
// WHAT IS NOT HERE, and why nothing fills it in:
//
//	Reddit    per-comment downvotes have not been public since 2014; the site
//	          fuzzes the score and the API returns downs=0. What IS published
//	          is `upvote-ratio` on the POST (verified live: 0.6666…), so that
//	          rides on FetchedPost and downvotes stay nil.
//	YouTube   dislikes were withdrawn in December 2021. Verified live on the
//	          current DOM: the dislike button's aria-label is the bare
//	          "Dislike this video" with no number anywhere on the page.
//	HN        comment points are not public — Algolia returns null for every
//	          comment (verified live). STORY points are public and land on
//	          FetchedPost.
//	Discourse has no downvote; "likes" is actions_summary id 2.
//
// Estimating any of them from a ratio or a rank would be inventing data, so
// they are left nil and the absence is recorded honestly.

// FetchedComment is one comment with everything the platform actually
// publishes about it.
type FetchedComment struct {
	// Identity. ID is what Fingerprint hashes, so it must NOT change for a
	// platform once comments have been ingested under it — a new ID means a
	// new fingerprint, which means the skip-list re-ingests the whole corpus.
	// PlatformID carries the platform's own id where that differs.
	ID         string `json:"id"`
	PlatformID string `json:"platform_id,omitempty"`
	// omitempty: every adapter drops a comment with no body, so an empty one
	// is never a real value — and the store blanks this field deliberately
	// when the body is already recorded a level up.
	Body  string `json:"body,omitempty"`
	Score int64  `json:"score"`

	// Who, when, where.
	Author    string `json:"author,omitempty"`
	AuthorURL string `json:"author_url,omitempty"`
	// CreatedAt is RFC3339 UTC when the platform publishes an absolute time.
	CreatedAt string `json:"created_at,omitempty"`
	// CreatedRaw keeps the platform's own words ("1 year ago") when that is
	// ALL it publishes. Parsing it into a timestamp would manufacture a
	// precision the page never had.
	CreatedRaw string `json:"created_raw,omitempty"`
	Permalink  string `json:"permalink,omitempty"`
	ParentID   string `json:"parent_id,omitempty"`
	// NO omitempty: depth 0 is the commonest depth there is — a top-level
	// comment — and omitting it made every one of them read as "depth not
	// captured" downstream.
	Depth int `json:"depth"`

	// Engagement.
	Upvotes   *int64 `json:"upvotes,omitempty"`
	Downvotes *int64 `json:"downvotes,omitempty"`
	Likes     *int64 `json:"likes,omitempty"`
	Dislikes  *int64 `json:"dislikes,omitempty"`
	Replies   *int64 `json:"replies,omitempty"`
	Awards    *int64 `json:"awards,omitempty"`
	Reads     *int64 `json:"reads,omitempty"`

	// Flags. These carry omitempty because `true` is the informative case and
	// false is the overwhelming majority; a reader that has the `detail`
	// object at all knows the adapter looked, so an absent flag there means
	// false rather than unknown. (Python's extractor seeds them accordingly.)
	Edited        bool `json:"edited,omitempty"`
	Pinned        bool `json:"pinned,omitempty"`
	AuthorIsOP    bool `json:"author_is_op,omitempty"`
	Distinguished bool `json:"distinguished,omitempty"`
	Accepted      bool `json:"accepted_answer,omitempty"`
	Hidden        bool `json:"hidden,omitempty"`

	// Extra is the long tail one platform publishes and the others do not —
	// Discourse trust levels and read counts, Reddit content types, YouTube
	// creator hearts. Kept rather than dropped: "scrape every detail" means
	// the detail that does not generalise too.
	Extra map[string]any `json:"extra,omitempty"`
}

// FetchedPost is the thread / video / topic the comments hang off. None of
// this used to be captured at all: a batch carried a thread_id and nothing
// else, so the archive could not say what the discussion was even about.
type FetchedPost struct {
	ID        string `json:"id,omitempty"`
	Title     string `json:"title,omitempty"`
	URL       string `json:"url,omitempty"`
	Body      string `json:"body,omitempty"`
	Author    string `json:"author,omitempty"`
	AuthorURL string `json:"author_url,omitempty"`
	CreatedAt string `json:"created_at,omitempty"`
	// CreatedRaw: same rule as the comment — the platform's own words when
	// that is all it gives ("3y ago").
	CreatedRaw string `json:"created_raw,omitempty"`

	Score *int64 `json:"score,omitempty"`
	// UpvoteRatio is Reddit's published fraction of votes that were upvotes.
	// It is the ONLY downvote signal any of these four platforms publishes,
	// and it is deliberately left as the raw ratio: score is vote-fuzzed, so
	// solving ups/downs out of it would hand downstream a precise-looking
	// number the site never stood behind.
	UpvoteRatio  *float64 `json:"upvote_ratio,omitempty"`
	Upvotes      *int64   `json:"upvotes,omitempty"`
	Downvotes    *int64   `json:"downvotes,omitempty"`
	Likes        *int64   `json:"likes,omitempty"`
	Dislikes     *int64   `json:"dislikes,omitempty"`
	CommentCount *int64   `json:"comment_count,omitempty"`
	Views        *int64   `json:"views,omitempty"`
	Awards       *int64   `json:"awards,omitempty"`
	Participants *int64   `json:"participants,omitempty"`
	Subscribers  *int64   `json:"subscribers,omitempty"`

	// Community is the subreddit / channel / forum-category the post lives in.
	Community    string   `json:"community,omitempty"`
	CommunityURL string   `json:"community_url,omitempty"`
	Tags         []string `json:"tags,omitempty"`
	Language     string   `json:"language,omitempty"`
	Kind         string   `json:"kind,omitempty"`
	Closed       bool     `json:"closed,omitempty"`
	Archived     bool     `json:"archived,omitempty"`
	Pinned       bool     `json:"pinned,omitempty"`

	Extra map[string]any `json:"extra,omitempty"`
}

// I64 boxes a count that WAS published. Every adapter goes through this rather
// than taking the address of a local, so "published as zero" reads the same
// everywhere and cannot be confused with an unset field.
func I64(v int64) *int64 { return &v }

// F64 boxes a published ratio.
func F64(v float64) *float64 { return &v }
