package config

import "testing"

// The per-source knobs are read by the worker, but they belong to this
// package, so they are pinned here too: an unset knob must mean the global
// behaviour, never a zero value that silently changes it.

func TestWantsPostsOnlyIsOptIn(t *testing.T) {
	var s Source
	if s.WantsPostsOnly() {
		t.Fatal("an unset posts_only must keep the comments")
	}
	no, yes := false, true
	s.PostsOnly = &no
	if s.WantsPostsOnly() {
		t.Fatal("posts_only: false must keep the comments")
	}
	s.PostsOnly = &yes
	if !s.WantsPostsOnly() {
		t.Fatal("posts_only: true must store the post instead")
	}
}

func TestMinCommentsOrPrefersTheSourceFloor(t *testing.T) {
	var s Source
	if got := s.MinCommentsOr(8); got != 8 {
		t.Fatalf("unset floor: got %d, want the global 8", got)
	}
	zero := 0
	s.MinComments = &zero
	if got := s.MinCommentsOr(8); got != 0 {
		t.Fatalf("an explicit 0 is a real floor, not unset: got %d", got)
	}
}
