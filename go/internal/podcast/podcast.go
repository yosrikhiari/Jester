// Package podcast ingests podcast transcripts through the podcast: RSS
// namespace.
//
// WHY THIS SOURCE, and why it beat the item next to it in the plan. A7 (RSS
// changelogs and status pages) and A8 (podcast transcripts) were both filed as
// "later", with A8 marked the higher effort of the two. Measured, they invert:
//
//	CHANGELOG feeds carry almost no pain. Across github.blog/changelog, AWS
//	what's-new, Chrome releases and the Docker blog — 145 items — exactly one
//	genuinely announced a removal ("MinIO End of Life"). Status feeds are 100%
//	incidents by construction, but the body of one reads "we will be performing
//	scheduled maintenance in DUB": vendor-voice operations, not user pain. And
//	the pain a deprecation causes already enters this archive as complaints on
//	Hacker News and GitHub when people hit it, which is the user-voice version
//	of the same event.
//
//	TRANSCRIPTS carry a great deal. Talk Python publishes one for 559 of 559
//	episodes and Changelog for 777 of 1,013 — 1,336 episodes of practitioners
//	describing, at length and unprompted, what does not work. It is the same
//	kind of speech the forums hold, from people who were asked to elaborate.
//
// SANCTIONED. The transcript URL is published by the show itself, in its own
// feed, under the <podcast:transcript> tag whose entire purpose is to say
// "here is the machine-readable text of this episode". Reading it is the
// declared use. Same category as Hacker News via Algolia or Steam's store API,
// and deliberately not the category the Reddit adapter sits in.
//
// THE UNIT IS A SPEAKER TURN. An episode is one document of tens of thousands
// of words; a VTT cue is a five-second fragment. Neither is a thing somebody
// said. Consecutive cues from one speaker are merged back into the turn they
// were split from, which is the unit the rest of this pipeline is built
// around: one person, one point.
package podcast

import (
	"context"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"jester/internal/htmltext"
	"jester/internal/reddit"
)

// FetchedComment is the shared batch comment shape (one dialect, §37.21).
type FetchedComment = reddit.FetchedComment

// FetchedPost is the shared thread shape.
type FetchedPost = reddit.FetchedPost

// Getter is the injection seam: tests drive fixtures, production drives HTTP.
type Getter func(ctx context.Context, url string) ([]byte, error)

// maxTurnChars bounds one merged speaker turn.
//
// Not truncation — segmentation. A guest can talk for four minutes without
// pausing, and the pipeline's prefilter drops anything past prefilter_max_chars
// as too long, so an unbounded merge would build turns that are thrown away
// whole. Splitting at a budget keeps every word and costs only a boundary in
// the middle of a long answer.
const maxTurnChars = 1200

type Client struct {
	Get Getter
	// MaxEpisodes bounds one walk of a show.
	MaxEpisodes int
	// Delay paces the per-episode transcript fetches.
	Delay time.Duration
}

func New() *Client {
	return &Client{Get: httpGet, MaxEpisodes: 5, Delay: 500 * time.Millisecond}
}

func httpGet(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "jester/1.0 (local research tool)")
	client := &http.Client{Timeout: 45 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	// Transcripts are large — a two-hour episode is comfortably a megabyte —
	// so the cap is generous, but it IS a cap.
	body, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", u, resp.Status)
	}
	return body, nil
}

// ---- feed ------------------------------------------------------------------

type transcriptTag struct {
	URL      string `xml:"url,attr"`
	Type     string `xml:"type,attr"`
	Language string `xml:"language,attr"`
}

type feedItem struct {
	Title       string          `xml:"title"`
	Link        string          `xml:"link"`
	GUID        string          `xml:"guid"`
	PubDate     string          `xml:"pubDate"`
	Description string          `xml:"description"`
	Author      string          `xml:"author"`
	Episode     string          `xml:"episode"`
	Transcripts []transcriptTag `xml:"transcript"`
}

type rssFeed struct {
	Channel struct {
		Title       string     `xml:"title"`
		Link        string     `xml:"link"`
		Description string     `xml:"description"`
		Items       []feedItem `xml:"item"`
	} `xml:"channel"`
}

// Episode is one show episode with a machine-readable transcript.
type Episode struct {
	Title         string
	Link          string
	GUID          string
	Published     string
	Number        string
	TranscriptURL string
	// TranscriptType is the tag's own MIME type, which decides the parser.
	TranscriptType string
}

// Show is the feed itself.
type Show struct {
	Title string
	Link  string
}

