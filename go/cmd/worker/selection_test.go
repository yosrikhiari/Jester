package main

import (
	"strings"
	"testing"

	"jester/internal/config"
)

func srcs(names ...string) []config.Source {
	out := make([]config.Source, 0, len(names))
	for _, n := range names {
		out = append(out, config.Source{Name: n, Platform: "reddit"})
	}
	return out
}

func names(ss []config.Source) []string {
	out := make([]string, 0, len(ss))
	for _, s := range ss {
		out = append(out, s.Name)
	}
	return out
}

func TestSelectSourcesEmptyMeansAll(t *testing.T) {
	all := srcs("a", "b", "c")
	for _, only := range []string{"", "   ", ",", " , ,"} {
		got, err := selectSources(all, only)
		if err != nil {
			t.Fatalf("only=%q: unexpected error %v", only, err)
		}
		if len(got) != 3 {
			t.Fatalf("only=%q: want all 3, got %v", only, names(got))
		}
	}
}

// The console sends the names in whatever order the operator ticked them; the
// run must still walk sources in configured order so two identical selections
// fetch in the same sequence.
func TestSelectSourcesKeepsConfiguredOrder(t *testing.T) {
	got, err := selectSources(srcs("a", "b", "c", "d"), "d,b")
	if err != nil {
		t.Fatalf("unexpected error %v", err)
	}
	if strings.Join(names(got), ",") != "b,d" {
		t.Fatalf("want b,d got %v", names(got))
	}
}

func TestSelectSourcesTrimsAndDedupes(t *testing.T) {
	got, err := selectSources(srcs("a", "b"), " a , a ,b")
	if err != nil {
		t.Fatalf("unexpected error %v", err)
	}
	if strings.Join(names(got), ",") != "a,b" {
		t.Fatalf("want a,b got %v", names(got))
	}
}

// A typo must not silently produce a zero-source run: that is indistinguishable
// from "every selected source had nothing new", which is the exact confusion
// the console's run report exists to prevent.
func TestSelectSourcesUnknownNameIsAnError(t *testing.T) {
	_, err := selectSources(srcs("a", "b"), "a,typo,alsotypo")
	if err == nil {
		t.Fatal("want an error for unknown names")
	}
	for _, want := range []string{"typo", "alsotypo"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("error %q should name %q", err, want)
		}
	}
	if strings.Index(err.Error(), "alsotypo") > strings.Index(err.Error(), "typo,") {
		// sorted output keeps the message stable across map iteration order
		t.Logf("message: %v", err)
	}
}

func TestBudgetUncappedNeverSpends(t *testing.T) {
	b := &budget{}
	for i := 0; i < 100; i++ {
		b.add(50)
		if b.spent() {
			t.Fatal("a zero-limit budget must never report spent")
		}
	}
	var nilb *budget
	if nilb.spent() {
		t.Fatal("nil budget must not report spent")
	}
	nilb.add(5) // must not panic
}

func TestBudgetCommentCeiling(t *testing.T) {
	b := &budget{comments: 25}
	b.add(10)
	if b.spent() {
		t.Fatal("10 of 25 should not be spent")
	}
	b.add(20) // overshoots: a thread is taken whole
	if !b.spent() {
		t.Fatal("30 of 25 should be spent")
	}
	if b.usedComments != 30 || b.usedPosts != 2 {
		t.Fatalf("want 30 comments over 2 posts, got %d/%d", b.usedComments, b.usedPosts)
	}
}

// Posts and comments are separate ceilings: a run capped at 2 posts must stop
// after two threads even if they carried three comments between them.
func TestBudgetPostCeilingIsIndependent(t *testing.T) {
	b := &budget{posts: 2}
	b.add(1)
	if b.spent() {
		t.Fatal("1 post of 2 should not be spent")
	}
	b.add(2)
	if !b.spent() {
		t.Fatal("2 posts of 2 should be spent")
	}
	if b.usedComments != 3 {
		t.Fatalf("want 3 comments booked, got %d", b.usedComments)
	}
}

func TestBudgetEitherCeilingEndsTheRun(t *testing.T) {
	b := &budget{comments: 1000, posts: 1}
	b.add(1)
	if !b.spent() {
		t.Fatal("the post ceiling alone should end the run")
	}
	b = &budget{comments: 1, posts: 1000}
	b.add(5)
	if !b.spent() {
		t.Fatal("the comment ceiling alone should end the run")
	}
}
