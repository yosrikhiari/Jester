# Review — Provenance / Reproducibility (2026-10-29)

**Reviewer:** independent review agent (read-only) · **Baseline:** `771d3d5`
**Scope:** journal lab/checks/publish/states, pipeline `runs`, signals reclassify/outcome, edit provenance, Docker/deps, CI.
**Editor verification (orchestrator):** the load-bearing premises below were re-checked line-by-line against the code before scoping builders: `code_state(None)` → `{}` (`lab.py:118-120`), `counts_for_gate` passes trivially when the code envelope is absent or lacks git info (`lab.py:294-301`), `--code` default `""` mapped to `None` (`cli.py:3284`, `cli.py:2942-2943`), `runs` DDL carries no env/commit (`store.py:168-191`), and the four docker-compose images are unpinned (`docker-compose.yml:39,163,209,235`; `.github/dependabot.yml:2` comment aspirational). Some of the reviewer's secondary file citations could not be located as written and were dropped; only verified premises survive into the task register.

---

## Post-merge status (2026-10-29, updated 2026-10-30)

Baseline `771d3d5`; re-checked against `main` at `70f966f` (clean, `== origin/main`) and updated after the N1–N7 batch landed. Findings are kept as written; this table is the current disposition.

| Finding | Status | Current evidence |
|---|---|---|
| B1 code provenance opt-in | **RESOLVED** | `journal run --code` is required (`cli.py:3372-3381`); `counts_for_gate` rejects an absent code envelope (`lab.py:302-320`); attached ledger runs write `code.source=ledger` |
| P-H1 `before_plan` display-only | **RESOLVED (N6, 2026-10-30)** | `checks._before_plan` is consulted by both `_verify_run` and `_block_runs` (`checks.py:194-222,258-273`); `test_a_before_plan_run_cannot_back_a_number` pins both paths |
| P-H2 `runs` has no env/code fingerprint | **RESOLVED** | `runs.env` column (`store.py:363`), populated at start; failed collection records a reason (`test_run_env.py`) |
| P-H3 export does not re-hash figures | **RESOLVED (N7, 2026-10-30)** | `publish.export` re-hashes every `figures/*` image with `lab.artifact_problem` before creating the site worktree (`publish.py:356-377`); edited/unregistered/missing figures each have a regression test |
| Per-run output hash (P1-3 / N2) | **RESOLVED (2026-10-30)** | `experiment_run.output_sha256` is migrated in place; `output_digest` hashes canonical result + stdout/stderr (`journal/__init__.py`, `journal/lab.py`); same output → same hash, changed output → different hash |
| M/M2 docker images unpinned | **RESOLVED** | all four compose images carry `sha256:` digests; `qdrant`/`ollama` retain `latest` only as a tag, made immutable by the digest; `test_docker_pins.py` accepts that shape deliberately |
| Deferred signal-audit findings | **OPEN** | `INSERT OR REPLACE` overwrite semantics, `set_outcome` without an audit row, `reclassify --apply` history, `origin='manual'` reuse — unchanged, not in the next batch |

**Not covered by this review, now on `main`:** `chat/`, `scheduler.py`, `edits.py`, `specs.py`, `maintenance.py`, console chat.

---

## 1. What exists and how it works

