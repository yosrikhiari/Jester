# Reddit ⇄ LinkedIn — how a saved post should be stored so it can go both ways

> **STATUS: PARKED (2026-09-22) — moved from the Coppler monorepo to Jester; this file is about Coppler's storage model (Reddit ⇄ LinkedIn repurposing, library badges), which is not reddit scraping. Whole body commented out, kept for when Coppler work resumes.**
> Companion to `2026-09-22-reddit-scraping-research.md`. That file says *how to get Reddit in*. This file says *how to store it so a Reddit idea becomes a LinkedIn post, a LinkedIn idea becomes a Reddit post, and the library always shows where each thing came from and where it went*.

---

<!-- COMMENTED OUT — entire content-model design is Coppler-specific, not reddit scraping

## §0 One sentence

Today Coppler stores "where it came from" and "where it is going" as two hard-coded enums (`EntrySourceType`, `DraftFormat`) that 22 files know about; the fix is to make the **idea** the centre (platform-free), attach a typed **origin** to it, generate drafts for a typed **target**, and drive both from **one platform registry** — then Reddit ⇄ LinkedIn is the same code path in both directions and the badge is free.

---

## §1 What exists today (the honest picture)

### 1.1 Two enums carry all platform knowledge

| Enum | Values | Meaning it is trying to carry |
|---|---|---|
| `EntrySourceType` (Capture) | `AiChat`, `Manual`, `Voice`, `LinkedInPost` | **two things at once**: the *kind* of thing (a chat, a note, a recording, a social post) **and** the *platform* (LinkedIn). ChatGPT/Claude/Gemini are all just `AiChat`; the platform is only in the JSON bag. |
| `DraftFormat` (Content) | `LinkedInPost`, `TweetX`, `ShortReflection` | the *target*. Each value = one prompt file (`linkedin-post-v2.md`, `tweet-x-v2.md`, `short-reflection-v2.md`). |

Adding Reddit "the LinkedIn way" means `EntrySourceType.RedditPost` **and** `DraftFormat.RedditPost` **and** updating every map that lists these values.

### 1.2 Where the platform list is copied (measured, not guessed)

`grep` for `LinkedInPost` / `"linkedin"` outside tests: **22 files** across the three codebases.

| Codebase | Files |
|---|---|
| Backend | `SourceType.cs`, `CreateEntryFromAiChatCommand.cs` (the `"linkedin"` string → enum `if`), `EntryCreatedHandler.cs` (title job only for LinkedIn), `GenerateEntryTitleJob.cs`, `DraftFormat.cs`, `GenerateDraftCommand.cs`, `PromptLoader.cs` |
| Web app | `library/types.ts`, `entries/types.ts`, `LibraryFilters.tsx`, `LibraryRow.tsx` (label + icon maps), `GenerateDraftModal.tsx`, `StudioOverlay.tsx` (3 maps: icon, i18n key, short label), `DraftPreviewCard.tsx`, `EntryDraftsTab.tsx`, `ConnectionBanner.tsx`, `ScheduleNewPostDialog.tsx` |
| Extension | `messages.ts` (`CopplerHost` union), `popup/state.ts` (host switch), `post-buttons.ts`, `repurpose-panel.ts` (`sourceType: "linkedin"` literal), `save-external-draft.ts` |

This is the thing that makes "refactor Reddit → LinkedIn and back" feel hard: the platform is not a *value*, it is a *branch* in 22 places.

### 1.3 What the library shows today

- `LibraryRow.tsx`: one icon + one label from `EntrySourceType` (`Bot` for AiChat, `MessageSquare` for LinkedIn). It cannot tell ChatGPT from Claude. It shows nothing about where the entry *went* (drafts / published posts).
- `EntryDetailPage.tsx`: parses `sourceMetadata` only for `tags` and `mediaUrls`. Author, permalink, community are in the JSON but not displayed as a proper "origin" block.
- `EntryDraftsTab.tsx`: lists drafts with a format label — that is the only "where it went" signal, and only inside the detail page.

### 1.4 What "repurpose" actually is (two implementations)

| Where | What it does | Target awareness |
|---|---|---|
| **Web** — `StudioOverlay.tsx` → `POST /content/drafts` → `GenerateDraftCommand` | picks a `DraftFormat`, loads the prompt file for it, wraps `StructuredContent` in `<entry>` delimiters, calls Grok via `IAiCompletion`, stores a `ContentDraft` (format, prompt version, tokens, cost) | target = `DraftFormat` enum |
| **Extension** — `repurpose-panel.ts` (4 500 lines) → `shared/services/repurpose.ts` | on LinkedIn: takes the post text + a **tone** (professional/casual/…) + a **style preset** (Hormozi, Welsh, …), does few-shot retrieval with embeddings, calls the provider adapters, then `COPPLER_SAVE_ENTRY` + `SaveExternalDraftCommand(EntryId, DraftFormat.LinkedInPost, Content)` | target is **assumed LinkedIn** everywhere |

