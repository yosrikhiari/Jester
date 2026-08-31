// Package store implements the SQLite durable queue and result tables shared
// with the Python agent pipeline. The Go worker writes ingest_batch rows; the
// Python side reads them and writes nuggets/ideas/runs.
//
// schemaVersion in meta must equal config.SchemaVersion or both sides refuse to
// run (cross-language contract guard, §15 Checkpoint 1).
package store

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"jester/internal/config"
	"jester/internal/mediahash"
	"jester/internal/reddit"
)

// schema is the DDL for the tables Go OWNS under §25.3/D-17: meta,
// ingest_batch, ingested_comments. Python owns nuggets/ideas/runs/quota —
// creating them from here would poison shared databases with stale M1.0-era
// shapes (§37.20 Phase A), so this side no longer touches them.
const schema = `
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS ingest_batch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  source TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  batch_index INTEGER NOT NULL,
  -- What this batch carries: 'comment' or 'listing'. The fork exists so the
  -- Python extractor can claim comment batches and never see a listing — a
  -- listing is already structured and has nothing to gain from a language
  -- model. DEFAULT 'comment' keeps every row written before the fork correct.
  kind TEXT NOT NULL DEFAULT 'comment',
  comments TEXT NOT NULL,
  -- JSON array of listings, NULL on a comment batch. The comments column stays
  -- NOT NULL and carries '[]' here, so nothing that reads it learns about kinds.
  listings TEXT,
  -- The thread/video/topic the comments hang off, as JSON: title, author,
  -- score, upvote ratio, view and comment counts. A batch used to carry a
  -- bare thread_id, so nothing downstream could say what the discussion was
  -- even about.
  thread_meta TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  claimed_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ingested_comments (
  fingerprint TEXT PRIMARY KEY,
  thread_id TEXT,
  ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- What each thread looked like the last time it was actually fetched.
--
-- ingested_comments answers "have I stored this comment", which can only be
-- asked AFTER the comment is in hand — so a thread that has not moved since
-- last run still costs a full browser navigation, an expanded comment tree and
-- a slot in the post budget before anything can be discarded. One live run:
-- 249 posts fetched, 392 comments kept, page after page of "31 fetched, 0 kept
-- (29 already archived)".
--
-- last_seen_comments is the listing's own advertised count at the moment the
-- thread was last read. If the listing still advertises no more than that, the
-- thread has nothing new and never needs to be opened.
CREATE TABLE IF NOT EXISTS thread_state (
  platform TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  last_seen_comments INTEGER NOT NULL,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (platform, thread_id)
);
-- One property on one portal. Identity only: everything that CHANGES lives in
-- listing_observation, because a listing's value is mostly in how it moves.
CREATE TABLE IF NOT EXISTS listing (
  portal TEXT NOT NULL,
  listing_id TEXT NOT NULL,
  url TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (portal, listing_id)
);
-- One row per time the listing was actually read.
--
-- Rows, not fields: a listing row that is UPDATED in place answers "what does
-- this cost" and destroys "what did it cost in March", which is the question
-- worth having. Price history, days-on-market, relist detection and withdrawal
-- are all queries over this table rather than features built on top of it.
--
-- content_hash covers the normalised facts (price, size, rooms, status);
-- gallery_hash covers the ordered photo fingerprints. Two hashes rather than
-- one because "the price moved" and "the photographs were replaced" are
-- different events, and the interesting listings are the ones where both
-- happen in the same week.
CREATE TABLE IF NOT EXISTS listing_observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  portal TEXT NOT NULL,
  listing_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  run_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  gallery_hash TEXT,
  price INTEGER,
  currency TEXT,
  status TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_listing ON listing_observation(portal, listing_id, observed_at);
-- One photograph as it appeared in one observation.
--
-- phash is a 16-character dHash (see internal/mediahash), never the image. The
-- index on it is the whole point: "which listings anywhere share this
-- photograph" is how one property is recognised across two competing portals,
-- and it has to be a lookup rather than a scan.
CREATE TABLE IF NOT EXISTS listing_media (
  observation_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  url TEXT NOT NULL,
  phash TEXT,
  PRIMARY KEY (observation_id, position)
);
CREATE INDEX IF NOT EXISTS idx_media_phash ON listing_media(phash);
-- Band keys for near-match lookup. Eight rows per fingerprinted photograph.
--
-- Equality on phash finds almost nothing: real re-deliveries of one photograph
-- were measured 4 to 9 bits apart, so the same house on two portals does not
-- share a hash, it shares a NEIGHBOURHOOD. Two hashes closer than 8 bits must
-- have at least one identical byte-band, which turns "find near matches" from
-- a full scan into an index lookup. See mediahash.Hash.Bands.
CREATE TABLE IF NOT EXISTS listing_media_band (
  observation_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  band TEXT NOT NULL,
  PRIMARY KEY (observation_id, position, band)
);
CREATE INDEX IF NOT EXISTS idx_media_band ON listing_media_band(band);
-- When each curated source was last walked. Without this a run budget is
-- always spent on whatever sits first in sources.yaml: the scheduler visited
-- hn-ask's same two threads every 30 minutes and never reached the other
-- fifteen sources, so it re-fetched an exhausted thread forever.
CREATE TABLE IF NOT EXISTS source_state (
  name TEXT PRIMARY KEY,
  last_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queued INTEGER NOT NULL DEFAULT 0,
  -- CUMULATIVE yield, which is the number that can condemn a source.
  -- last_queued alone cannot: "visited five times, never produced anything"
  -- and "visited five times, produced plenty on the second" look identical
  -- through it. With 90 configured sources, a dead one silently burns a
  -- rotation slot every run and nobody can tell which.
  total_queued INTEGER NOT NULL DEFAULT 0,
  -- How many of those visits are actually COUNTED in total_queued. On a
  -- database written before total_queued existed the two disagree: the
  -- lifetime yield of those earlier visits was never recorded and cannot be
  -- recovered (ingest_batch.source holds a thread URL, not a source name, so
  -- there is nothing to join against). Reading "visits=9, total_queued=0" as
  -- a dead source would condemn every source in a database that has been
  -- working for weeks, so the health verdict counts measured visits only and
  -- an unmeasured history simply says nothing either way.
  measured_visits INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0
);
`

