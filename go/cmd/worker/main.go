// Command worker is the Jester ingestion worker (Go). Mock mode (default):
// load fixtures, run pre-filter, dedup against the skip-list, enqueue
// ingest_batch rows into SQLite. `-live` (M1.1, §37.21): drive cloakserve via
// chromedp over the calibrated shreddit DOM path.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"jester/internal/cloakbrowser"
	"jester/internal/config"
	"jester/internal/discourse"
	"jester/internal/hackernews"
	"jester/internal/prefilter"
	"jester/internal/reddit"
	"jester/internal/store"
	"jester/internal/youtube"
)

func main() {
	configDir := flag.String("config", "config", "directory with sources.yaml, thresholds.yaml, scraper.yaml")
	dbPath := flag.String("db", "data/jester.db", "SQLite database path")
	runID := flag.String("run", "mock-run", "run identifier")
	fixture := flag.String("fixture", "testdata/reddit_thread_1.json", "mock fixture file")
	live := flag.Bool("live", false, "fetch live via cloakserve CDP (§37.21); requires the container running")
	flag.Parse()

	cfg, err := config.LoadConfig(*configDir)
	if err != nil {
		fail("load config: %v", err)
	}
	enabled := 0
	for _, s := range cfg.Sources.Sources {
		if s.IsEnabled() {
			enabled++
		}
	}
	fmt.Printf("config schema_version=%d sources=%d (%d enabled)\n",
		config.SchemaVersion, len(cfg.Sources.Sources), enabled)

	os.MkdirAll(filepath.Dir(*dbPath), 0o755)
	st, err := store.Open(*dbPath)
	if err != nil {
		fail("open store: %v", err)
	}
	defer st.Close()

	pp := prefilter.Params{
		MinChars:    cfg.Thresholds.PrefilterMinChars,
		MaxChars:    cfg.Thresholds.PrefilterMaxChars,
		MaxEmoji:    cfg.Thresholds.PrefilterMaxEmoji,
		MaxMentions: cfg.Thresholds.PrefilterMaxMentions,
		MinWords:    cfg.Thresholds.PrefilterMinWords,
	}

	if *live {
		runLive(cfg, st, *runID, pp)
		return
	}

	cb := cloakbrowser.New(cfg.Scraper.CDPURL)
	if !cb.Mock {
		fail("live fetch requires -live (set JESTER_MOCK=1 for mock mode)")
	}

	comments, err := reddit.MockLoad(*fixture)
	if err != nil {
		fail("mock load: %v", err)
	}

	var batch []store.Comment
	for _, c := range comments {
		fp := reddit.Fingerprint(c)
		dup, err := st.AlreadyIngested(fp)
		if err != nil {
			fail("dedup check: %v", err)
		}
		if dup {
			continue
		}
		ok, reason := prefilter.Keep(c.Body, pp)
		if !ok {
			fmt.Printf("filtered (%s): %.40q\n", reason, c.Body)
			continue
		}
		if err := st.MarkIngested(fp); err != nil {
			fail("mark ingested: %v", err)
		}
		batch = append(batch, store.Comment{Body: c.Body, Fingerprint: fp, Upvotes: c.Score})
	}

	if len(batch) == 0 {
		// An empty batch is still a pending row the console counts as work
		// waiting; a re-run of the same fixture should queue nothing at all.
		fmt.Println("nothing new to enqueue (every comment was already ingested or filtered)")
		return
	}
	id, err := st.EnqueueBatch(store.Batch{
		RunID:    *runID,
		Platform: "reddit",
		Source:   "mock",
		ThreadID: filepath.Base(*fixture),
		Index:    0,
		Comments: batch,
	})
	if err != nil {
		fail("enqueue: %v", err)
	}
	fmt.Printf("enqueued batch id=%d with %d kept comments\n", id, len(batch))
}