So: the *input* side is already platform-free (a draft is generated from `StructuredContent`, never from the raw LinkedIn HTML). The *output* side is LinkedIn-shaped by assumption. That is good news — the idea layer already exists; it is just not named.

### 1.5 Publishing (where it went, for real)

`Coppler.Publishing`: `ScheduledPost` (text, media, `ScheduledAtUtc`, `Status`, `ProviderPostUrn`) with `SocialPlatform` enum (`LinkedIn` only). A `ScheduledPost` **does not reference the `Entry` or `ContentDraft` it came from** — so today the library cannot say "this idea was published to LinkedIn on Tuesday". That link is missing for any platform, not just Reddit.

---

## §2 The idea in one picture

```
              ORIGIN (typed)                   IDEA (platform-free)                 TARGETS (typed)
  ┌────────────────────────────┐        ┌──────────────────────────┐        ┌──────────────────────────────┐
  │ platform: reddit           │        │ Entry                    │        │ ContentDraft                 │
  │ kind: SocialPost           │ ─────▶ │  Title                   │ ─────▶ │  target: linkedin / post     │
  │ externalId: t3_abc123      │        │  StructuredContent (md)  │        │  content, prompt version…    │
  │ url: reddit.com/r/…        │        │  Status, Tags            │        ├──────────────────────────────┤
  │ author: u/someone          │        │                          │ ─────▶ │  target: reddit / post       │
  │ community: r/selfhosted    │        │                          │        │  (title + body)              │
  │ score: 342, flair: Guide   │        └──────────────────────────┘        └──────────────┬───────────────┘
  └────────────────────────────┘                                                           │ publish
                                                                                           ▼
                                                                                  ScheduledPost (linkedin | reddit)
                                                                                  ProviderPostUrn / permalink

  Library row badge:   [reddit icon] r/selfhosted  →  [linkedin icon ✓]  [reddit icon ○]
                        ^ origin                        ^ targets: ✓ published, ○ draft only
```

Reddit → LinkedIn and LinkedIn → Reddit are then **the same operation**: `origin.platform` is whatever it is, the idea is the idea, and you pick a target from the registry. Nothing in the flow branches on "is this from Reddit".

---

## §3 Three options

### Option 1 — Keep the enums, add values (the way LinkedIn was added)

**What changes**
- `EntrySourceType` += `RedditPost`. `DraftFormat` += `RedditPost` + prompt `reddit-post-v1.md`.
- Extension `CopplerHost` += `"reddit"`; backend `if (sourceStr == "reddit")` next to the LinkedIn one.
- Every map in §1.2 gets one more line (≈ 22 files, 1–3 lines each).
- Library badge: one more icon in `SOURCE_ICONS`.

**Reddit → LinkedIn:** open the entry, Studio, pick "LinkedIn". Works today already for any entry.
**LinkedIn → Reddit:** pick "Reddit post" in Studio (new format). Draft = title + body. Copy-paste to Reddit (no publisher).

**Pros:** smallest diff, no migration, no risk to existing rows, done in a day.
**Cons:** platform stays a branch, not a value. Next platform (HN? X capture? YouTube?) = 22 files again. `EntrySourceType` keeps mixing kind and platform (ChatGPT vs Claude still invisible). Badge can only show origin, never "where it went". Library cannot filter "everything from Reddit that already became a LinkedIn draft".

### Option 2 — Idea + typed Origin + Platform registry  ← **recommended**

**What changes**

*Backend (`Coppler.Capture`)*
- `Entry` gets typed origin columns (all nullable except kind):

  | Column | Type | Example |
  |---|---|---|
  | `SourceKind` | enum `AiChat \| Manual \| Voice \| SocialPost` | `SocialPost` |
  | `SourcePlatform` | string (registry key) | `"reddit"`, `"linkedin"`, `"chatgpt"` |
  | `ExternalId` | string | `"t3_abc123"`, LinkedIn activity URN |
  | `SourceUrl` | string | permalink |
  | `AuthorName`, `AuthorUrl` | string | `u/someone` |
  | `Community` | string | `r/selfhosted`, LinkedIn group / company page |
  | `CapturedAtUtc` | datetime | |
  | `SourceMetadata` | jsonb (**kept**) | long tail: score, flair, mediaUrls, numComments |

  Unique index `(OrganizationId, UserId, SourcePlatform, ExternalId)` where `ExternalId` is not null → dedup for free, for every platform.
