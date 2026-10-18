# Console adaptation — kept-real deviations & exclusions

Design-of-record: `2026-10-17-console-adaptation-mockup.html` (rev 4, `06da16a`).
The mockup defines look/feel; the real console's behavior and data set the
limits of what may be shown. This ledger records every deliberate divergence
so the record outlives the chat that made it.

## Deliberately kept real (equivalent feature, different presentation)

- **Detail model — slide-over drawer at all widths** (decided 2026-10-18,
  user answer: "Keep drawer"). Runs, ideas and batch details open in the
  shared `#drawer`. The mockup's wide-screen split panes (`#rsplit/#qsplit/
  #isplit` + docked `.detail`) stay mockup-only; its ≤1100px drawer mode is
  already what the real console does everywhere. Rev-3 pieces that were
  portable (fixed-height runs page, internal ledger scroll, phase chips,
  models under `details`, focus restore on close, hidden scrollbars) landed
  in `d0be92e` regardless.
- **232px rail** vs mockup 236px — the real token wins.
- **Real nav vocabulary**: "Health" group as a real group, lowercase
  "the collector", real `PAGES` registry — not the mockup's nav list.
- **Real status vocabulary** `queued/running/done/failed/requeued` on runs,
  not the mock's `run/ok/bad` pill set.
- **Real table columns**, including the `overall` score column and `.ratio`
  duration bars; lowercase sunk headers (all from the 2026-09-22 redesign).
- **Topbar breadcrumb omitted** — deliberately skipped; addable later with one
  small `app.js` line if wanted.
- **Sources group band**: the whole band is clickable (mockup toggles from the
  chevron only); global sticky thead kept, per-group repeated column heads
  skipped; count stays in the card-head pill (no `tfoot` duplication).
- **No status-toned phase pills in run detail**: `phase_checkpoints` carries
  only a name and a timestamp. Rev-3's `ok/running/queued` tone would be a
  fabricated reading, so phases render as name·time chips.
- **Dark theme `--accent-ink`**: real console keeps its dark-ink-on-accent
  choice; the mockup's white-on-`#E0645C` is lower contrast. One token,
  flip-ready if wanted.
- **Posts detail model — in-page view swap** (2026-10-18). The real posts
  page exchanges the list for `#post-detail` (`#posts-list` hidden), not a
  side drawer. Rev-4's `#psplit` split pane and its ≤1100px drawer mode stay
  mockup-only; what was portable landed in `b1195e6`/`6e53cdb` (fixed-height
  posts page, detail as the scroll region with the back button pinned,
  list/tablewrap internal scroll, ≤900px fallbacks, seg buttons unsquashable).
  The focus rule ported with it: opening a post focuses `#posts-back` (the
  detail's persistent exit, standing in for the mock's `drawer-only` close),
  and the "‹ all posts" button hands focus back to the ledger row, so the
  rows gained `tabindex="0"` plus Enter/Space activation.
- **Runs/ideas rows are still not tabbable** (pre-existing). Their drawer
  focus restore is therefore a mouse-only no-op there; only the posts rows
  got the full keyboard path now. Extending `tabindex` to the run/idea
  ledgers is a one-line follow-up if wanted.

## Excluded from the mockups (would misrepresent, need `api.py`, or are mock scaffolding)

### Data the real API does not provide (would need `api.py`, out of boundary)
- **Sources**: "Watching for" keyword chips, 5-dot yield rating, per-visit
  history subrows — `/api/sources` has no keywords, no rating, only visit
  counts.
- **Rail "N new" posts badge / Reddit-watches Overview panel** —
  `COUNTS.posts` is total-only and set on posts-page load; "new" is not
  representable without an API change.
- **Posts state & subreddit filter groups** (`#pstate`/`#psub` since rev 3) —
  New/Kept/Dismissed is not a distinction the archive makes, and post
  pagination is server-side, so a subreddit facet built client-side from the
  current 50-row page would fake a picture of the whole archive.

### Mock-only scaffolding (would pretend the app does something it doesn't)
- **Sources pager** — the real list is complete and unpaginated.
- **Per-group "+" add buttons** in band rows — skipped (nested buttons are
  invalid inside the shared `groupRow()` helper; the add form is one panel
  away). Revisit if the helper grows an optional trailing slot.
- **Run-detail per-run requeue button** — no per-run endpoint; the global
  "requeue failed" action exists and stays as-is.
- **`dempty` / `dfoot`** — parts of the split-pane detail model, which the
  drawer decision above moots.
- **Posts Escape/backdrop close (rev 4)** — the real post detail is not a
  modal: no scrim, no fixed overlay, an always-visible back button. Escape
  already routes to the shared drawer helpfully enough; adding a second
  dismiss channel for an in-page swap buys nothing.

### Excluded by user decision ("Exclude all", rev 1)
- Accent switcher, Undo-on-removal toasts, "Add note" and "Send to Coppler"
  buttons, "Resynthesize all" header button, export-kept-as-`.md` placement.

Items in the first bucket can be revisited if `api.py` changes are explicitly
authorized; everything else needs an explicit revert of the relevant decision.
