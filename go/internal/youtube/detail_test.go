package youtube

import "testing"

func TestParseCountReadsYouTubesHumanNumbers(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want int64
		ok   bool
	}{
		{"306K", 306000, true},
		{"1.8B", 1800000000, true},
		{"41", 41, true},
		{"963 replies", 963, true},
		{"2,455,504 Comments", 2455504, true},
		{"1.8B views  16y ago", 1800000000, true},
		{"like this video along with 19,352,172 other people", 19352172, true},
		{"4.53M subscribers", 4530000, true},
		// The one that matters most: YouTube withdrew dislike counts in
		// December 2021, so the button's label carries no number at all. That
		// must read as "nothing published", never as zero dislikes.
		{"Dislike this video", 0, false},
		{"", 0, false},
		{"no digits here", 0, false},
	} {
		got, ok := ParseCount(tc.in)
		if ok != tc.ok || (ok && got != tc.want) {
			t.Errorf("ParseCount(%q) = %d,%v; want %d,%v", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}

func TestCommentsFromDOMKeepsLikesAndIdentity(t *testing.T) {
	rows := []map[string]any{{
		"author":             "@bic4",
		"author_url":         "/@bic4",
		"body":               "  the setup docs are wrong  ",
		"likes":              "306K",
		"published_raw":      "1 year ago",
		"comment_id":         "Ugzge340dBgB75hWBm54AaABAg",
		"permalink":          "/watch?v=abc&lc=Ugzge340dBgB75hWBm54AaABAg",
		"replies":            "963 replies",
		"hearted":            true,
		"pinned":             true,
		"author_is_uploader": false,
	}}
	got := CommentsFromDOM(rows, "https://www.youtube.com/watch?v=abc")
	if len(got) != 1 {
		t.Fatalf("want 1 comment, got %d", len(got))
	}
	c := got[0]
	// Likes ARE the engagement figure here. Leaving Score at 0 ranked every
	// YouTube comment below a one-upvote Reddit reply.
	if c.Likes == nil || *c.Likes != 306000 || c.Score != 306000 {
		t.Errorf("like count lost: %+v score=%d", c.Likes, c.Score)
	}
	if c.Dislikes != nil {
		t.Errorf("youtube publishes no dislikes; got %d", *c.Dislikes)
	}
	if c.Replies == nil || *c.Replies != 963 {
		t.Errorf("reply count lost: %+v", c.Replies)
	}
	// The fingerprint input must NOT move: it is author+body, and changing it
	// would give every already-ingested YouTube comment a new fingerprint and
	// re-ingest the whole back catalogue as new.
	if c.ID != "@bic4the setup docs are wrong" {
		t.Errorf("fingerprint input changed: %q", c.ID)
	}
	if c.PlatformID != "Ugzge340dBgB75hWBm54AaABAg" {
		t.Errorf("real comment id lost: %q", c.PlatformID)
	}
	if c.Permalink != "https://www.youtube.com/watch?v=abc&lc=Ugzge340dBgB75hWBm54AaABAg" {
		t.Errorf("permalink wrong: %q", c.Permalink)
	}
	// YouTube renders ages, not dates. Keeping its words beats resolving them
	// to a timestamp the page never published.
	if c.CreatedRaw != "1 year ago" || c.CreatedAt != "" {
		t.Errorf("relative age mishandled: raw=%q at=%q", c.CreatedRaw, c.CreatedAt)
	}
	if !c.Pinned || c.AuthorIsOP {
		t.Errorf("flags wrong: pinned=%v op=%v", c.Pinned, c.AuthorIsOP)
	}
	if c.Extra["creator_hearted"] != true {
		t.Errorf("creator heart lost: %+v", c.Extra)
	}
}

func TestPostFromMetaTakesTheExactLikeCount(t *testing.T) {
	p := PostFromMeta(map[string]any{
		"title":         "Rick Astley - Never Gonna Give You Up",
		"channel":       "Rick Astley",
		"channel_url":   "/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
		"subscribers":   "4.53M subscribers",
		"info":          "1.8B views  16y ago",
		"published":     "Oct 25, 2009",
		"like_label":    "like this video along with 19,352,172 other people",
		"dislike_label": "Dislike this video",
		"comment_count": "2,455,504 Comments",
		"upload_date":   "2009-10-24T23:57:33-07:00",
	}, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
	if p == nil {
		t.Fatal("video metadata dropped")
	}
	// The visible label is abbreviated to "19M"; the aria-label is exact.
	if p.Likes == nil || *p.Likes != 19352172 {
		t.Errorf("exact like count lost: %+v", p.Likes)
	}
	// "Dislike this video" carries no number — nothing to record.
	if p.Dislikes != nil {
		t.Errorf("dislikes were withdrawn in 2021; got %d", *p.Dislikes)
	}
	if p.Views == nil || *p.Views != 1800000000 {
		t.Errorf("view count lost: %+v", p.Views)
	}
	if p.Subscribers == nil || *p.Subscribers != 4530000 {
		t.Errorf("subscriber count lost: %+v", p.Subscribers)
	}
	if p.CommentCount == nil || *p.CommentCount != 2455504 {
		t.Errorf("comment count lost: %+v", p.CommentCount)
	}
	if p.CreatedAt != "2009-10-25T06:57:33Z" {
		t.Errorf("upload date not normalised to UTC: %q", p.CreatedAt)
	}
	if p.ID != "dQw4w9WgXcQ" {
		t.Errorf("video id lost: %q", p.ID)
	}
}