- `EntrySourceType` → renamed/migrated to `SourceKind` (`LinkedInPost` rows become `SocialPost` + `SourcePlatform = "linkedin"`; `AiChat` rows get `SourcePlatform` from their JSON `host`). One EF migration with a small data backfill.
- `CreateEntryFromAiChatCommand` → `CreateCapturedEntryCommand` with an explicit `Origin` DTO; the `"linkedin"` string sniffing disappears. Same command used by the extension **and** by the future Reddit poller job.
- `Platforms.cs` (Capture.Domain): `static class Platforms { public const string Reddit = "reddit"; … }` + a small `PlatformInfo` record (`Key`, `DisplayName`, `CanCapture`, `CanPublish`, `DefaultDraftFormat`). One owner on the backend.

*Backend (`Coppler.Content`)*
- `DraftFormat` enum → `TargetPlatform` (registry key) + `Format` (string: `post`, `tweet`, `reflection`, `comment`). Prompt files renamed `<platform>-<format>-vN.md` (`linkedin-post-v2.md` already fits; add `reddit-post-v1.md`, keep `tweet-x-v2.md` as `x-post-v2.md` or leave as alias). `ContentDraft` stores both. `PromptLoader` resolves by `(platform, format)` — no enum switch.
- `ScheduledPost` gets nullable `EntryId` + `ContentDraftId` so "where it went" is a real link (data-integrity rule: one owner — the link lives on the publishing side, the Entry never navigates to it).

*Frontend (`frontend/src/shared/platforms.ts` — one TS owner for web **and** extension, because the extension already aliases `frontend/src/shared`)*
- `PLATFORMS: Record<PlatformKey, { label, icon, color token, canCapture, canPublish, formats[] }>`.
- `LibraryRow`: origin badge = `PLATFORMS[entry.sourcePlatform].icon` + `community`; targets strip = one small icon per distinct draft target, filled when a `ScheduledPost` for it is `Published`. Both from one `EntryListItem` DTO extension (`sourcePlatform`, `community`, `targets: [{ platform, status }]` — computed DB-side in `ListEntriesQuery`, no N+1).
- `LibraryFilters`: filter by **platform** and by **kind** (two chips, both from the registry), plus "has draft for → X".
- `EntryDetailPage`: one generic `OriginCard` (platform icon, community, author link, score if present, "open on <platform>"). Same component renders LinkedIn and Reddit.
- `StudioOverlay` / `GenerateDraftModal`: format picker built from `PLATFORMS[*].formats` instead of the three hard-coded maps.

*Extension*
- `CopplerHost` union → `PlatformKey` imported from the shared registry. `EntryDraft.sourceType` → `origin: { platform, externalId, url, authorName, authorUrl, community }`. `repurpose-panel.ts` stops assuming LinkedIn as the target: it reads the **current tab's platform** as the *default* target and lets the user switch (Reddit tab → default target Reddit, but "make this a LinkedIn post" is one click).

**Reddit → LinkedIn:** save on reddit.com (origin = reddit) → Studio → target LinkedIn → schedule via Publishing. Row badge: `reddit → linkedin ✓`.
**LinkedIn → Reddit:** save on linkedin.com (origin = linkedin) → Studio → target Reddit (`reddit-post-v1.md` produces a title + body, community-etiquette voice, no marketing hooks) → copy-paste, or publish once a Reddit publisher exists (`submit` scope, `ISocialPublisher` for `SocialPlatform.Reddit`). Row badge: `linkedin → reddit ○/✓`.

**Pros:** platform becomes a value; new platform = registry entry + selector file + prompt file (≈ 3–5 files, not 22). Badge shows origin **and** destination. Dedup, filtering and the origin card are generic. `Entry` stays the single aggregate → every module rule in `AGENTS.md` still holds, no new cross-module seam. Reddit poller (00-research §5) reuses the same command.
**Cons:** one migration with backfill (touches every existing entry — needs a dry run against a copy of the dev DB); a rename sweep across the 22 files (mechanical, one PR, tests catch misses); `DraftFormat` change is a public DTO change → `compliance-check` + `deep-review` required by the ship rules.

### Option 3 — Idea with many Sources (full graph)

**What changes**
- New table `entry_source` (Capture): `EntryId`, `Platform`, `ExternalId`, `Url`, `Author…`, `Community`, `SnapshotText`, `CapturedAtUtc`. An idea can have **N** sources (a Reddit thread + the ChatGPT chat you had about it + the LinkedIn post that inspired it).
- `Entry` keeps only idea fields. `ContentDraft` + `ScheduledPost` as in Option 2.
- UI: "Attach source" on the detail page, merge two entries into one idea, badge strip shows every origin platform.