// ListEpisodes reads a show's feed and returns its newest episodes that
// actually publish a transcript.
//
// An episode WITHOUT one is skipped rather than fetched-and-guessed: there is
// no transcript to read, and the audio is not something this pipeline can
// consume. The count of what was skipped is returned so the caller can say so
// rather than reporting a short walk as a complete one (R55).
func (c *Client) ListEpisodes(ctx context.Context, feedURL string) (Show, []Episode, int, error) {
	body, err := c.Get(ctx, feedURL)
	if err != nil {
		return Show{}, nil, 0, fmt.Errorf("podcast feed %s: %w", feedURL, err)
	}
	var feed rssFeed
	if err := xml.Unmarshal(body, &feed); err != nil {
		return Show{}, nil, 0, fmt.Errorf("podcast feed %s decode: %w", feedURL, err)
	}
	show := Show{
		Title: strings.TrimSpace(feed.Channel.Title),
		Link:  strings.TrimSpace(feed.Channel.Link),
	}
	var out []Episode
	skipped := 0
	for _, it := range feed.Channel.Items {
		t := pickTranscript(it.Transcripts)
		if t == nil {
			skipped++
			continue
		}
		out = append(out, Episode{
			Title:          strings.TrimSpace(it.Title),
			Link:           strings.TrimSpace(it.Link),
			GUID:           strings.TrimSpace(it.GUID),
			Published:      normalizeDate(it.PubDate),
			Number:         strings.TrimSpace(it.Episode),
			TranscriptURL:  strings.TrimSpace(t.URL),
			TranscriptType: strings.ToLower(strings.TrimSpace(t.Type)),
		})
		if c.MaxEpisodes > 0 && len(out) >= c.MaxEpisodes {
			break
		}
	}
	return show, out, skipped, nil
}

// pickTranscript prefers VTT, which carries speaker names in <v> tags and
// timestamps per cue. HTML is the fallback and loses the timings.
func pickTranscript(tags []transcriptTag) *transcriptTag {
	var html, other *transcriptTag
	for i := range tags {
		t := &tags[i]
		if strings.TrimSpace(t.URL) == "" {
			continue
		}
		// A show may publish several languages; this pipeline reads English
		// only, so a tag that names something else is not a candidate.
		if lang := strings.ToLower(t.Language); lang != "" && !strings.HasPrefix(lang, "en") {
			continue
		}
		switch {
		case strings.Contains(strings.ToLower(t.Type), "vtt"):
			return t
		case strings.Contains(strings.ToLower(t.Type), "html"):
			if html == nil {
				html = t
			}
		default:
			if other == nil {
				other = t
			}
		}
	}
	if html != nil {
		return html
	}
	return other
}

var pubDateFormats = []string{
	time.RFC1123Z, time.RFC1123, time.RFC822Z, time.RFC822, time.RFC3339,
	"Mon, 2 Jan 2006 15:04:05 -0700", "Mon, 2 Jan 2006 15:04:05 MST",
}

// normalizeDate renders an RSS pubDate as RFC3339 UTC, or "" when it cannot be
// parsed — an unparseable date is recorded as absent rather than as an epoch.
func normalizeDate(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	for _, f := range pubDateFormats {
		if t, err := time.Parse(f, raw); err == nil {
			return t.UTC().Format(time.RFC3339)
		}
	}
	return ""
}

// ---- transcripts -----------------------------------------------------------

// Turn is one uninterrupted stretch of one person speaking.
type Turn struct {
	Speaker string
	Text    string
	// At is the cue timestamp the turn starts on ("00:14:22"), when the format
	// carries one. HTML transcripts do not.
	At string
}

var (
	cueTimeRE = regexp.MustCompile(`^(\d{2}:\d{2}:\d{2})[.,]\d{3}\s*-->`)
	voiceRE   = regexp.MustCompile(`^<v\s+([^>]*?)>\s*`)
	tagRE     = regexp.MustCompile(`</?[^>]+>`)
)

