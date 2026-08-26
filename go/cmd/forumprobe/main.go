// Command forumprobe tests candidate Discourse forums before they reach the
// config.
//
// Adding a forum is one config line, which makes it tempting to add forty from
// memory. Half of them would be dead, moved, running phpBB, or Discourse
// instances that require login to read — and every one of those becomes a
// source that fails silently every run, burns a slot in the rotation, and
// teaches the operator to ignore skip messages.
//
// So: probe first, keep what answers. Throwaway; not wired into the build.
package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"jester/internal/discourse"
)

type result struct {
	url    string
	topics int
	sample string
	err    error
}

func main() {
	candidates := strings.Fields(strings.Join(os.Args[1:], " "))
	if len(candidates) == 0 {
		fmt.Println("usage: forumprobe <url> [url...]")
		return
	}

	// Concurrency 6: polite to any single host (each candidate is a different
	// host anyway) and finishes forty probes in well under a minute.
	sem := make(chan struct{}, 6)
	var wg sync.WaitGroup
	results := make([]result, len(candidates))

	for i, raw := range candidates {
		wg.Add(1)
		go func(i int, raw string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
			defer cancel()

			dc := discourse.New()
			// A low floor here: the question is "does this forum answer", not
			// "is it busy". Depth is a separate config decision.
			dc.MinPosts = 2
			topics, err := dc.ListTopics(ctx, raw, 5)
			r := result{url: raw, err: err}
			if err == nil {
				r.topics = len(topics)
				if len(topics) > 0 {
					r.sample = topics[0].Title
				}
			}
			results[i] = r
		}(i, raw)
	}
	wg.Wait()

	good, bad := 0, 0
	for _, r := range results {
		if r.err != nil {
			bad++
			msg := r.err.Error()
			if len(msg) > 72 {
				msg = msg[:72]
			}
			fmt.Printf("FAIL %-40s %s\n", host(r.url), msg)
			continue
		}
		good++
		s := r.sample
		if len(s) > 52 {
			s = s[:52]
		}
		fmt.Printf("OK   %-40s %d topics · %q\n", host(r.url), r.topics, s)
	}
	fmt.Printf("\n%d answered, %d did not\n", good, bad)
}

func host(u string) string {
	return strings.TrimSuffix(strings.TrimPrefix(strings.TrimPrefix(u, "https://"), "http://"), "/")
}
