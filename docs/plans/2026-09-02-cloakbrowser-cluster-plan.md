# CloakBrowser Cluster for Real Estate Scraping

> **Status:** Active — plan approved, implementation in progress
> **Decision:** Use v146 free binary (no license key) — the v146 build ships without fingerprinting restrictions and works without a license for single-concurrency use. Newer builds require a license key. We pin `CLOAKBROWSER_VERSION=v146` and leave `CLOAKBROWSER_LICENSE_KEY` empty.
> **Gate:** [Decision Gate 1] Cluster must run at least one stealth Chrome process per real estate portal without a license key.

## Why CloakBrowser for Real Estate

Real estate portals (Tayara, Mubawab, PrivateProperty, Property24, Houni, TunisieAnnonce) render listing pages with Next.js `__NEXT_DATA__`, JSF/PrimeFaces, or server-rendered HTML that is incomplete without JS execution. The existing `fetchPage` HTTP GET in `fetchRealEstateListings` returns raw HTML without the `__NEXT_DATA__` JSON payload that carries listing data. CloakBrowser provides a real Chromium process that executes JS and exposes the fully rendered DOM via `cloakserve` CDP.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  cloakbrowser (v146, no license)                     │   │
│  │    ├─ port 9222: CDP endpoint                        │   │
│  │    ├─ /cbdp/cloakserve: WS endpoint                 │   │
│  │    └─ /cbdp/health: health check                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                             │                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  go/cmd/worker (M1.1 live mode)                      │   │
│  │    ├─ cloakbrowser.NewWithOptions(seed=...)          │   │
│  │    ├─ NewSession() → one Chrome process per portal   │   │
│  │    └─ FetchListings() via CDP                        │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Files to Create / Modify

### 1. `docker-compose.yml` — CloakBrowser service (v146, no license)

- Pin `CLOAKBROWSER_VERSION=v146`
- Leave `CLOAKBROWSER_LICENSE_KEY` empty (v146 free tier)
- Expose port 9222 for CDP
- Mount `/tmp/cloakbrowser` for fingerprint cache
- Keep `profiles: ["live"]`
- Health check on `/cbdp/health`

### 2. `go/internal/realestate/` — new package

Replicates the `reddit/` package pattern:

- `realestate.go` — `FetchListingList()`, `FetchListingDetail()`, `Fingerprint()`
- `live.go` — cloakserve session navigation, `__NEXT_DATA__` extraction
- `fetched.go` — `FetchedListing`, `FencedListing`, `Fingerprint` structs
- `dom.go` — SSR DOM extraction helpers (reuse `parseHTML`, `findNode`, `attr`)
- `pace.go` — pacing between page navigations

### 3. `go/cmd/worker/main.go` — wire realestate to cloakserve

- Add `"jester/internal/realestate"` import
- Add `case "realestate"` to `needsBrowser()` switch → returns `true`
- Update `fetchSource()` to route `realestate` listings through CloakBrowser
- Replace `fetchPage()` HTTP GET with cloakserve CDP navigation

### 4. `config/scraper.yaml` — CloakBrowser cluster settings

- `cdp_url: "ws://cloakbrowser:9222/cbdp/cloakserve"`
- `version: v146`
- `license_key: ""` (empty — no license)

### 5. `config/profiles/*.yaml` — real estate portal profiles

Already exist (tayara.yaml, mubawab.yaml, etc.). May need `fetch_mode: browser` flag.

## Decision Gates

1. **Gate 1:** `docker-compose up` starts cloakbrowser v146 without a license key. `curl http://localhost:9222/cbdp/health` returns 200.
2. **Gate 2:** `go build ./cmd/worker` compiles with the new `realestate` package.
3. **Gate 3:** `go test ./go/internal/realestate/` passes with mock fixtures.
4. **Gate 4:** End-to-end: `go run ./cmd/worker -live -only tayara` fetches listings via CloakBrowser.

## Build Order

1. `docker-compose.yml` — CloakBrowser service config
2. `go/internal/realestate/fetched.go` — data structures
3. `go/internal/realestate/dom.go` — DOM helpers
4. `go/internal/realestate/pace.go` — pacing
5. `go/internal/realestate/live.go` — cloakserve integration
6. `go/internal/realestate/realestate.go` — main adapter
7. `go/cmd/worker/main.go` — wire in `fetchSource()` and `needsBrowser()`
8. `config/scraper.yaml` — CloakBrowser settings
9. Profile YAML updates (add `fetch_mode`)
10. End-to-end verification

## Risks

- **v146 compatibility:** The v146 binary may have different CLI flags than newer versions. We test with `cloakbrowser --help` first.
- **Concurrency limit:** Free tier = 1 concurrent session. Multiple portal scrapers must reuse the same seed or queue sequentially. The `needsBrowser()` check ensures only real estate sources open a session.
- **Portal JS rendering:** Some portals may not render `__NEXT_DATA__` in a headless Chrome environment (anti-bot detection). Fingerprint seeds must match the portal's expected browser profile.