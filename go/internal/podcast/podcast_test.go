package podcast

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func read(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatalf("fixture %s: %v", name, err)
	}
	return b
}

func routed(t *testing.T, feed, transcript []byte) *Client {
	t.Helper()
	c := New()
	c.Delay = 0
	c.Get = func(_ context.Context, u string) ([]byte, error) {
		switch {
		case strings.HasSuffix(u, ".vtt"), strings.Contains(u, "transcript"):
			if transcript == nil {
				return nil, fmt.Errorf("no transcript fixture")
			}
			return transcript, nil
		default:
			return feed, nil
		}
	}
	return c
}

// ── the feed ─────────────────────────────────────────────────────────────────

func TestListEpisodesFindsTheTranscriptTag(t *testing.T) {
	c := routed(t, read(t, "feed.xml"), nil)
	show, eps, skipped, err := c.ListEpisodes(context.Background(), "https://talkpython.fm/episodes/rss")
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if show.Title == "" {
		t.Error("the show title is the community these turns belong to")
	}
	if len(eps) == 0 {
		t.Fatal("the fixture holds episodes with transcripts")
	}
	for _, e := range eps {
		if e.TranscriptURL == "" {
			t.Errorf("episode %q was listed without a transcript", e.Title)
		}
		if e.Title == "" {
			t.Error("an episode needs a title; it becomes the post")
		}
		if e.Published == "" {
			t.Errorf("episode %q lost its date", e.Title)
		}
	}
	_ = skipped
}

func TestEpisodesWithoutATranscriptAreSkippedAndCounted(t *testing.T) {
	// There is no transcript to read and the audio is not something this
	// pipeline can consume, so skipping is right — but a short walk reported
	// as a complete one is exactly what R55 forbids.
	feed := []byte(`<?xml version="1.0"?><rss xmlns:podcast="https://podcastindex.org/namespace/1.0">
	  <channel><title>Show</title><link>https://example.invalid</link>
	    <item><title>No transcript</title><link>https://example.invalid/1</link></item>
	    <item><title>Has one</title><link>https://example.invalid/2</link>
	      <podcast:transcript url="https://example.invalid/2.vtt" type="text/vtt"/></item>
	  </channel></rss>`)
	c := routed(t, feed, nil)
	_, eps, skipped, err := c.ListEpisodes(context.Background(), "https://example.invalid/feed")
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(eps) != 1 || eps[0].Title != "Has one" {
		t.Fatalf("want only the episode with a transcript, got %+v", eps)
	}
	if skipped != 1 {
		t.Fatalf("want 1 reported as skipped, got %d", skipped)
	}
}

func TestVTTIsPreferredOverHTML(t *testing.T) {
	// VTT carries speaker names and timings; HTML carries neither.
	tags := []transcriptTag{
		{URL: "a.html", Type: "text/html"},
		{URL: "b.vtt", Type: "text/vtt"},
	}
	if got := pickTranscript(tags); got == nil || got.URL != "b.vtt" {
		t.Fatalf("want the VTT, got %+v", got)
	}
	// HTML is still better than nothing.
	if got := pickTranscript(tags[:1]); got == nil || got.URL != "a.html" {
		t.Fatalf("want the HTML fallback, got %+v", got)
	}
	if pickTranscript(nil) != nil {
		t.Error("no tags means no transcript")
	}
}

func TestANonEnglishTranscriptIsNotChosen(t *testing.T) {
	// The prefilter, extractor and embeddings are all English, so a Spanish
	// transcript is real signal this stack cannot read.
	tags := []transcriptTag{{URL: "es.vtt", Type: "text/vtt", Language: "es"}}
	if got := pickTranscript(tags); got != nil {
		t.Fatalf("a Spanish transcript must not be picked, got %+v", got)
	}
	tags = append(tags, transcriptTag{URL: "en.vtt", Type: "text/vtt", Language: "en-US"})
	if got := pickTranscript(tags); got == nil || got.URL != "en.vtt" {
		t.Fatalf("want the English one, got %+v", got)
	}
}

func TestAnUnparseableDateIsAbsentNotEpoch(t *testing.T) {
	if got := normalizeDate("not a date"); got != "" {
		t.Errorf("want empty, got %q — 1970 is a date the feed never gave", got)
	}
	if got := normalizeDate(""); got != "" {
		t.Errorf("want empty, got %q", got)
	}
	if got := normalizeDate("Tue, 26 Aug 2026 10:00:00 +0000"); !strings.HasPrefix(got, "2026-08-26") {
		t.Errorf("want an RFC3339 date, got %q", got)
	}
}

// ── the transcript ───────────────────────────────────────────────────────────

