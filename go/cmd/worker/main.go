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
	"strings"
	"time"

	"jester/internal/cloakbrowser"
	"jester/internal/config"
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
	fmt.Printf("config schema_version=%d sources=%d\n", config.SchemaVersion, len(cfg.Sources.Sources))

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
	cb := cloakbrowser.New(cfg.Scraper.CDPURL)
	sess, err := cb.NewSession(context.Background())
	if err != nil {
		fail("cdp session: %v", err)
	}
	defer sess.Cancel()

	delay := time.Duration(cfg.Thresholds.RequestDelayMs) * time.Millisecond
	total := 0
	for _, src := range cfg.Sources.Sources {
		switch src.Platform {
		case "reddit":
			listing := strings.TrimRight(src.URL, "/") + "/new/"
			fmt.Printf("[live] %s\n", listing)
			comments, threadURL, threadID, err := reddit.FetchThread(
				sess.Ctx, listing, delay,
				reddit.ThreadIDFromPath, cfg.Scraper.BlockedAction,
			)
			if err != nil {
				fmt.Printf("[live] skipped (%v)\n", err)
				continue
			}
			n := enqueueComments(st, runID, "reddit", threadURL, threadID, comments, pp)
			fmt.Printf("[live] enqueued batch with %d kept comments (thread %s)\n", n, threadID)
			total += n

		case "youtube":
			listing := strings.TrimRight(src.URL, "/") + "/videos"
			fmt.Printf("[live] %s\n", listing)
			comments, err := youtube.FetchVideoComments(
				sess.Ctx, cfg.Scraper.CDPURL, listing, delay, 5, cfg.Scraper.BlockedAction,
			)
			if err != nil {
				fmt.Printf("[live] skipped (%v)\n", err)
				continue
			}
			watchURL := src.URL
			threadID := youtube.VideoIDFromURL(watchURL)
			n := enqueueComments(st, runID, "youtube", watchURL, threadID, comments, pp)
			fmt.Printf("[live] enqueued batch with %d kept comments (video %s)\n", n, threadID)
			total += n
		}
	}
	fmt.Printf("[live] done: %d comment(s) queued this run\n", total)
}

func enqueueComments(st *store.Store, runID, platform, source, threadID string,
	comments []reddit.FetchedComment, pp prefilter.Params) int {
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