**Pros:** most truthful model; "one idea, many inputs, many outputs" is what a content pipeline really is; Option 2's origin columns are literally this table's row moved inline, so Option 2 → 3 is a later mechanical lift.
**Cons:** ~2× the work of Option 2 (new entity, new configuration, merge/attach UI, cascade on delete/undelete, list query joins). Until you actually merge sources, every entry has exactly one source row and the extra table buys nothing visible.

---

## §4 Comparison

| | Option 1 | Option 2 | Option 3 |
|---|---|---|---|
| Files touched to add Reddit | ~22 | ~22 once (refactor) then ~5 | ~30+ |
| Files touched for the platform after Reddit | ~22 | ~5 | ~5 |
| Migration | none | 1 (add columns + backfill) | 1 (new table + move data) |
| Badge: origin | platform icon | platform icon + community | platform icon(s) |
| Badge: destination | no | yes (`→ linkedin ✓`) | yes |
| Filter "from Reddit, already a LinkedIn draft" | no | yes | yes |
| Dedup across manual + auto save | ad-hoc | unique index | unique index |
| Distinguish ChatGPT vs Claude in library | no | yes | yes |
| Multiple sources per idea | no | no | yes |
| Fits `AGENTS.md` module rules | yes | yes (Entry still one aggregate) | yes, more seams |
| Effort | 1 day | 2–3 days refactor + Reddit 1–2 days | 1–2 weeks |

## §5 Recommendation

**Option 2.** It is the smallest change that makes the two directions symmetric and gives the badge you described, it fixes the 22-file scatter once, and it does not break the module rules. Option 1 is faster today but you would pay the same refactor at the next platform, with more rows to backfill. Option 3 is the right end state only if "merge several sources into one idea" becomes a real habit — and Option 2 is the first half of it anyway.

## §6 Build order (Option 2)

| Step | What | Gate |
|---|---|---|
| 1 | Backend: `Platforms.cs`, `SourceKind`, origin columns, migration + backfill, unique index; `CreateCapturedEntryCommand`; keep old endpoint path as an alias for one release | migration dry-run on a DB copy: every existing row has `SourcePlatform` filled; Capture tests green |
| 2 | Backend: `ContentDraft` (`TargetPlatform`, `Format`), `PromptLoader` by pair, `reddit-post-v1.md`; `ScheduledPost.EntryId/ContentDraftId` | generate a draft for each existing format still works; new Reddit format returns title + body |
| 3 | `ListEntriesQuery` returns `sourcePlatform`, `community`, `targets[]` (DB-side) | no N+1 (check the SQL log) |
| 4 | `frontend/src/shared/platforms.ts` registry; web maps replaced (`LibraryRow`, `LibraryFilters`, `StudioOverlay`, `GenerateDraftModal`, `EntryDraftsTab`, `DraftPreviewCard`); `OriginCard` | LinkedIn entries look the same as before; badges show origin + targets |
| 5 | Extension: `origin` in `EntryDraft`, `PlatformKey` from the registry, repurpose panel target picker defaulting to the current tab | LinkedIn capture still works end-to-end (tests in `tests/e2e`) |
| 6 | Reddit capture (00-research §4) on top | gates A-1…A-4 |
| 7 | `compliance-check` + `deep-review` (DTO change, migration, delete cascade) | CRITICAL/HIGH fixed |

## §7 OPEN — I need you on these

- **OPEN-A. Reddit as a real publish target, or copy-paste only?** Real posting needs a subreddit picker + title + Reddit's `submit` OAuth scope + a `RedditPublisher : ISocialPublisher`. Copy-paste needs only the prompt file. My pick: copy-paste in V1, publisher in V2 (it slots into Publishing without touching Capture/Content).
- **OPEN-B. Badge content.** Origin only, or origin + targets strip (`reddit → linkedin ✓`)? My pick: both; the strip is what makes "did I already turn this into a LinkedIn post?" answerable at a glance.
- **OPEN-C. Migration style.** One-shot (rename `SourceType` → `SourceKind`, backfill, drop) vs. two-step (add new columns, keep `SourceType` one release, then drop). My pick: one-shot — this is a dev DB and the Coppler branch is the only consumer.
- **OPEN-D. Extension tone/style presets** (`stylePresets.ts`: Hormozi, Welsh…) are LinkedIn creators. Keep them global, or make presets per target platform in the registry (Reddit gets "helpful commenter", "OP update", …)? My pick: per platform, defined in the same registry so the picker is data-driven.
- **OPEN-E. Should the auto-scraper's entries (00-research Option C) land as `Status = Draft` like everything else, or a new `Inbox` status so the library can separate "I saved this" from "the bot saved this"?** My pick: same `Draft` status + `sourceMetadata.mode = "auto"` + a filter chip; no new status.

-->

