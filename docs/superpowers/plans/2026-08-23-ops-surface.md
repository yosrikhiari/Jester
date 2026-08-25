# Plan: Ops / Scheduling Surface (M2.4 + §13) — pure-Python build

- Date: 2026-08-23
- Feature: Operations & scheduling surface for the `jester` CLI (deviation from Go worker; built on the existing pure-Python system).
- Spec: `jester-setup.md` §13 (Ops surface), M2.4, §36 (runs table), v3.7 #11 (advisory lock), R21/R34/R40/R42/R46, §25 #9 (tenacity backoff).

## Scope (this pass)
1. Record a per-run **run summary** on every `jester run` (§13.1 / §36 `runs` table):
   - `n_posts`, `n_nuggets_kept`, `n_ideas`, `n_discarded_trivial`, `trivial_share`
   - `models_used` JSON (R42), `floor_flags` JSON (R21/R40), `phase_checkpoints` JSON (R46)
   - `started_at`/`finished_at`/`status`/`origin`
2. `jester runs` command lists stored runs — **only stored fields** (R34).
3. **Advisory lock** (v3.7 #11): `jester run` refuses a second concurrent run.
4. **Floor flags** computed: `LOW_NUGGETS`, `NO_IDEAS`, `FAILED_BATCHES`, `THIN_HAUL`, `ALL_TRIVIAL` (R21/R40).
5. **Retention sweep**: `jester retention` drops already-synthesized nuggets older than `retention_days` and prunes their vectors; records `retention_swept_at`/`count`.
6. **Nightly/on-demand trigger**: `scripts/nightly.sh` wrapper invoking `jester run`; on-demand is `jester run` itself.
7. Failure handling uses `tenacity` backoff where the fetch/LLM calls would live (§25 #9) — applied to the synthesizer call path.

## Non-goals (deferred to doc-stack migration)
- Real fetch / CloakBrowser / Qdrant / CrewAI / Ollama. Mock pipeline stays.
- Multi-platform additive platforms (cp4–5).

## Approach
- TDD: write `python/tests/test_ops.py` first (red), then implement store + cli changes (green), then run full suite.

## Test list (test_ops.py)
- `test_run_writes_summary`: counts (posts/kept/ideas/trivial) recorded correctly.
- `test_run_floor_flags`: NO_IDEAS flagged when no clusters; LOW_NUGGETS when kept below `min_nuggets`.
- `test_run_advisory_lock`: second `cmd_run` while lock held exits non-zero; lock cleared on completion.
- `test_jester_runs_lists_stored_fields_only`: `cmd_runs` output uses only persisted columns.
- `test_retention_sweep_drops_old_synthesized_nuggets`: old synthesized nuggets deleted, vectors pruned.
- `test_models_used_and_checkpoints_recorded`: R42/R46 captured as JSON.

## Files
- `python/jester/store.py` — add `runs` table + `begin_run/finish_run/list_runs/get_run/active_run/retention_sweep` + lock helpers.
- `python/jester/cli.py` — `cmd_run` writes summary + lock; add `cmd_runs`, `cmd_retention`; subparsers.
- `python/tests/test_ops.py` — new.
- `scripts/nightly.sh` — new wrapper.
- `jester-setup.md` — §37 minimal progress note.
