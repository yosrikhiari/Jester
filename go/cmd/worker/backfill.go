package main

import (
	"context"
	"fmt"
	"time"

	"jester/internal/config"
	"jester/internal/hackernews"
	"jester/internal/prefilter"
	"jester/internal/store"
)

// Backfill is the one-off deep walk into a platform's archive, as opposed to
// the scheduled run's shallow pass over every source.
//
// WHY IT IS A SEPARATE MODE. A scheduled tick is a rotation: each source gets a
// small, comparable slice so the whole list is covered across ticks. A backfill
// is the opposite shape — one source, thousands of threads, hours of walking —
// and running it through the rotation would starve every other source for as
// long as it took. It also must not touch source_state: a backfill is not a
// visit in the rotation's sense, and crediting it as one would send a source to
// the back of the queue for days on the strength of work the schedule did not
// do.
//
// WHAT IT CAN REACH, and what it cannot. Hacker News only, for now, because it
// is the one platform where the ceiling was both real and crossable. Algolia
// reports nbHits=180,552 for ask_hn, but caps every query at 1,000 RESULTS —
// page-walking reaches 0.6% of it — and a created_at_i cursor opens a fresh
// window each time. Discourse and Lemmy already page properly, Steam already
// cursors, and the metered APIs (Stack Exchange, GitHub) would spend a daily
// quota in one run, so none of them are wired here.

// backfillOptions is what the operator asked for.
type backfillOptions struct {
	// Source is the curated source name to walk, which must be a hackernews
	// feed. Naming a source rather than a tag keeps the depth ceiling, the
	// prefilter and the community label identical to an ordinary run.
	Source string
	// Want is the number of THREADS to walk, not comments.
	Want int
	// Until is a Unix second to stop at; 0 walks until Want is met.
	Until int64
	// Delay paces the listing walk. The comment fetches are paced by the
	// caller's own delay.
	Delay time.Duration
}

// runBackfill walks one source deep and enqueues everything it finds.
//
// Returns the number of comments queued. Errors from a single thread are
// reported and skipped rather than ending the walk — an archive deep enough to
// be worth backfilling will contain a few threads that no longer fetch, and
// abandoning thousands of good ones over them is not a trade worth making.
func runBackfill(ctx context.Context, cfg *config.Config, st *store.Store, runID string,
	pp prefilter.Params, sources []config.Source, opts backfillOptions,
) (int, error) {
	src, err := findSource(sources, opts.Source)
	if err != nil {
		return 0, err
	}
	if src.Platform != "hackernews" {
		return 0, fmt.Errorf(
			"backfill is only implemented for hackernews, and %q is %s — "+
				"Discourse and Lemmy already page, Steam already cursors, and the "+
				"metered APIs would spend a day's quota in one run",
			src.Name, src.Platform)
	}
	kind := src.ResolvedKind()
	if kind != "feed" {
		return 0, fmt.Errorf(
			"backfill walks a feed; %q is a %s, which is a single thread",
			src.Name, kind)
	}

	hn := hackernews.New()
	hn.Delay = opts.Delay
	if cfg.Thresholds.MinCommentsPerThread > 0 {
		hn.MinComments = int(cfg.Thresholds.MinCommentsPerThread)
	}
	tag := hackernewsTagFromURL(src.URL)
	feed := hackernews.FeedName(tag)

	fmt.Printf("[backfill] %s (%s) — up to %d thread(s)%s\n",
		src.Name, tag, opts.Want, untilPhrase(opts.Until))

	stories, err := hn.BackfillStories(ctx, tag, opts.Want, opts.Until,
		func(n int, oldest int64) {
			// Say how far back the walk has reached while it is happening. A
			// silent hour is indistinguishable from a hung one.
			fmt.Printf("[backfill]   +%d listed, now at %s\n", n, stamp(oldest))
		})
	if err != nil {
		return 0, err
	}
	if len(stories) == 0 {
		return 0, fmt.Errorf("%s yielded no stories in that window", src.Name)
	}
	fmt.Printf("[backfill] %d thread(s) to fetch, oldest %s\n",
		len(stories), stamp(oldestOf(stories)))

	total, failed := 0, 0
	for i, s := range stories {
		if s.ID == "" {
			continue
		}
		comments, post, err := hn.FetchComments(ctx, s.ID)
		if err != nil {
			failed++
			fmt.Printf("[backfill]   story %s skipped: %v\n", s.ID, err)
			continue
		}
		if post != nil && feed != "" {
			post.Community = feed
			post.CommunityURL = hackernews.FeedURL(feed)
		}
		n := enqueueComments(st, runID, "hackernews",
			hackernews.SourceURL(s.ID), "hn-"+s.ID, comments, post, pp,
			int(cfg.Thresholds.MaxCommentsPerThread))
		total += n
		// One line per thread over thousands would bury the run; every
		// twenty-five is enough to see it moving.
		if (i+1)%25 == 0 || i == len(stories)-1 {
			fmt.Printf("[backfill]   %d/%d thread(s), %d comment(s) queued\n",
				i+1, len(stories), total)
		}
		if opts.Delay > 0 {
			select {
			case <-ctx.Done():
				return total, ctx.Err()
			case <-time.After(opts.Delay):
			}
		}
	}
	// R55: a walk that silently dropped threads reads as a clean run.
	if failed > 0 {
		fmt.Printf("[backfill] %d thread(s) could not be fetched and were skipped\n", failed)
	}
	fmt.Printf("[backfill] done: %d comment(s) from %d thread(s)\n", total, len(stories)-failed)
	return total, nil
}

// findSource resolves a curated source by name, listing what IS available when
// the name is wrong — a bare "not found" on a 93-entry list is a guessing game.
func findSource(sources []config.Source, name string) (config.Source, error) {
	for _, s := range sources {
		if s.Name == name {
			return s, nil
		}
	}
	var feeds []string
	for _, s := range sources {
		if s.Platform == "hackernews" && s.ResolvedKind() == "feed" {
			feeds = append(feeds, s.Name)
		}
	}
	return config.Source{}, fmt.Errorf(
		"no source named %q; backfillable sources are: %v", name, feeds)
}

func oldestOf(stories []hackernews.Story) int64 {
	var oldest int64
	for _, s := range stories {
		if s.CreatedAtI > 0 && (oldest == 0 || s.CreatedAtI < oldest) {
			oldest = s.CreatedAtI
		}
	}
	return oldest
}

// stamp renders a Unix second as a date, or "unknown" for the zero it uses to
// mean "the page published no timestamp" — printing 1970 would be a lie.
func stamp(sec int64) string {
	if sec <= 0 {
		return "unknown"
	}
	return time.Unix(sec, 0).UTC().Format("2006-01-02")
}

func untilPhrase(until int64) string {
	if until <= 0 {
		return ""
	}
	return ", stopping at " + stamp(until)
}
