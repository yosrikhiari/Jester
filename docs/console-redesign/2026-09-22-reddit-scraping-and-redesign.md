# Jester — Reddit scraping (API + browser), saved posts, and the redesign

> **STATUS: NOT STARTED (2026-09-22) — plan v1. Two fixes already landed today (see §1); everything else is proposal.**
> Supersedes the Reddit parts of `2026-09-22-reddit-scraping-research.md` (which was written for Coppler). Answers from Yosri on 2026-09-22: Reddit's job is the app's main purpose (comments → nuggets → ideas); use **both** the official API and CloakBrowser; where saved posts live is still to be studied; comments-with-posts is an **option**; watch rules as proposed; stack revived; unrelated failures fixed; **redesign the whole console**, inspired by ayautomate.com.

---

## §0 One sentence

Make Reddit the reliable spine of Jester again (official API first, CloakBrowser only when the API can't), add "watch a subreddit and keep the posts that match" on top of the existing comment pipeline, and replace the dark "operator console" look with a light, airy, big-type interface in the style of ayautomate.com.

---

## §1 What happened today (done, verified)

| # | What | Proof |
|---|---|---|
| 1 | Brought `qdrant` + `cloakbrowser` containers back (`docker compose --profile live up -d qdrant cloakbrowser`). Console and Ollama stay native. | `:6333` answers (qdrant 1.19.0), `:9222/json/version` answers (Chrome/146). |
| 2 | Reddit ingestion works again. | `jester ingest --only selfhosted --max-posts 3` → 30 comments from 2 posts queued; 1 thread hit a Reddit block page → backoff (expected). Before today Reddit had been frozen at 935 comments since 2026-08-27 because cloakserve was down and every browser source was "skipped". |
| 3 | Groq 413 root cause fixed. 15 unclaimed groups were giant Hacker News threads (287–716 nuggets = 10k–26k tokens), over Groq free tier's 8k tokens/request. Every hourly cycle retried them, all fell back, 0 ideas. New knob `max_nuggets_per_idea: 40` splits oversized thread groups into chunks (trailing single row folds into the previous chunk so R50 never strands it). | `python/jester/config.py`, `agents/synthesizer.py` (`_chunk_groups`), `cli.py` summary line, `config/thresholds.yaml` + `config-live/`, §4.3.1 registry rows, 4 new tests in `tests/test_synth_idea_cap.py`. Full suite: 717 passed, 1 pre-existing unrelated failure (`scraper.yaml` `version` key — Go side). |
| 4 | Two other knobs that were live in code but missing from the §4.3.1 registry (`ingest_timeout_seconds`, `realestate_detail_per_page`) registered. | `test_config_registry.py` green except the pre-existing `version` one. |

Not committed. The verification `jester run --max-ideas 2` was killed by a session restart (shows as `mock-run · aborted` in Runs); the synthesizer chunking is proven by its 4 unit tests, and the next hourly cycle is the live proof.

### Redesign steps 1–4 + the review picks (done 2026-09-22, same day)

| # | What | Where |
|---|---|---|
| 5 | Console restyled in place: light theme (dark kept), Lexend self-hosted, side rail + top bar, plain page headers, skeleton first paint. | `console/static/{app.css,index.html,fonts/}`, `server.py` (.woff2) |
| 6 | Review picks implemented (`plans/2026-09-22-console-review.html`): infra cached 30 s; paged+sorted Ideas (50/page, status+resynth in drawer); Nuggets facet rail + 100/page; Queue split into comments / listings with a batch drawer; Runs compact signals + zero-yield reason + duration ratio; Overview 4 tiles with 7-day sparklines, headline sentence, quota meter, infra rows with consequences, weighted actions; Clusters sortable 3-column grid; Search facets + recent + similarity bars; Sources fetch-path/blocked column; Config knobs grouped by stage + map editor (fixes `[object Object]`); Schedule two columns; Doctor fix cards with one action each; run strip in the top bar; keyboard (`/`, `?`, `g`+letter); plain-language labels. | `console/api.py` (`trends`, `running`, `ideas(...)`, `nuggets_page`, `queue(kind=)`, `infra` cache), `server.py` routes, `static/app.js`, `tests/test_console_review_picks.py` (7 tests) |

Deferred from the review: the Posts page (needs the Reddit-watch backend, §2.3).

---

## §2 Reddit ingestion: API **and** browser

### 2.1 What exists

- Browser path: `go/internal/reddit/{live,dom,pace}.go` — cloakserve CDP, listing walk with `?after=`, `<shreddit-post>` / `<shreddit-comment>` DOM, ±30 % jitter, block-page regex, warm-up, `thread_state` skip. Solid, but fragile by nature (1 free concurrent session, challenges, ToS grey — `jester-setup.md §35.3` still unresolved).
- Routing: `needsBrowser(platform)` in `go/cmd/worker/main.go` decides per **platform**. Reddit = always browser.
- Sources: `config/sources.yaml`, 18 Reddit `kind: subreddit`, walked `/new/`, `max_threads_per_platform.reddit` = 3, `min_comments_per_thread: 8`.

### 2.2 The plan: API first, browser as fallback

Reddit's official Data API (OAuth2, free for personal/non-commercial use, 100 requests/min per app — verify the current terms when registering) gives everything the DOM scrape gives, as JSON, without fingerprints:

| Need | API call |
|---|---|
| listing | `GET /r/{sub}/new` (or `hot`, `top?t=week`) with `after=`/`before=` cursors, `limit=100` |
| thread + comments | `GET /comments/{id}?limit=500&depth=…&sort=top`; `morechildren` for collapsed trees |
| search inside a sub | `GET /r/{sub}/search?q=…&restrict_sr=1&sort=new` (this is what a keyword watch uses) |
| rate limit | headers `X-Ratelimit-Remaining` / `-Reset` |

**Changes (Go worker):**

| File | Change |
|---|---|
| `go/internal/reddit/api.go` (new) | `APIClient`: OAuth `client_credentials` (script app: client id + secret + username + password, or app-only for read-only public data), token refresh, `User-Agent: windows:jester:v<ver> (by /u/<name>)`, `ListThreads`, `FetchThread`, `Search`. Same return types (`[]Thread`, `[]FetchedComment`, `*FetchedPost`) as the browser path so `enqueueComments` is untouched. |
| `go/internal/reddit/route.go` (new) | `Fetcher` interface + `Auto` fetcher: try API; on 401/403 (private/quarantined), 429 after backoff, or missing credentials → browser. Records which path served each thread in `thread_meta.extra.fetched_via`. |
| `go/cmd/worker/main.go` | `needsBrowser` becomes per-**source**: `fetch: api \| browser \| auto` (default `auto`). Only spin up a cloakserve session when a source actually falls back. |
| `go/internal/config/config.go` + `python/jester/config.py` | new `scraper.yaml` keys `reddit_client_id`, `reddit_client_secret`, `reddit_username`, `reddit_user_agent` (env-substituted like `license_key` today) + `sources.yaml` `fetch` field. Both sides register them (the registry test enforces this). |
| `config/scraper.yaml`, `.env.example` | `${REDDIT_CLIENT_ID}` etc. |
| Console Doctor page | shows "Reddit API: token OK / missing creds / rate-limited" next to cloakserve. |

**Why both, not one:** the API is the fast, legal, cursor-exact path for public subs. The browser stays for what the API refuses (quarantined/NSFW without `over_18`, private subs you're a member of, or the day Reddit revokes the app) — and it's already written.

**Pacing:** API calls still go through `Pace(delay)` jitter; 60 req/min ceiling in the client (below the 100 limit); one token per app shared across sources.

### 2.3 Saved posts on top (the "watch" feature)

Today a Reddit *post* only survives as `thread_meta` JSON on a comment batch. The new feature keeps posts you care about as first-class rows.

**Config (`sources.yaml`, per Reddit source, all optional):**

```yaml
- platform: reddit
  name: selfhosted
  kind: subreddit
  url: https://www.reddit.com/r/selfhosted/
  fetch: auto            # api | browser | auto
  watch:                 # absent = comments-only, today's behaviour
    sort: new            # new | hot | top
    keywords: ["backup", "self-host*", "migrat*"]   # any match; * = prefix
    min_score: 10
    min_comments: 0
    flair: []            # empty = any
    include_comments: top   # none | top | all   (Yosri: "add this as an option")
    top_n: 5
    interval_minutes: 60
```

**Worker:** for a source with `watch`, one extra step per run: list (API `new`/`hot`/`top` or `search` when keywords are set), filter, write a `saved_post` row per match (dedup on `(platform, post_id)`), optionally attach `top_n` comments. Cursor `last_seen_fullname` per source in `source_state` so a run only looks at new posts. Runs inside the existing hourly cycle (no new scheduler) unless `interval_minutes` says otherwise, in which case the worker skips the source when it ran too recently.

**Table (`store.go` + `store.py`, both declare it):**

```
saved_post(platform, post_id PK, source_name, community, title, body, url, target_url,
           author, author_url, score, upvote_ratio, comment_count, flair, kind, created_at,
           captured_at, fetched_via, matched_keywords JSON, comments JSON NULL,
           status 'new'|'kept'|'dismissed', note TEXT NULL)
```

Where you **see** them is §3 (open).

---

## §3 Where saved posts live and are viewed — study

You asked to study this more before deciding. Three shapes:

| Option | What it is | Pros | Cons |
|---|---|---|---|
| **A. Console page "Posts"** | new page in the Python console (`/api/posts`, list + detail, keep/dismiss, note, search, filter by sub/keyword) — same place as Nuggets/Ideas | one app, one DB, uses the redesign; posts sit next to the nuggets they produced | one more page to design; console is vanilla JS today (see §4) |
| **B. Files** | worker writes `data/posts/<sub>/<date>-<id>.md` (front-matter + body + top comments) and a CSV in `data/exports/` | zero UI, greppable, Obsidian-friendly, survives any console rewrite | no keep/dismiss state, no dedup view, no link to ideas |
| **C. Push to Coppler** | worker (or a Python job) `POST /api/modules/coppler/capture/entries/ai-chat` with `sourceType: reddit` — Coppler becomes the library, Jester the collector | reuses Coppler's library/tags/drafts; the Coppler research doc already lists the payload | Coppler must be running; two apps to keep alive; the Coppler-side work (source type, badge) is parked in `2026-09-22-reddit-content-model.md` |

**Not mutually exclusive.** A stores; B is an export of A (one function); C is a later "send to Coppler" button on A. Leaning: **A + B now, C later** — but this stays OPEN until you say so (OPEN-1 below).

---

## §4 The redesign

### 4.1 What the reference actually is (measured on ayautomate.com, not guessed)

| Token | Value |
|---|---|
| Background | `#f8f5f6` warm off-white; sections alternate with a 50 % lighter wash; cards pure `#fff` |
| Ink | `#201d25` (headings), muted `#6b5e72`, soft `#8b7d92` |
| Border | `#e9e5e8`, 0.8 px, very quiet |
| Accent | lavender `#8082c1` (50→900 scale) — used **sparingly**: italic accent word in a heading, dot in an eyebrow badge, focus ring; one yellow-outlined primary CTA in the hero |
| Font | **Lexend** everywhere (geometric sans). h1 128 px / 700 / −2 % tracking; h2 72 px / 500 / −1.8 px; body 16 px |
| Radius | `--radius: .625rem` (10 px); cards 16 px; pills fully round |
| Spacing | section padding 80–128 px; lots of air; 12 px card padding |
| Shadows | essentially none — borders and background steps do the work |
| Extras | light/dark toggle; pixel-art illustration in the hero; small-caps eyebrow labels ("TRUSTED PARTNERS" with a purple dot); logo strip in white cards |

The feel: calm, high-contrast type, one accent, no gradients, no glow, no neon.

### 4.2 What Jester looks like now (and why it reads as "AI slop")

Dark navy `#0b1020`-ish, gold serif display font for "Jester", pink/gold accents, gradient header card, bordered stat tiles with monospace numbers, pink "danger" numbers. It's the default "dark dashboard" template look: three accent colors, decorative gradient, serif-on-dark. Tokens live in `python/jester/console/static/app.css` (795 lines), markup in `index.html` (529 lines, 11 pages), behaviour in `app.js` (2 194 lines, vanilla, one `fetch` wrapper). The React `frontend/` is a 29-line scaffold; `JESTER-FRONTEND-SYSTEM-DESIGN.md` specifies a (different, also dark) jester theme that was never built.

### 4.3 Two ways to do the redesign

| | **Option R1 — restyle the console in place** | **Option R2 — build the React frontend** |
|---|---|---|
| What | New `tokens.css` + rewrite `app.css`; restructure `index.html` layout (top bar instead of dark sidebar? or light sidebar); keep `app.js` behaviour, touch it only where markup changes | Implement `frontend/` (React 19 + Vite) against the existing `/api/*` (45 routes), re-themed to §4.1, retire `console/static` |
| Effort | 2–3 days for all 11 pages | 1–2 weeks (state, routing, every page from scratch, then parity testing) |
| Risk | markup coupling in `app.js` (query selectors) — mitigated by keeping ids/data attributes | parity gaps; two UIs during the transition; the scheduled cycle logs/console must keep working |
| Result | same app, new skin + layout; feels new if typography and spacing change, not just colors | cleaner long-term codebase, component model, easier "Posts" page |

**Recommendation: R1 now.** The complaint is the *look*, and the look is 100 % in `app.css` + layout in `index.html`. Do R1 with a real token system (so R2 can inherit it later), and build the new "Posts" page (§3-A) in the same pass so it's born in the new style. R2 only if, after R1, the vanilla JS becomes the bottleneck.

### 4.4 R1 design decisions (proposed)

- **Theme:** light by default, dark as a toggle (the reference has both). Warm off-white bg, near-black ink, **one** accent. Keep Jester's identity through the accent choice and the logo, not through gold/pink/serif.
- **Accent:** not ayautomate's lavender (that's their brand). Candidates: a muted jester-red `#c8453d`, or keep gold but as a single flat accent `#b8860b` on light. Pick one (OPEN-3).
- **Type:** Lexend (self-hosted `.woff2`, no CDN — the console runs offline). Page titles 40–48 px / 600 / −1.5 % tracking (not 128 px — this is a tool, not a landing page). Numbers in stat tiles: same font, tabular figures, not monospace.
- **Layout:** keep a left nav (11 pages need it) but light, borderless, 220 px; top bar with db/config chips and the run button; content max-width 1 200 px, 32 px gutters; section spacing 40 px.
- **Surfaces:** white cards, 16 px radius, 0.8 px border, no shadow, no gradient header. The gradient "Overview" banner goes; the page title becomes a plain big heading + one-line description.
- **Status color:** only for status — green/amber/red pills; never as decoration.
- **Illustration:** the existing Jester logo, small, in the nav — optional pixel-art hero on Overview later (the reference's charm is partly that image).
- **Motion:** none beyond 120 ms hover/focus transitions.

### 4.5 Redesign build order

| Step | What | Gate |
|---|---|---|
| 1 | `tokens.css` (light + dark), Lexend files, base reset | Overview renders in the new tokens with zero JS changes |
| 2 | Shell: nav, top bar, page header pattern | all 11 pages navigate; `app.js` selectors intact (`grep` the ids) |
| 3 | Primitives: card, stat tile, pill, table, form controls, dialog, banner, empty state, skeleton | one screenshot per primitive in `docs/img/` |
| 4 | Pages: Overview, Runs, Queue, Ideas, Nuggets, Clusters, Search, Sources, Config, Schedule, Doctor | every page visually checked at 1280 and 1024 wide; dark toggle works |
| 5 | New "Posts" page (§3-A) | list + detail + keep/dismiss against `/api/posts` |
| 6 | Remove dead CSS, run the Python console tests, screenshot for README | tests green; README screenshots replaced |

---

## §5 Build order (everything)

1. Commit today's fixes (§1) as one commit: "synthesizer: cap nuggets per idea (Groq 8k TPM)".
2. Reddit API client + `auto` routing (§2.2) — gate: `jester ingest --only selfhosted` fetches through the API with cloakserve **stopped**, then falls back with it running when a sub is forced `fetch: browser`.
3. `watch` config + `saved_post` table + worker step (§2.3) — gate: a keyword watch on r/selfhosted produces rows; re-run produces 0 duplicates; `include_comments: top` attaches 5 comments.
4. Redesign R1 steps 1–4 (§4.5).
5. Posts page + file export (§3-A + B) — gate: keep/dismiss persists; `data/posts/*.md` written.
6. Doctor page shows Reddit API state; `jester-setup.md §35.3` gets a decision written down: "(b′) API where possible, browser as fallback, throttle + block-rate monitoring".

Rough sizes: 2 ≈ 2 days, 3 ≈ 2 days, 4 ≈ 3 days, 5 ≈ 1–2 days.

---

## §6 OPEN — need Yosri

- **OPEN-1 (§3):** where saved posts live — A console page, B files, C push to Coppler, or A+B now / C later (my lean)?
- **OPEN-2:** Reddit app credentials. Registering a "script" app at reddit.com/prefs/apps needs your Reddit account; I'll wire `.env` keys and the client, you paste the id/secret. OK? (No credentials are typed by me.)
- **OPEN-3 (§4.4):** accent color for the new theme — muted red, flat gold, or ayautomate-style lavender?
- **OPEN-4 (§4.3):** R1 (restyle in place, recommended) or R2 (React rewrite)?
- **OPEN-5:** keep the left nav (11 pages) or go top-nav like the reference? Reference is a landing page; a tool with 11 pages usually wants a side nav. My pick: light side nav.
- **OPEN-6:** which subreddits + keywords for the first watch (needed for gate 3)?
- **OPEN-7:** `jester-setup.md §35.3` — do you accept the "(b′) API first, browser fallback" posture so I can close that open question in the doc?