- **Journal fingerprinting (designed right):** `fingerprint()` (`journal/lab.py:129-143`) records OS, Python version, machine, CPU count, GPU list, `code_state()` (git HEAD + porcelain → `dirty`, `lab.py:118-126`), seed; runs carry `env` (`lab.py:189-191`); `_start_run` locks the plan at first run (`lab.py:148-157`); `journal attach` derives `before_plan` by comparing timestamps (`lab.py:242-276`), naive stamps treated as UTC (`lab.py:279-291`).
- **Gate helper:** `counts_for_gate` (`lab.py:294-301`) — finished ok + raw data kept + not made from uncommitted code.
- **Figures:** stored with sha256; `artifact_problem()` re-hashes on disk (`lab.py:393-441`) and is consulted by the `numbers` check at `journal check` time (`checks.py:276-286`).
- **Publish/export gates:** required checks (`citations|numbers|lint|origin`) recorded for the version (publish.py:75-78), markdown hash must match (`:73-74`), human approval + `ready` state (`:65-81`).
- **Pipeline provenance:** `runs` table records `run_id, models_used (roles + overrides + prompt_versions + paused), origin, phase_checkpoints, funnel counters` (`store.py:168-191`, `cli.py:297-316`); per-row model stamps; schaf append-only `edit_log` proposal regime (`edits.py`, `store.py:192-204`) — chat/scheduler edits are auditable with before/after rows and reverts-as-new-rows.
- **Signals:** failures-as-rows; `mode` column prevents synthetic/live conflation; `signals evidence` tri-way reconciliation with non-zero exit; timestamps stored UTC/ISO.

## 2. Strengths (verified)

Numbers always computed from stored run artifacts, never interpolated (`checks.py:194-266` + publish gates); the fingerprint model is honest and wide (OS/GPU/code/seed); edit regime structurally refuses UPDATE/DELETE on `edit_log` (tested); README's claimed invariants map to real functions; `.db` files not committed; CI installs from a hash-pinned lock.

## 3. Gaps (verified subset)

- **B1 (blocker) — code provenance is opt-in and trivially passes when absent.** Omitting `--code` yields `code_state(None)` → `{}` and `counts_for_gate` reads `code.get("dirty") is not True` → True. The documented rule ("a run from uncommitted code does not count toward drafting") is therefore not enforced — it is silently waived whenever `--code` is forgotten. Same shape for a run whose `--code` path is not a git repo → `dirty: None` → passes.
- **P-H1 (high) — `before_plan` is display-only.** Runs that predate a plan can stand behind numbers in `_verify_run`/`_block_runs`; nothing rejects or even warns.
- **P-H2 (high) — pipeline `runs` rows carry no environment/code fingerprint** (verified DDL); two identically-named `manual` runs differ only by timestamps.
- **P-H3 (high) — `export` does not re-verify figure hashes at export time** (figures verified only at `check` time; a hand-edited SVG at export damages the article while the markdown hash still matches).
- **M/M2 (medium) — docker images unpinned despite the dependabot comment claiming digest pinning occurs at merge** (all four lines verified).

Deferred-findings from the reviewer (not adopted here, kept in the gap register): signals `signal_run` `INSERT OR REPLACE` overwrite semantics + `set_outcome` overwrite without an audit row (repo already has an append-only edit-log family; proposed audit rows were judged scope-heavy for this cycle); `reclassify --apply` not writing change history; `origin='manual'` default reuse on re-run.

## 4. Candidate workstreams

| # | Task | Files | Acceptance |
|---|------|-------|------------|
| C1 | Code-envelope hardening: `--code` required for `journal run`; `counts_for_gate` returns False for a missing/`{}` code envelope; `attach_ledger` writes an explicit `code: {source: "ledger", ...}` envelope so attached runs carry *some* declaration instead of silence | `journal/lab.py`, `cli.py` + tests | `pytest -k "counts_for_gate or journal"` — a run without `--code` cannot pass the gate; dirty code still blocks; attached ledger runs keep their documentation semantics |
| C2 | Pipeline run fingerprint: `runs.env` column (ADDED_COLUMNS migration) recording `os/python/machine/git commit + dirty` of the repo at run finish (failures recorded as `{"unavailable": reason}`, never fatal) | `store.py`, `cli.py` + tests | `pytest -k run_env` — fresh runs carry the env JSON; legacy DB migrates |
| C3 | Docker pinning: all four compose images pinned (`repo:tag@sha256:`); Dockerfile base images versioned; dependabot comment aligned with reality | `docker-compose.yml`, `docker/Dockerfile.python`, docs note | no `latest`/untagged `image:` lines; a small test asserts pinning; if registry resolution is unreachable, fall back to explicit version tags and document the deferral honestly |