func runLive(cfg *config.Config, st *store.Store, runID string, pp prefilter.Params) {
	delay := time.Duration(cfg.Thresholds.RequestDelayMs) * time.Millisecond
	perSource := cfg.Thresholds.MaxThreadsPerSource
	if perSource < 1 {
		perSource = 1
	}
	total, skipped := 0, 0
	for _, src := range cfg.Sources.Sources {
		if !src.IsEnabled() {
			fmt.Printf("[live] %s/%s disabled — skipping\n", src.Platform, src.Name)
			continue
		}
		kind := src.ResolvedKind()

		ctx := context.Background()
		var cancel func()
		if needsBrowser(src.Platform) {
			// §4.3.2: one stable stealth identity per source. cloakserve keys a
			// separate Chrome process (and cookie jar) off the seed, so a source
			// keeps its warmed session between runs. Sessions are opened one at a
			// time — the free tier's concurrency ceiling (v3.11 #1) makes parallel
			// fetching a licence question, not a code one.
			seed := cloakbrowser.SeedFor(src.Platform, src.Name)
			cb := cloakbrowser.NewWithOptions(cfg.Scraper.CDPURL, cloakbrowser.Options{
				Fingerprint: seed,
				Timezone:    cfg.Scraper.Timezone,
				Locale:      cfg.Scraper.Locale,
				Proxy:       cfg.Scraper.Proxy,
				GeoIP:       cfg.Scraper.GeoIPEnabled(),
			})
			sess, err := cb.NewSession(ctx)
			if err != nil {
				fmt.Printf("[live] %s/%s (%s) skipped: cdp session: %v\n",
					src.Platform, src.Name, kind, err)
				skipped++
				continue
			}
			fmt.Printf("[live] session seed=%s\n", seed)
			ctx, cancel = sess.Ctx, sess.Cancel
		}

		n, err := fetchSource(ctx, cfg, st, runID, pp, src, kind, delay, perSource)
		if cancel != nil {
			cancel()
		}
		if err != nil {
			// One unreachable source must not abort the others: every run
			// says which source failed and why, and keeps going.
			fmt.Printf("[live] %s/%s (%s) skipped: %v\n", src.Platform, src.Name, kind, err)
			skipped++
			continue
		}
		total += n
	}
	fmt.Printf("[live] done: %d comment(s) queued this run, %d source(s) skipped\n", total, skipped)
}

// needsBrowser reports whether a platform is scraped through cloakserve.
//
// Hacker News and Discourse expose documented public JSON APIs, so they are
// read over plain HTTP — no stealth browser, no fingerprint to warm, no
// challenge to absorb. Opening a Chrome process for them would be pure cost.
func needsBrowser(platform string) bool {
	switch platform {
	case "hackernews", "discourse":
		return false
	default:
		return true
	}
}