func TestVTTCuesAreMergedBackIntoSpeakerTurns(t *testing.T) {
	// A cue is a five-second display fragment, not a thing anybody said —
	// Talk Python splits single sentences across two of them. Merging is what
	// makes the result the same shape as a forum comment.
	turns := ParseVTT(string(read(t, "episode.vtt")))
	if len(turns) == 0 {
		t.Fatal("the fixture is a real transcript")
	}
	cues := strings.Count(string(read(t, "episode.vtt")), "-->")
	if len(turns) >= cues {
		t.Fatalf("merging did nothing: %d turns from %d cues", len(turns), cues)
	}
	named := 0
	for _, tn := range turns {
		if tn.Text == "" {
			t.Fatal("an empty turn should never be emitted")
		}
		if len(tn.Text) > maxTurnChars+200 {
			t.Fatalf("turn of %d chars blows past the budget: %.60q", len(tn.Text), tn.Text)
		}
		if strings.Contains(tn.Text, "-->") || strings.Contains(tn.Text, "<v ") {
			t.Fatalf("cue markup leaked into the text: %.80q", tn.Text)
		}
		if tn.Speaker != "" {
			named++
		}
	}
	if named == 0 {
		t.Fatal("this transcript names its speakers in <v> tags; none survived")
	}
}

func TestATurnIsSplitNotTruncated(t *testing.T) {
	// A guest can talk for four minutes without pausing. The budget exists so
	// the prefilter does not throw the whole answer away as too long — so the
	// text has to continue in the next turn, not stop.
	var b strings.Builder
	b.WriteString("WEBVTT\n\n")
	word := "everything about this deployment pipeline is broken "
	for i := 0; i < 60; i++ {
		fmt.Fprintf(&b, "00:00:%02d.000 --> 00:00:%02d.000\n<v Guest>%s\n\n", i, i+1, word)
	}
	turns := ParseVTT(b.String())
	if len(turns) < 2 {
		t.Fatalf("a long monologue must split, got %d turn(s)", len(turns))
	}
	total := 0
	for _, tn := range turns {
		if tn.Speaker != "Guest" {
			t.Errorf("a split turn keeps its speaker, got %q", tn.Speaker)
		}
		total += strings.Count(tn.Text, "broken")
	}
	if total != 60 {
		t.Fatalf("words were lost: %d of 60 survived", total)
	}
}

func TestASpeakerChangeStartsANewTurn(t *testing.T) {
	vtt := "WEBVTT\n\n" +
		"00:00:01.000 --> 00:00:02.000\n<v Michael>So what went wrong there.\n\n" +
		"00:00:02.000 --> 00:00:03.000\n<v Sean>The migration silently dropped every index.\n\n" +
		"00:00:03.000 --> 00:00:04.000\n<v Sean>We found out in production.\n\n"
	turns := ParseVTT(vtt)
	if len(turns) != 2 {
		t.Fatalf("want 2 turns (one each), got %d: %+v", len(turns), turns)
	}
	if turns[0].Speaker != "Michael" || turns[1].Speaker != "Sean" {
		t.Fatalf("speakers wrong: %+v", turns)
	}
	if !strings.Contains(turns[1].Text, "dropped every index") ||
		!strings.Contains(turns[1].Text, "in production") {
		t.Fatalf("consecutive cues from one speaker must merge: %q", turns[1].Text)
	}
	if turns[0].At != "00:00:01" {
		t.Errorf("the turn should carry its start time, got %q", turns[0].At)
	}
}

func TestHTMLTranscriptsNeverGuessASpeaker(t *testing.T) {
	// HTML transcripts declare no speakers. Recovering one from a "Name:"
	// prefix is reading what is there; inferring it from position would
	// attribute words to people who did not say them.
	turns := ParseHTML("<p>Jerod: The build broke on every Windows runner.</p>" +
		"<p>And nobody noticed for a week.</p>")
	if len(turns) != 2 {
		t.Fatalf("want 2 paragraphs, got %d", len(turns))
	}
	if turns[0].Speaker != "Jerod" {
		t.Errorf("a declared speaker should be read, got %q", turns[0].Speaker)
	}
	if turns[1].Speaker != "" {
		t.Errorf("an undeclared speaker must stay empty, got %q", turns[1].Speaker)
	}
	if strings.Contains(turns[0].Text, "Jerod:") {
		t.Errorf("the prefix should be stripped from the body: %q", turns[0].Text)
	}
}

// ── mapping ──────────────────────────────────────────────────────────────────

func TestFetchEpisodeMapsOntoTheSharedShape(t *testing.T) {
	c := routed(t, read(t, "feed.xml"), read(t, "episode.vtt"))
	show, eps, _, err := c.ListEpisodes(context.Background(), "https://talkpython.fm/episodes/rss")
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	comments, post, err := c.FetchEpisode(context.Background(), show, eps[0])
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if post == nil || post.Title == "" {
		t.Fatal("the episode is the post these turns hang off")
	}
	if post.Community != show.Title {
		t.Errorf("community should be the show, got %q", post.Community)
	}
	seen := map[string]bool{}
	for _, cm := range comments {
		if cm.ID == "" || cm.Body == "" {
			t.Fatalf("a mapped turn needs an id and a body: %+v", cm)
		}
		if seen[cm.ID] {
			t.Fatalf("duplicate id %s — the fingerprint is built from it", cm.ID)
		}
		seen[cm.ID] = true
		if cm.Depth != 0 {
			t.Errorf("a transcript is a sequence, not a tree: depth %d", cm.Depth)
		}
	}
}