// ParseVTT turns a WebVTT transcript into speaker turns.
//
// Consecutive cues from the same speaker are merged, because a cue is a
// five-second display fragment and not a thing anybody said — Talk Python
// splits a single sentence across two of them. Merging restores the turn and
// is what makes the result the same shape as a forum comment.
func ParseVTT(body string) []Turn {
	var turns []Turn
	var cur Turn
	flush := func() {
		text := strings.TrimSpace(cur.Text)
		if text != "" {
			cur.Text = text
			turns = append(turns, cur)
		}
		cur = Turn{}
	}
	pendingAt := ""
	for _, raw := range strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || line == "WEBVTT" || strings.HasPrefix(line, "NOTE") {
			continue
		}
		if m := cueTimeRE.FindStringSubmatch(line); m != nil {
			pendingAt = m[1]
			continue
		}
		// A bare cue identifier (a number or slug on its own line) carries no
		// text; skipping it keeps it out of the transcript body.
		if !strings.Contains(line, " ") && !strings.HasPrefix(line, "<v") {
			continue
		}
		speaker := ""
		if m := voiceRE.FindStringSubmatch(line); m != nil {
			speaker = strings.TrimSpace(m[1])
			line = voiceRE.ReplaceAllString(line, "")
		}
		line = strings.TrimSpace(tagRE.ReplaceAllString(line, ""))
		if line == "" {
			continue
		}
		// Only a change of SPEAKER starts a new turn here. The length budget
		// is applied afterwards, by splitTurn, because a cue boundary falls
		// wherever the caption happened to wrap — splitting on it produced
		// turns beginning "of extra samples and a simple back of the envelope
		// calculation", which is a fragment rather than a thing somebody said.
		if speaker != "" && speaker != cur.Speaker {
			flush()
		}
		if cur.Speaker == "" {
			cur.Speaker = speaker
			cur.At = pendingAt
		}
		if cur.Text != "" {
			cur.Text += " "
		}
		cur.Text += line
	}
	flush()
	return splitAll(turns)
}

// sentenceEnd finds the last sentence boundary at or before `limit`, or -1.
var sentenceEnd = regexp.MustCompile(`[.!?]["')\]]?\s`)

// splitTurn breaks one turn into pieces that fit the budget, preferring a
// sentence boundary and falling back to a word boundary.
//
// The budget exists so the pipeline's prefilter does not discard a long answer
// whole as too long. Where it cuts matters: a piece that begins mid-clause is
// a fragment, and the extractor downstream is being asked to read it as a
// statement. No text is dropped at any point — the remainder becomes the next
// piece.
func splitTurn(t Turn) []Turn {
	if len(t.Text) <= maxTurnChars {
		return []Turn{t}
	}
	var out []Turn
	rest := t.Text
	first := true
	for len(rest) > maxTurnChars {
		window := rest[:maxTurnChars]
		cut := -1
		if locs := sentenceEnd.FindAllStringIndex(window, -1); len(locs) > 0 {
			cut = locs[len(locs)-1][1]
		}
		if cut <= 0 {
			// No sentence ended in this window — a long unpunctuated stretch.
			// A word boundary is the next best thing; mid-word is the worst.
			if w := strings.LastIndex(window, " "); w > 0 {
				cut = w
			} else {
				cut = maxTurnChars
			}
		}
		piece := strings.TrimSpace(rest[:cut])
		if piece != "" {
			out = append(out, Turn{Speaker: t.Speaker, Text: piece, At: atFor(t, first)})
			first = false
		}
		rest = strings.TrimSpace(rest[cut:])
	}
	if rest != "" {
		out = append(out, Turn{Speaker: t.Speaker, Text: rest, At: atFor(t, first)})
	}
	return out
}

// atFor keeps the timestamp on the FIRST piece only. The later pieces happen
// somewhere after it and the transcript does not say where, so claiming the
// turn's start time for all of them would put the same timestamp on five
// minutes of speech.
func atFor(t Turn, first bool) string {
	if first {
		return t.At
	}
	return ""
}

func splitAll(turns []Turn) []Turn {
	out := make([]Turn, 0, len(turns))
	for _, t := range turns {
		out = append(out, splitTurn(t)...)
	}
	return out
}

// speakerHTMLRE matches a "Name:" prefix at the head of a paragraph.
var speakerHTMLRE = regexp.MustCompile(`^\s*\*?\*?([A-Z][\w.' -]{1,40})\*?\*?\s*:\s+`)

// citePairRE matches the shape the whole Changelog network publishes:
//
//	<cite>Justin Garisson:</cite>
//	<p>Hello, and welcome to Ship It...</p>
//
// Reading it structurally is the difference between turns and slices. Stripping
// the tags first — which is what this used to do — leaves the speaker alone on
// a line, where a "Name:" prefix cannot match because there is no body after
// the colon: the cite became a one-word turn the prefilter dropped, and every
// paragraph became a turn with NO speaker. Four shows publish this way
// (Changelog, Go Time, JS Party, Ship It!), so it is worth reading properly
// rather than approximately.
var citePairRE = regexp.MustCompile(`(?is)<cite[^>]*>(.*?)</cite>\s*<p[^>]*>(.*?)</p>`)