// fetchSource routes one source to the right adapter based on its kind.
func fetchSource(ctx context.Context, cfg *config.Config, st *store.Store, runID string,
	pp prefilter.Params, src config.Source, kind string, delay time.Duration, perSource int,
) (int, error) {
	maxPerThread := int(cfg.Thresholds.MaxCommentsPerThread)
	switch {
	case src.Platform == "reddit" && kind == "thread":
		fmt.Printf("[live] reddit thread %s\n", src.URL)
		comments, err := reddit.FetchThreadURL(ctx, src.URL, delay, cfg.Scraper.BlockedAction)
		if err != nil {
			return 0, err
		}
		threadID := reddit.ThreadIDFromPath(src.URL)
		n := enqueueComments(st, runID, "reddit", src.URL, threadID, comments, pp, maxPerThread)
		fmt.Printf("[live] enqueued %d kept comments (thread %s)\n", n, threadID)
		return n, nil

	case src.Platform == "reddit" && kind == "subreddit":
		listing := strings.TrimRight(src.URL, "/") + "/new/"
		fmt.Printf("[live] reddit listing %s (up to %d thread(s))\n", listing, perSource)
		threads, err := reddit.ListThreads(ctx, listing, delay, perSource)
		if err != nil {
			return 0, err
		}
		total := 0
		for _, threadURL := range threads {
			comments, err := reddit.FetchThreadURL(ctx, threadURL, delay, cfg.Scraper.BlockedAction)
			if err != nil {
				fmt.Printf("[live]   thread %s skipped: %v\n", threadURL, err)
				continue
			}
			threadID := reddit.ThreadIDFromPath(threadURL)
			n := enqueueComments(st, runID, "reddit", threadURL, threadID, comments, pp, maxPerThread)
			fmt.Printf("[live]   enqueued %d kept comments (thread %s)\n", n, threadID)
			total += n
		}
		return total, nil

	case src.Platform == "youtube" && kind == "video":
		fmt.Printf("[live] youtube video %s\n", src.URL)
		comments, err := youtube.FetchVideoComments(
			ctx, src.URL, delay, 5, cfg.Scraper.BlockedAction,
		)
		if err != nil {
			return 0, err
		}
		videoID := youtube.VideoIDFromURL(src.URL)
		n := enqueueComments(st, runID, "youtube", src.URL, videoID, comments, pp, maxPerThread)
		fmt.Printf("[live] enqueued %d kept comments (video %s)\n", n, videoID)
		return n, nil

	case src.Platform == "youtube" && kind == "channel":
		fmt.Printf("[live] youtube channel %s (up to %d video(s))\n", src.URL, perSource)
		videos, err := youtube.ListChannelVideos(ctx, src.URL, delay, perSource)
		if err != nil {
			return 0, err
		}
		total := 0
		for _, watchURL := range videos {
			comments, err := youtube.FetchVideoComments(
				ctx, watchURL, delay, 5, cfg.Scraper.BlockedAction,
			)
			if err != nil {
				fmt.Printf("[live]   video %s skipped: %v\n", watchURL, err)
				continue
			}
			videoID := youtube.VideoIDFromURL(watchURL)
			n := enqueueComments(st, runID, "youtube", watchURL, videoID, comments, pp, maxPerThread)
			fmt.Printf("[live]   enqueued %d kept comments (video %s)\n", n, videoID)
			total += n
		}
		return total, nil

	case src.Platform == "hackernews":
		hn := hackernews.New()
		if cfg.Thresholds.MinCommentsPerThread > 0 {
			hn.MinComments = cfg.Thresholds.MinCommentsPerThread
		}
		stories := []hackernews.Story{}
		if kind == "story" {
			stories = append(stories, hackernews.Story{ID: hackernewsIDFromURL(src.URL)})
		} else {
			tag := hackernewsTagFromURL(src.URL)
			fmt.Printf("[live] hacker news %s (up to %d thread(s))\n", tag, perSource)
			found, err := hn.ListStories(ctx, tag, perSource)
			if err != nil {
				return 0, err
			}
			stories = found
		}
		total := 0
		for _, s := range stories {
			if s.ID == "" {
				continue
			}
			comments, err := hn.FetchComments(ctx, s.ID)
			if err != nil {
				fmt.Printf("[live]   story %s skipped: %v\n", s.ID, err)
				continue
			}
			n := enqueueComments(st, runID, "hackernews",
				hackernews.SourceURL(s.ID), "hn-"+s.ID, comments, pp, maxPerThread)
			fmt.Printf("[live]   enqueued %d kept comments (hn %s)\n", n, s.ID)
			total += n
			sleep(delay)
		}
		return total, nil

	case src.Platform == "discourse":
		dc := discourse.New()
		if cfg.Thresholds.MinCommentsPerThread > 0 {
			// Same floor as Hacker News: both adapters get a count in the
			// listing, so both can skip a quiet thread before fetching it.
			dc.MinPosts = cfg.Thresholds.MinCommentsPerThread
		}
		base := discourse.BaseURL(src.URL)
		topics := []discourse.Topic{}
		if kind == "topic" {
			id, slug := discourseTopicFromURL(src.URL)
			if id == 0 {
				return 0, fmt.Errorf("no topic id in %s", src.URL)
			}
			base = discourseBaseFromTopicURL(src.URL)
			topics = append(topics, discourse.Topic{ID: id, Slug: slug})
		} else {
			fmt.Printf("[live] discourse %s (up to %d topic(s))\n", base, perSource)
			found, err := dc.ListTopics(ctx, base, perSource)
			if err != nil {
				return 0, err
			}
			topics = found
		}
		total := 0
		for _, t := range topics {
			comments, err := dc.FetchPosts(ctx, base, t.ID)
			if err != nil {
				fmt.Printf("[live]   topic %d skipped: %v\n", t.ID, err)
				continue
			}
			n := enqueueComments(st, runID, "discourse",
				discourse.TopicURL(base, t), fmt.Sprintf("dc-%d", t.ID), comments, pp, maxPerThread)
			fmt.Printf("[live]   enqueued %d kept comments (topic %d)\n", n, t.ID)
			total += n
			sleep(delay)
		}
		return total, nil

	default:
		// R29 spirit: say so out loud rather than silently ingesting nothing.
		return 0, fmt.Errorf("no adapter for platform=%q kind=%q yet", src.Platform, kind)
	}
}

