# Batch 1 work order — parallel implementation, verification, critic gate

> **Date:** 2026-10-25 · **Base commit:** `5c1800f` · **Integration branch:** `feat/roadmap-batch-1`
> **Authority:** `docs/roadmap/2026-10-25-project-assessment.md` §6 (task register).

## 1. Scope of this batch

Only work that can be completed and verified **without inventing human input** is in scope. The
assessment's P0 quality tasks require the owner's labels and decision; they are excluded and listed in §4.

| Workstream | Branch / worktree | Tasks | Files owned (exclusive) |
|---|---|---|---|
| W1 Journal | `feat/w1-journal` · `ws-journal` | P1-1 `journal measure`; P1-2 per-run output hash | `python/jester/journal/**`, `python/jester/cli.py`, `python/tests/test_journal*` |
| W2 Console | `feat/w2-console` · `ws-console` | P1-3 `/metrics` + console request/DB gauges; P2-7 schema preflight; P2-2 control-boundary contrast | `python/jester/console/**`, `python/tests/test_console*`, `python/tests/test_metrics*` |
| W3 Provenance & limits | `feat/w3-provenance` · `ws-provenance` | P1-4 batching cap; P2-4 model digest | `python/jester/config.py`, `roles.py`, `llm.py`, `providers_meta.py` (new), `config/thresholds.yaml`, their tests |
| W4 UI regression | `feat/w4-uitests` · `ws-uitests` | P2-1 browser smoke + CI job | `python/tests/test_ui_smoke.py` (new), `.github/workflows/ci.yml`, `docs/quality/ui-smoke.md` |

Ownership is exclusive: a builder may not edit another workstream's files. No builder pushes; each
commits on its own branch. The integrator merges in order W1 → W2 → W3 → W4 and resolves by hand.

## 2. Cross-workstream contracts (agreed before implementation)

- **C1 — metrics endpoint.** W2 owns the route and the module; it must not require anything from W3.
  `GET /metrics` returns Prometheus text and never raises.
- **C2 — no LLM telemetry by invention.** The archive persists no per-role LLM counters, so W2 must not
  emit them. The omission is documented in the module and reported as a follow-up, not faked as zero.
- **C3 — model digest is additive.** W3 adds `digest` to the resolved role record and `agent.digest`.
  Existing keys and `model_name()`'s output are unchanged, so W2's JSON payloads stay compatible.
- **C4 — no schema migration on open.** W2's preflight may detect a stale archive but must never reshape
  it; the remedy is the existing `jester migrate` command.
- **C5 — hermetic tests.** W4's smoke skips unless `JESTER_UI_SMOKE=1`; no test in this batch may require
  network, a running Ollama, or a populated production archive.

## 3. Acceptance per workstream

| W | Acceptance (all must be demonstrated with command output) |
|---|---|
| W1 | measure succeeds and refuses correctly (unknown slug, unposted channel, `--at` before posting, no values); gate opens after 7- and 30-day rows; same fingerprint + different output ⇒ different `output_sha256`; NULL renders "not recorded"; targeted journal+cli tests pass |
| W2 | `/metrics` output quoted; stale archive ⇒ `/api/*` 503 naming `jester migrate` while `/` and `/metrics` still work; contrast test proves `--line-control` ≥ 3:1 in both themes and fails when reverted; console tests pass |
| W3 | `batch=6` rejected, `5` accepted, default unchanged; digest present from a fake endpoint, `None` offline, `None` hosted, cached; `jester models` still prints; targeted tests pass |
| W4 | smoke passes with `JESTER_UI_SMOKE=1`; fails on a deliberately reintroduced nav-contrast or card-overflow defect; skips without the flag; `ci.yml` parses |

## 4. Explicitly out of scope, and why

| Task | Why not in this batch |
|---|---|
| P0-1 scope sign-off | Owner decision; fabricating it would falsify the project's central claim |
| P0-2 extraction labels; P0-3 dedup labels | Requires human labelling; the harness can be built but the measurement cannot be invented |
| P2-3 critic calibration | Acceptance requires a blinded human ranking (Spearman ≥ 0.7) |
| P2-5 image-digest pinning, non-root images | Needs registry access / Docker and conflicts with the shared-SQLite uid constraint |
| P2-6 branch deletion | Destructive on shared state; requires explicit authorisation |
| P3-1 c-TF-IDF labeller; P3-2 HNSW recall | Need corpus-scale data and an operator judgement on labels |

## 5. Integration sequence and gates

1. Merge W1 → run journal tests.
2. Merge W2 → run console tests + boot the console and hit `/metrics`.
3. Merge W3 → run config/roles/providers tests + `jester models`.
4. Merge W4 → run the smoke with the flag.
5. Full gates: `pytest python/tests` (all, excluding live markers), `go test ./...`, the UI smoke, and a
   final diff review for unrelated changes.
6. Independent verifier per workstream, then the global critic. Nothing merges to `main` unless the critic
   scores ≥ 8.5 **and** every gate above is green.

## 6. Iteration log

| Iter | Issues found | Owner | Change | Verification | Critic | Blockers |
|---|---|---|---|---|---|---|
| 0 | — | — | Baseline: 4 branches cut from `5c1800f` | Baseline suite green at `b175e32` (1,665 passed) | — | — |