// addColumns are the columns added to Go-owned tables after the first release.
// Each must be safe to re-run: SQLite has no ADD COLUMN IF NOT EXISTS, so the
// duplicate error is the expected outcome on an up-to-date database.
var addColumns = []string{
	"ALTER TABLE ingest_batch ADD COLUMN thread_meta TEXT",
	"ALTER TABLE source_state ADD COLUMN total_queued INTEGER NOT NULL DEFAULT 0",
	"ALTER TABLE source_state ADD COLUMN measured_visits INTEGER NOT NULL DEFAULT 0",
	// The record-type fork. Everything written before this existed was a
	// comment batch, and DEFAULT 'comment' says so without a backfill.
	"ALTER TABLE ingest_batch ADD COLUMN kind TEXT NOT NULL DEFAULT 'comment'",
	// Listing payload. Nullable: a comment batch has none, and `comments`
	// stays NOT NULL so every existing reader keeps working untouched.
	"ALTER TABLE ingest_batch ADD COLUMN listings TEXT",
}

// isDuplicateColumn reports whether err is SQLite's "this column is already
// here" — the ONLY error an idempotent ALTER may ignore. Swallowing every
// error would hide a genuinely broken migration behind a working-looking
// startup.
func isDuplicateColumn(err error) bool {
	return err != nil && strings.Contains(strings.ToLower(err.Error()), "duplicate column name")
}

// Store wraps a *sql.DB for Jester.
type Store struct {
	db *sql.DB
}