// enqueueComments pre-filters, dedups and queues one thread's comments.
//
// maxPerThread caps what is *kept*. Python's pre-filter has always applied the
// same cap, but only after the worker had already queued the whole thread — so
// an Ask HN mega-thread (849 comments in one live run) landed in ingest_batch
// in full and was then mostly discarded downstream. Capping here keeps the
// queue honest about how much work is actually pending, and stops one busy
// thread from crowding out every other source in a night's run.
func enqueueComments(st *store.Store, runID, platform, source, threadID string,
	comments []reddit.FetchedComment, pp prefilter.Params, maxPerThread int) int {
	var batch []store.Comment
	dropped := 0
	for _, c := range comments {
		if maxPerThread > 0 && len(batch) >= maxPerThread {
			dropped = len(comments) - len(batch) - dropped
			break
		}
		fp := reddit.Fingerprint(c)
		dup, err := st.AlreadyIngested(fp)
		if err != nil {
			fail("dedup check: %v", err)
		}
		if dup {
			continue
		}
		ok, reason := prefilter.Keep(c.Body, pp)
		if !ok {
			fmt.Printf("filtered (%s): %.40q\n", reason, c.Body)
			continue
		}
		if err := st.MarkIngested(fp); err != nil {
			fail("mark ingested: %v", err)
		}
		batch = append(batch, store.Comment{Body: c.Body, Fingerprint: fp, Upvotes: c.Score})
	}
	if maxPerThread > 0 && len(batch) >= maxPerThread {
		// R55/no-silent-caps: a truncated thread must say so, or the run reads
		// as if it captured everything the thread had.
		fmt.Printf("capped thread %s at %d comment(s) (max_comments_per_thread)\n",
			threadID, maxPerThread)
	}
	if len(batch) == 0 {
		return 0
	}
	id, err := st.EnqueueBatch(store.Batch{
		RunID: runID, Platform: platform, Source: source,
		ThreadID: threadID, Index: 0, Comments: batch,
	})
	if err != nil {
		fail("enqueue: %v", err)
	}
	_ = id
	return len(batch)
}

func fail(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "worker: "+format+"\n", a...)
	os.Exit(1)
}


// sleep paces adapters that talk plain HTTP; the browser adapters pace inside
// chromedp actions instead.
func sleep(d time.Duration) {
	if d > 0 {
		time.Sleep(d)
	}
}

// hackernewsTagFromURL reads the Algolia tag out of a curated HN source URL
// ("https://news.ycombinator.com/ask" -> "ask_hn"). Unknown paths fall back to
// ask_hn, the densest pain-signal feed.
func hackernewsTagFromURL(raw string) string {
	// Match on the PATH, not the whole URL: the host itself contains "news",
	// so a whole-string match couples the mapping to the hostname and would
	// misread any future feed whose name shares a substring with it.
	low := strings.ToLower(strings.TrimSpace(raw))
	path := low
	if i := strings.Index(low, "news.ycombinator.com"); i >= 0 {
		path = low[i+len("news.ycombinator.com"):]
	}
	path = strings.Trim(strings.SplitN(path, "?", 2)[0], "/")
	switch path {
	case "show", "shownew", "show_hn":
		return "show_hn"
	case "news", "newest", "front", "front_page":
		return "front_page"
	default:
		// Anything unrecognised falls back to the densest pain-signal feed
		// rather than erroring a curated source out of the run.
		return "ask_hn"
	}
}

// hackernewsIDFromURL pulls the item id out of .../item?id=12345.
func hackernewsIDFromURL(raw string) string {
	if i := strings.Index(raw, "id="); i >= 0 {
		rest := raw[i+3:]
		if j := strings.IndexAny(rest, "&#?"); j >= 0 {
			rest = rest[:j]
		}
		return strings.TrimSpace(rest)
	}
	return ""
}

// discourseTopicFromURL parses /t/<slug>/<id> (slug optional).
func discourseTopicFromURL(raw string) (int64, string) {
	parts := strings.Split(strings.Trim(raw, "/"), "/")
	for i, p := range parts {
		if p != "t" {
			continue
		}
		slug := ""
		for _, tail := range parts[i+1:] {
			if n, err := strconv.ParseInt(tail, 10, 64); err == nil {
				return n, slug
			}
			slug = tail
		}
	}
	return 0, ""
}

// discourseBaseFromTopicURL trims a topic path back to the forum root.
func discourseBaseFromTopicURL(raw string) string {
	if i := strings.Index(raw, "/t/"); i > 0 {
		return discourse.BaseURL(raw[:i])
	}
	return discourse.BaseURL(raw)
}
