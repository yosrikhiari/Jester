# UI branch evaluation — 2026-10-25

**Branch:** `feat/ui-corporate-redesign` at `771d3d5` (67 commits ahead of `46cbd27`).
**Decision:** MERGE AFTER FIXES — two blocking defects fixed on the branch, everything
else verified, then integrated.
**Evidence:** local test runs, headless-Chromium comparison of both consoles on one
seeded archive, token/contrast measurement, and the market + literature reviews in
`docs/ui-restyle-literature-review.md` (abstracts only; paywalled full texts).

## What was measured

| Check | `main` (`46cbd27`) | branch (`771d3d5`) | verdict |
|---|---|---|---|
| Python suite (excl. E2E) | 1541 passed, 0 failed | 1665 passed, 0 failed | +124 new tests, none broken |
| E2E journeys (`test_e2e_changes.py`) | 5 passed | 5 passed | no regression |
| JS page errors, 12 pages | none | none | clean |
| Rows above the fold, Runs @1440x900 | 9 | 9 | no density loss |
| Rows above the fold, Ideas @1440x900 | 5 | 6 (row 113px → 95px) | slightly denser |
| Light-theme text contrast failures | 6–19 per page (3.56:1 worst) | 13 per page (1.85:1 worst) | **regression → fixed** |
| Dark-theme text contrast failures | 0–13 per page | 0 (5 on Schedule, 4.42:1) | improvement |
| Skip link / first Tab target | none (brand link) | "Skip to content" | improvement |
| Pointer targets < 24×24 px | 0 | 0 | pass |
| New runtime dependencies | — | none (packaging list only) | no build step added |

## Blocking defects found and fixed

1. **Light-theme navigation was unreadable.** `.nav button` used `var(--ink-2)`, which is
   `#44444C` in the light palette, on a rail that is `#17171B` in *both* themes: **1.85:1**.
   Inactive labels for Runs/Queue/Ideas/… could not be read (WCAG 1.4.3, 4.5:1 required).
   Fixed by using `var(--nav-muted)` → **6.42:1**. Verified by measurement and screenshot.
2. **The Nuggets card feed collapsed.** `.ncard` carried `min-height:0`, which zeroed the
   grid item's automatic minimum: every row resolved to 26px while cards needed 150–186px,
   with `overflow:visible`, so card text painted over its neighbours. All 100 rendered cards
   overflowed. Fixed by removing `min-height:0` → cards 164px, **0 overflow, 0 overlap**.

Both fixes are two lines of CSS in `python/jester/console/static/app.css`; no markup, JS or
API change.

## Deliberately not changed

- **Form-control borders** are 1.31:1 in the branch and 1.32:1 on `main` — a pre-existing
  WCAG 1.4.11 gap, not a regression. Tracked as follow-up, not a merge blocker.
- **Mojibake in the runs table's last column** (`â€¹`) exists on `main` too. Pre-existing.
- **Chat, scheduler, specs, maintenance** all ship disabled or human-gated
  (`chat_enabled:false`, `scheduler_enabled:false`, proposals applied by a human,
  maintenance flags only, edit log append-only). They are inert until switched on, and
  their 124 tests pass.

## Evidence from outside the repo

- Market review: side nav and a light default are both conventional in this category
  (Grafana dark-by-default with a side menu; Airflow/Dagster top nav; Prefect added a
  sidebar); every comparable is a compiled SPA, so keeping the no-build console is the
  differentiator, not a compromise.
- Literature: light-on-dark polarity advantage replicates (Buchner & Baumgartner 2007;
  Dobres et al. 2016); aesthetics improve *perceived* usability, not measured task speed
  (Tractinsky 2000), and the two Sonderegger/Sauer papers disagree on direction — so a SUS
  gain after this restyle must not be reported as a speed gain. Density has no evidence
  favouring whitespace for expert table scanning (Tullis 1983), which is why the rows-above-
  the-fold check was a gate rather than a preference.