// Open opens (creating if needed) the SQLite db at path and ensures schema and
// schema_version match config.SchemaVersion.
func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Rollback journal (DELETE), not WAL — matches the Python side. The DB is
	// shared with the Python pipeline across processes (sometimes containers)
	// over a bind-mounted volume where WAL's -wal/-shm sidecars cannot be
	// arbitrated reliably, surfacing as "unable to open database file". Forcing
	// DELETE here keeps both sides on one mode and drops the sidecars entirely;
	// busy_timeout absorbs brief writer locks.
	for _, pragma := range []string{
		"PRAGMA journal_mode=DELETE",
		"PRAGMA busy_timeout=5000",
	} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, fmt.Errorf("set %s: %w", pragma, err)
		}
	}
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("ensure schema: %w", err)
	}
	// CREATE TABLE IF NOT EXISTS never widens a table that already exists, so
	// a database written by an earlier build keeps its old shape and every
	// INSERT naming a new column fails on it. Python's store has carried an
	// idempotent ALTER list for exactly this reason; this side needed one too
	// the moment it grew a column. A duplicate-column error IS the success
	// case here, so it is swallowed and nothing else is.
	for _, stmt := range addColumns {
		if _, err := db.Exec(stmt); err != nil && !isDuplicateColumn(err) {
			db.Close()
			return nil, fmt.Errorf("%s: %w", stmt, err)
		}
	}
	s := &Store{db: db}
	if err := s.ensureVersion(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

func (s *Store) ensureVersion() error {
	want := fmt.Sprintf("%d", config.SchemaVersion)
	var have string
	err := s.db.QueryRow("SELECT value FROM meta WHERE key='schema_version'").Scan(&have)
	if err == sql.ErrNoRows {
		if _, err := s.db.Exec("INSERT INTO meta(key,value) VALUES('schema_version',?)", want); err != nil {
			return fmt.Errorf("set schema_version: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("read schema_version: %w", err)
	}
	if have != want {
		return fmt.Errorf("schema_version mismatch: db=%s code=%s", have, want)
	}
	return nil
}

// Version returns the stored schema_version.
func (s *Store) Version() (int, error) {
	var v string
	if err := s.db.QueryRow("SELECT value FROM meta WHERE key='schema_version'").Scan(&v); err != nil {
		return 0, err
	}
	var n int
	if _, err := fmt.Sscanf(v, "%d", &n); err != nil {
		return 0, err
	}
	return n, nil
}

// Batch is a unit of fetched comments enqueued for the Python pipeline.
type Batch struct {
	RunID    string
	Platform string
	Source   string
	ThreadID string
	Index    int
	Comments []Comment
	// Post is the thread / video / topic these comments came from. Nil when
	// the adapter could not read it — which is a real state, distinct from
	// "read it and it was empty", and is stored as SQL NULL.
	Post *reddit.FetchedPost
}

// Comment is one raw comment with its dedup fingerprint, serialised into
// ingest_batch.comments as the cross-language handoff (D-17).
//
// The top three keys — body, fingerprint, upvotes — are the ORIGINAL contract
// and keep their exact names and meanings, because the Python pipeline has
// always read those and a schema change is not worth a flag day. Everything
// the adapters know beyond them lives under `detail`.
//
// Nested, NOT embedded, and the distinction matters: with the adapter struct
// embedded, encoding/json resolves the two `upvotes` keys by depth and the
// shallower plain int64 wins. That silently flattens *upvotes = nil ("this
// platform does not publish a per-comment score" — every Hacker News comment)
// into `"upvotes": 0` ("nobody upvoted it"). The nesting keeps the nullable
// field addressable at detail.upvotes, where absent still means absent.
type Comment struct {
	Body        string `json:"body"`
	Fingerprint string `json:"fingerprint"`
	// Upvotes is the legacy engagement number: the platform's published score,
	// whatever that platform counts. It is NOT a claim that downvotes were
	// zero — see detail for what was actually published.
	Upvotes int64 `json:"upvotes"`
	// Detail is everything else the adapter captured: author, timestamp,
	// permalink, tree position, likes, replies, awards, flags.
	Detail reddit.FetchedComment `json:"detail"`
}

// NewComment builds the stored shape from what an adapter fetched.
//
// Detail.Body is deliberately cleared: it is already the top-level `body`, and
// a thread can carry 500 comments — duplicating every one of them would double
// the queue payload to say the same thing twice.
func NewComment(fp string, c reddit.FetchedComment) Comment {
	detail := c
	detail.Body = ""
	return Comment{
		Body:        c.Body,
		Fingerprint: fp,
		Upvotes:     c.Score,
		Detail:      detail,
	}
}

// EnqueueBatch inserts a pending ingest_batch row.
func (s *Store) EnqueueBatch(b Batch) (int64, error) {
	payload, err := json.Marshal(b.Comments)
	if err != nil {
		return 0, err
	}
	// NULL, not "{}" or "null", when there is no post: a reader can then tell
	// "the adapter never captured this" from "it captured an empty one".
	var meta any
	if b.Post != nil {
		raw, err := json.Marshal(b.Post)
		if err != nil {
			return 0, err
		}
		meta = string(raw)
	}
	res, err := s.db.Exec(
		`INSERT INTO ingest_batch(run_id,platform,source,thread_id,batch_index,comments,thread_meta,status)
		 VALUES(?,?,?,?,?,?,?,'pending')`,
		b.RunID, b.Platform, b.Source, b.ThreadID, b.Index, string(payload), meta)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// MarkIngested records a comment fingerprint in the skip-list.
func (s *Store) MarkIngested(fp string) error {
	_, err := s.db.Exec("INSERT OR IGNORE INTO ingested_comments(fingerprint) VALUES(?)", fp)
	return err
}

// AlreadyIngested reports whether fp is in the skip-list.
func (s *Store) AlreadyIngested(fp string) (bool, error) {
	var n int
	if err := s.db.QueryRow("SELECT count(1) FROM ingested_comments WHERE fingerprint=?", fp).Scan(&n); err != nil {
		return false, err
	}
	return n > 0, nil
}

// PendingBatches returns pending batch rows (used by contract/integration tests).
// Listing is one property as a portal published it at one moment.
//
// Price is a pointer because "not published" and "free" are different facts,
// and a portal that shows "price on application" must not be recorded as zero.
type Listing struct {
	Portal    string
	ListingID string
	URL       string
	Price     *int64
	Currency  string
	Status    string
	// Media is the gallery in published order. Each entry may carry a hash or
	// not: a URL recorded without a fingerprint is a real state, meaning the
	// image was seen but not fetched.
	Media []Media
	// Payload is the full normalised record as JSON — everything the adapter
	// read, including the fields this struct does not name.
	Payload string
}

// Media is one photograph's position, address and fingerprint.
type Media struct {
	Position int
	URL      string
	// PHash is a 16-character dHash, or "" when the image was not fetched.
	PHash string
}

// ContentHash fingerprints the facts that make this observation distinct from
// the last one. Deliberately excludes the gallery: a photo swap and a price cut
// are separate events and are hashed separately.
func (l Listing) ContentHash() string {
	price := "none"
	if l.Price != nil {
		price = fmt.Sprintf("%d", *l.Price)
	}
	return fingerprint(strings.Join([]string{price, l.Currency, l.Status, l.Payload}, "\x1f"))
}

// GalleryHash fingerprints the ordered photo set. Returns "" when nothing in
// the gallery was fingerprinted, so that "no photos hashed" never reads as a
// gallery that legitimately changed.
func (l Listing) GalleryHash() string {
	parts := make([]string, 0, len(l.Media))
	any := false
	for _, m := range l.Media {
		if m.PHash != "" {
			any = true
		}
		parts = append(parts, m.PHash)
	}
	if !any {
		return ""
	}
	return fingerprint(strings.Join(parts, ","))
}

func fingerprint(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:16])
}

// ObservationState is what the last visit recorded, for deciding whether this
// visit changed anything.
type ObservationState struct {
	ContentHash string
	GalleryHash string
	ObservedAt  string
}

// LastObservation returns the most recent observation of a listing.
//
// found=false means never seen, which is never grounds to skip a fetch.
func (s *Store) LastObservation(portal, listingID string) (ObservationState, bool, error) {
	var st ObservationState
	var gallery sql.NullString
	err := s.db.QueryRow(
		`SELECT content_hash, gallery_hash, observed_at FROM listing_observation
		 WHERE portal = ? AND listing_id = ? ORDER BY id DESC LIMIT 1`,
		portal, listingID,
	).Scan(&st.ContentHash, &gallery, &st.ObservedAt)
	if err == sql.ErrNoRows {
		return st, false, nil
	}
	if err != nil {
		return st, false, err
	}
	st.GalleryHash = gallery.String
	return st, true, nil
}

// RecordObservation appends one observation and its media, and updates the
// listing's identity row.
//
// ALWAYS APPENDS. An unchanged listing still gets a row, because "we looked on
// the 3rd and it had not moved" is evidence — it is what makes days-on-market
// a fact rather than an inference from gaps. Callers wanting to suppress
// duplicates should compare ContentHash against LastObservation and decide;
// this function does not decide for them.
func (s *Store) RecordObservation(runID string, l Listing) (int64, error) {
	tx, err := s.db.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()

	now := Now()
	if _, err := tx.Exec(
		`INSERT INTO listing (portal, listing_id, url, first_seen_at, last_seen_at)
		 VALUES (?, ?, ?, ?, ?)
		 ON CONFLICT(portal, listing_id) DO UPDATE SET
		   url = excluded.url, last_seen_at = excluded.last_seen_at`,
		l.Portal, l.ListingID, l.URL, now, now,
	); err != nil {
		return 0, fmt.Errorf("upsert listing: %w", err)
	}

	var price any
	if l.Price != nil {
		price = *l.Price
	}
	gallery := l.GalleryHash()
	var galleryVal any
	if gallery != "" {
		galleryVal = gallery
	}
	res, err := tx.Exec(
		`INSERT INTO listing_observation
		   (portal, listing_id, observed_at, run_id, content_hash, gallery_hash,
		    price, currency, status, payload)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		l.Portal, l.ListingID, now, runID, l.ContentHash(), galleryVal,
		price, l.Currency, l.Status, l.Payload,
	)
	if err != nil {
		return 0, fmt.Errorf("insert observation: %w", err)
	}
	obsID, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	for _, m := range l.Media {
		var ph any
		if m.PHash != "" {
			ph = m.PHash
		}
		if _, err := tx.Exec(
			`INSERT INTO listing_media (observation_id, position, url, phash)
			 VALUES (?, ?, ?, ?)`,
			obsID, m.Position, m.URL, ph,
		); err != nil {
			return 0, fmt.Errorf("insert media %d: %w", m.Position, err)
		}
		if m.PHash == "" {
			continue
		}
		h, err := mediahash.ParseHash(m.PHash)
		if err != nil {
			// A malformed fingerprint is a bug in the caller, not a reason to
			// lose the observation — the URL and position are still worth
			// keeping. It simply will not be findable by similarity.
			continue
		}
		for _, band := range h.Bands() {
			if _, err := tx.Exec(
				`INSERT OR IGNORE INTO listing_media_band (observation_id, position, band)
				 VALUES (?, ?, ?)`,
				obsID, m.Position, band,
			); err != nil {
				return 0, fmt.Errorf("insert band for media %d: %w", m.Position, err)
			}
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return obsID, nil
}

// PriceHistory returns a listing's observations oldest-first.
type PricePoint struct {
	ObservedAt  string
	Price       *int64
	Status      string
	ContentHash string
	GalleryHash string
}

// PriceHistory is the payoff for appending rather than updating: it exists
// because nothing was ever overwritten.
func (s *Store) PriceHistory(portal, listingID string) ([]PricePoint, error) {
	rows, err := s.db.Query(
		`SELECT observed_at, price, status, content_hash, gallery_hash
		 FROM listing_observation WHERE portal = ? AND listing_id = ? ORDER BY id`,
		portal, listingID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []PricePoint
	for rows.Next() {
		var p PricePoint
		var price sql.NullInt64
		var status, gallery sql.NullString
		if err := rows.Scan(&p.ObservedAt, &price, &status, &p.ContentHash, &gallery); err != nil {
			return nil, err
		}
		if price.Valid {
			v := price.Int64
			p.Price = &v
		}
		p.Status = status.String
		p.GalleryHash = gallery.String
		out = append(out, p)
	}
	return out, rows.Err()
}

// PhashMatch is one listing that published a given photograph.
type PhashMatch struct {
	Portal    string
	ListingID string
	URL       string
	Position  int
}

// MatchByPhash finds every listing that has published this photograph,
// optionally excluding one portal.
//
// NEAR, NOT EXACT — and that correction is the whole reason this method is
// written the way it is. It began as `WHERE phash = ?`, justified by synthetic
// fixtures in which every re-delivery of an image hashed identically. Real
// files disagreed: a photograph re-encoded at JPEG q40 moved 9 bits, and a
// 150px thumbnail moved 4. Equality would therefore have missed most genuine
// cross-portal matches while looking, from the tests, like it worked.
//
// Two steps. Retrieve candidates that share at least one byte-band (an index
// lookup, guaranteed to find everything within 7 bits — see
// mediahash.Hash.Bands), then confirm each one with a real distance
// comparison. The band is only ever a reason to look.
func (s *Store) MatchByPhash(phash, excludePortal string) ([]PhashMatch, error) {
	if phash == "" {
		return nil, nil
	}
	want, err := mediahash.ParseHash(phash)
	if err != nil {
		return nil, err
	}
	bands := want.Bands()
	args := make([]any, 0, len(bands)+1)
	for _, b := range bands {
		args = append(args, b)
	}
	args = append(args, excludePortal)

	rows, err := s.db.Query(
		`SELECT DISTINCT o.portal, o.listing_id, l.url, m.position, m.phash
		 FROM listing_media_band b
		 JOIN listing_media m
		   ON m.observation_id = b.observation_id AND m.position = b.position
		 JOIN listing_observation o ON o.id = m.observation_id
		 JOIN listing l ON l.portal = o.portal AND l.listing_id = o.listing_id
		 WHERE b.band IN (?,?,?,?,?,?,?,?) AND o.portal <> ?
		 ORDER BY o.portal, o.listing_id, m.position`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []PhashMatch
	for rows.Next() {
		var m PhashMatch
		var got sql.NullString
		if err := rows.Scan(&m.Portal, &m.ListingID, &m.URL, &m.Position, &got); err != nil {
			return nil, err
		}
		h, err := mediahash.ParseHash(got.String)
		if err != nil {
			continue
		}
		// The candidate shared a band; that is not yet a match.
		if mediahash.SameImage(want, h) {
			out = append(out, m)
		}
	}
	return out, rows.Err()
}

// ThreadUnchanged reports whether a thread can be skipped without fetching it:
// it has been read before, and the listing still advertises no more comments
// than it held then.
//
// advertised < 0 means the listing published no number, which is never grounds
// to skip — an absent count is not a zero (see reddit.Thread).
//
// The tradeoff, stated because it is real: a thread where one comment was
// deleted and one added shows an unchanged count and will be skipped. That
// costs one missed comment. The alternative — re-reading every exhausted
// thread forever — costs a browser navigation per thread per run, which is the
// scarcer resource by a wide margin on a single-session stealth browser.
func (s *Store) ThreadUnchanged(platform, threadID string, advertised int) (bool, error) {
	if advertised < 0 {
		return false, nil
	}
	var last int
	err := s.db.QueryRow(
		`SELECT last_seen_comments FROM thread_state WHERE platform = ? AND thread_id = ?`,
		platform, threadID,
	).Scan(&last)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return advertised <= last, nil
}

// MarkThreadRead records what the listing advertised at the moment the thread
// was fetched. Called after a fetch, never instead of one.
//
// An unpublished count (advertised < 0) is stored as 0 rather than -1: it must
// not later read as a ceiling that suppresses the next visit.
func (s *Store) MarkThreadRead(platform, threadID string, advertised int) error {
	if advertised < 0 {
		advertised = 0
	}
	_, err := s.db.Exec(
		`INSERT INTO thread_state (platform, thread_id, last_seen_comments, last_fetched_at)
		 VALUES (?, ?, ?, ?)
		 ON CONFLICT(platform, thread_id) DO UPDATE SET
		   last_seen_comments = excluded.last_seen_comments,
		   last_fetched_at    = excluded.last_fetched_at`,
		platform, threadID, advertised, Now(),
	)
	return err
}

func (s *Store) PendingBatches() (int, error) {
	var n int
	if err := s.db.QueryRow("SELECT count(1) FROM ingest_batch WHERE status='pending'").Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// Now is a stable clock helper.
func Now() string { return time.Now().UTC().Format(time.RFC3339) }

// TouchSource records that a source was walked, and what it yielded. Called
// for every source the run reaches — including one that returned nothing,
// because "visited and empty" is precisely what must push it to the back of
// the queue next time.
func (s *Store) TouchSource(name string, queued int) error {
	if strings.TrimSpace(name) == "" {
		return nil
	}
	_, err := s.db.Exec(`
INSERT INTO source_state(name, last_fetched_at, last_queued, total_queued, measured_visits, visits)
VALUES(?, datetime('now'), ?, ?, 1, 1)
ON CONFLICT(name) DO UPDATE SET
  last_fetched_at = datetime('now'),
  last_queued     = excluded.last_queued,
  total_queued    = source_state.total_queued + excluded.last_queued,
  measured_visits = source_state.measured_visits + 1,
  visits          = source_state.visits + 1`, name, queued, queued)
	return err
}

// SourceOrder returns the configured names sorted least-recently-fetched
// first, so a capped run spreads across ticks instead of re-walking the head
// of sources.yaml. Names never fetched sort first — a newly added source
// should be tried before one that was visited an hour ago.
//
// Ties keep the configured order, which keeps a run reproducible.
func (s *Store) SourceOrder(names []string) ([]string, error) {
	if len(names) < 2 {
		return names, nil
	}
	seen := map[string]string{}
	rows, err := s.db.Query("SELECT name, last_fetched_at FROM source_state")
	if err != nil {
		return nil, fmt.Errorf("source order: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var n, at string
		if err := rows.Scan(&n, &at); err != nil {
			return nil, fmt.Errorf("source order scan: %w", err)
		}
		seen[n] = at
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("source order rows: %w", err)
	}
	order := make([]string, len(names))
	copy(order, names)
	sort.SliceStable(order, func(i, j int) bool {
		ai, oki := seen[order[i]]
		aj, okj := seen[order[j]]
		if oki != okj {
			return !oki // never fetched goes first
		}
		if !oki {
			return false // both unseen: configured order stands
		}
		return ai < aj // older timestamp first
	})
	return order, nil
}

// SourceState is one row of source_state: when a curated source was last
// walked, what that visit yielded, what every visit has yielded, and how many
// times it has been walked at all.
type SourceState struct {
	LastFetchedAt string
	LastQueued    int
	TotalQueued   int
	// MeasuredVisits is the subset of Visits whose yield is included in
	// TotalQueued, and it is the one a health verdict may divide by.
	MeasuredVisits int
	Visits         int
}

// SourceStates is the whole table, for the console's Sources view.
func (s *Store) SourceStates() (map[string]SourceState, error) {
	out := map[string]SourceState{}
	rows, err := s.db.Query(
		"SELECT name, last_fetched_at, last_queued, total_queued, measured_visits, visits " +
			"FROM source_state")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var st SourceState
		var n string
		if err := rows.Scan(&n, &st.LastFetchedAt, &st.LastQueued, &st.TotalQueued,
			&st.MeasuredVisits, &st.Visits); err != nil {
			return nil, err
		}
		out[n] = st
	}
	return out, rows.Err()
}