// ParseHTML turns an HTML transcript into speaker turns.
//
// Weaker than the VTT path in one respect that cannot be fixed: HTML carries no
// timings. Speakers ARE recoverable when the document declares them — either as
// cite/paragraph pairs or as a "Name:" prefix — and are left EMPTY when it does
// not. Guessing from position in the document would attribute words to people
// who did not say them.
func ParseHTML(body string) []Turn {
	// The structured form first, when the document actually has it.
	if pairs := citePairRE.FindAllStringSubmatch(body, -1); len(pairs) > 1 {
		var turns []Turn
		for _, m := range pairs {
			speaker := strings.TrimSuffix(
				strings.TrimSpace(htmltext.Plain(m[1])), ":")
			text := strings.TrimSpace(htmltext.Plain(m[2]))
			if text == "" {
				continue
			}
			turns = append(turns, splitTurn(Turn{
				Speaker: strings.TrimSpace(speaker), Text: text})...)
		}
		if len(turns) > 0 {
			return turns
		}
	}
	plain := htmltext.Plain(body)
	var turns []Turn
	// A line that is ONLY "Name:" is a heading for what follows, not a thing
	// anybody said. Carrying it forward keeps the attribution and keeps a
	// one-word turn out of the archive.
	pending := ""
	for _, para := range strings.Split(plain, "\n") {
		para = strings.TrimSpace(para)
		if para == "" {
			continue
		}
		if m := speakerHTMLRE.FindStringSubmatch(para + " "); m != nil &&
			strings.TrimSpace(speakerHTMLRE.ReplaceAllString(para+" ", "")) == "" {
			pending = strings.TrimSpace(m[1])
			continue
		}
		speaker := pending
		pending = ""
		if m := speakerHTMLRE.FindStringSubmatch(para); m != nil {
			speaker = strings.TrimSpace(m[1])
			para = strings.TrimSpace(speakerHTMLRE.ReplaceAllString(para, ""))
		}
		turns = append(turns, splitTurn(Turn{Speaker: speaker, Text: para})...)
	}
	return turns
}

// FetchEpisode reads one episode's transcript and returns it as comments, with
// the episode as the post they hang off.
func (c *Client) FetchEpisode(ctx context.Context, show Show, ep Episode) ([]FetchedComment, *FetchedPost, error) {
	if strings.TrimSpace(ep.TranscriptURL) == "" {
		return nil, nil, fmt.Errorf("episode %q publishes no transcript", ep.Title)
	}
	body, err := c.Get(ctx, ep.TranscriptURL)
	if err != nil {
		return nil, nil, fmt.Errorf("transcript %s: %w", ep.TranscriptURL, err)
	}
	text := string(body)
	var turns []Turn
	// Trust the tag's declared type, but fall back on the content itself: a
	// feed that mislabels its own transcript is a real thing, and a WEBVTT
	// header is unambiguous.
	if strings.Contains(ep.TranscriptType, "vtt") || strings.HasPrefix(strings.TrimSpace(text), "WEBVTT") {
		turns = ParseVTT(text)
	} else {
		turns = ParseHTML(text)
	}
	if len(turns) == 0 {
		return nil, nil, fmt.Errorf("transcript %s parsed to nothing", ep.TranscriptURL)
	}

	id := ep.GUID
	if id == "" {
		id = ep.Link
	}
	post := &FetchedPost{
		ID:           id,
		Title:        ep.Title,
		URL:          ep.Link,
		Kind:         "episode",
		CreatedAt:    ep.Published,
		Community:    show.Title,
		CommunityURL: show.Link,
		CommentCount: reddit.I64(int64(len(turns))),
	}
	if ep.Number != "" {
		post.Extra = map[string]any{"episode": ep.Number}
	}

	out := make([]FetchedComment, 0, len(turns))
	for i, t := range turns {
		cm := FetchedComment{
			// Position in the episode, so the id is stable across re-fetches
			// of the same transcript — the fingerprint is built from it.
			ID:         fmt.Sprintf("%s#%d", id, i),
			PlatformID: fmt.Sprintf("%s#%d", id, i),
			Body:       t.Text,
			Author:     t.Speaker,
			CreatedAt:  ep.Published,
			Permalink:  ep.Link,
			// A transcript is a sequence, not a tree. Depth 0 is the truth
			// here rather than a default nobody filled in.
			Depth: 0,
		}
		if t.At != "" {
			cm.Extra = map[string]any{"at": t.At}
		}
		out = append(out, cm)
	}
	return out, post, nil
}