func TestAnEpisodeWithNoTranscriptIsAnError(t *testing.T) {
	c := routed(t, nil, nil)
	_, _, err := c.FetchEpisode(context.Background(), Show{}, Episode{Title: "x"})
	if err == nil {
		t.Fatal("no transcript URL must be refused, not fetched blindly")
	}
}

func TestAMislabelledTranscriptIsStillParsedCorrectly(t *testing.T) {
	// A feed that mislabels its own transcript is a real thing, and a WEBVTT
	// header is unambiguous — trusting the tag alone would run the HTML
	// parser over cue timings and emit them as speech.
	c := routed(t, nil, read(t, "episode.vtt"))
	ep := Episode{Title: "x", GUID: "g", TranscriptURL: "https://example.invalid/transcript",
		TranscriptType: "text/html"}
	comments, _, err := c.FetchEpisode(context.Background(), Show{Title: "S"}, ep)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	for _, cm := range comments {
		if strings.Contains(cm.Body, "-->") {
			t.Fatalf("cue timings reached the body: %.80q", cm.Body)
		}
	}
}

// ── where a long turn is cut ─────────────────────────────────────────────────

// The budget exists so the prefilter does not discard a long answer whole.
// WHERE it cuts matters: splitting on cue boundaries produced turns beginning
// "of extra samples and a simple back of the envelope calculation" — a
// fragment the extractor downstream is then asked to read as a statement.
func TestALongTurnIsCutAtASentenceBoundary(t *testing.T) {
	sentence := "The migration silently dropped every index in production. "
	var b strings.Builder
	b.WriteString("WEBVTT\n\n")
	// Cues that wrap mid-sentence, which is what a real caption file does.
	words := strings.Fields(strings.Repeat(sentence, 60))
	for i := 0; i < len(words); i += 4 {
		end := i + 4
		if end > len(words) {
			end = len(words)
		}
		fmt.Fprintf(&b, "00:00:%02d.000 --> 00:00:%02d.000\n<v Guest>%s\n\n",
			i%60, (i+1)%60, strings.Join(words[i:end], " "))
	}
	turns := ParseVTT(b.String())
	if len(turns) < 2 {
		t.Fatalf("want several pieces, got %d", len(turns))
	}
	for i, tn := range turns[:len(turns)-1] {
		// Every piece but the last should end where a sentence ends.
		if !strings.HasSuffix(strings.TrimSpace(tn.Text), ".") {
			t.Errorf("piece %d ends mid-sentence: %.60q", i, tn.Text)
		}
		if !strings.HasPrefix(tn.Text, "The migration") {
			t.Errorf("piece %d starts mid-sentence: %.60q", i, tn.Text)
		}
	}
}

func TestOnlyTheFirstPieceKeepsTheTimestamp(t *testing.T) {
	// The later pieces happen somewhere after it and the transcript does not
	// say where. Repeating the start time would stamp five minutes of speech
	// with one moment.
	long := Turn{Speaker: "Guest", At: "00:04:11",
		Text: strings.Repeat("Deployment broke again for the third time. ", 60)}
	pieces := splitTurn(long)
	if len(pieces) < 2 {
		t.Fatalf("want a split, got %d", len(pieces))
	}
	if pieces[0].At != "00:04:11" {
		t.Errorf("the first piece should keep the turn's start, got %q", pieces[0].At)
	}
	for i, p := range pieces[1:] {
		if p.At != "" {
			t.Errorf("piece %d claims a time the transcript never gave: %q", i+1, p.At)
		}
	}
}

func TestAnUnpunctuatedStretchFallsBackToAWordBoundary(t *testing.T) {
	// Auto-generated transcripts sometimes carry no punctuation at all. A
	// mid-word cut is the one outcome worth avoiding.
	pieces := splitTurn(Turn{Speaker: "X", Text: strings.Repeat("everything breaks constantly ", 100)})
	if len(pieces) < 2 {
		t.Fatal("want a split")
	}
	for i, p := range pieces {
		for _, frag := range []string{"everythin ", "break ", "constant "} {
			if strings.Contains(p.Text+" ", frag) {
				t.Errorf("piece %d cut mid-word near %q: %.50q", i, frag, p.Text)
			}
		}
	}
}

func TestSplittingNeverLosesText(t *testing.T) {
	original := strings.TrimSpace(strings.Repeat("The build fails on Windows only. ", 200))
	pieces := splitTurn(Turn{Speaker: "X", Text: original})
	rejoined := ""
	for _, p := range pieces {
		if rejoined != "" {
			rejoined += " "
		}
		rejoined += p.Text
	}
	if strings.Join(strings.Fields(rejoined), " ") != strings.Join(strings.Fields(original), " ") {
		t.Fatalf("text changed across the split: %d chars in, %d out", len(original), len(rejoined))
	}
}
