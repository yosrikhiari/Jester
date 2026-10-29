# Review — Dedup / Clustering / Classification (2026-10-29)

**Reviewer:** independent review agent (read-only) · **Baseline:** `771d3d5` on `feat/ui-corporate-redesign`
**Scope:** `vector.py`, `agents/archivist.py`, `agents/property_dedup.py`, `idea_dedup.py`, `clustering.py`, `agents/labeller.py`, `chunking.py`, `dedup_sweep.py`, `signals/` (filters, fixtures, from_archive), `config/thresholds.yaml`, `config/signal_rules.yaml`, `config/hiring_rules.yaml`, `store.py` clusters/reembed, CLI cluster/dedup-sweep/reembed/doctor, tests, `docs/realestate-dedup-validation.md`.
**Labels:** MEASURED = verified against code/tests this session; HYPOTHESIZED = plausible failure mode not yet observed.

---

## 1. How it works (map)

- **Nugget dedup path.** Per-DB vector store namespacing (`cli.py:88-126`); open failure → `UnavailableVectorStore` (`vector.py:67-107`), dimension mismatch fatal (`cli.py:596-603`). `Archivist.run` (`agents/archivist.py:33-84`): DB `exists_unique_key` skip → `search_scored` top-5 (`vector.py:244-252`) → duplicate if `score >= dedup_threshold` (`archivist.py:69`) → near-miss band recorded (hard-coded 0.85, `archivist.py:14-19`) → `upsert` (1 retry; `vector.py:44-56`) → `insert_nugget`; upsert failure still archives with `needs_reembed=1` (`archivist.py:74-81`). Search failure defers; batches stay queued; `n_dedup_deferred` in run summary (`cli.py:604-629`). Corpus-model guard R32 (`archivist.py:36-43`).
- **Idea-level dedup (R44).** 0.87 on title+problem vs `ideas` collection (`agents/synthesizer.py:297`, `idea_dedup.py:45-59`).
- **Clustering.** `jester cluster` → `cluster_archive` (`clustering.py:585-702`): chunk → embed → mutual k-NN → split incoherent oversized components → min_nuggets → `label_clusters` → `replace_clusters` (delete-all, re-insert, `store.py:785-834`). FakeEmbedding refusal tested (`clustering.py:93-104`; `test_clustering.py:103-117`).
- **Labeller.** Model names + deterministic keyword fallback + `coherent` field (`agents/labeller.py:93-201`).
- **Signal classification.** Deterministic rules (`signals/filters.py`): family weights once per family, buyer = owner_voice + pain, matched phrases stored in the reason (`:342-343`), crossposts reported. 20-fixture gate (`python/tests/fixtures/signals/*.json`, `signals/fixtures.py:162-192`).
- **Calibration tooling.** `dedup_sweep.py` pure P/R/F1 sweep over JSONL pairs, 0.80–0.92 grid (`:17-40`); CLI `jester dedup-sweep` (`cli.py:1854-1888`). `reembed` batched (`vector.py:203-235`).

## 2. Strengths (evidence-backed)

Layered availability handling with regression tests (mid-run 408 → defer; dead-at-open → defer; width mismatch fatal — `test_archivist.py:99-188`); batching fenced from the dedup decision (`vector.py:210-211`, equality e2e `test_e2e_changes.py:222-235`); sub-threshold neighbours visible for near-miss accounting (`vector.py:244-252`); pure clustering rule with brute-force oracle (`test_clustering.py:213-220`); honesty in summaries (ungrouped count, neighbour backend, unknown authors); classifier offline/deterministic with reasons naming matched words and missing halves, validated load rules; reembed refuses live runs and keeps flags + exit 1 (`test_reembed.py:67-106`); property dedup's unknown-not-zero semantics + evidence-weight floor; signals retention discipline (excerpt-only, unknown stays unknown, mode column).

## 3. Gaps

### HIGH
- **H1 — `hash()`-derived Qdrant point IDs are not stable across processes** (`vector.py:195,227`, `archivist.py:76`, `cli.py:1947,1953`, `idea_dedup.py:42`). Observed this session: two processes hash `unique-key-abc` to two different mod-2^63 values → `reembed --all` in a new process adds duplicate points, parity `rows != points` noise, meaningless `embedding_id`s. **Remediation:** `point_id_for(unique_key)` = sha256-derived 63-bit id at all six sites; add cross-process + idempotent-upsert tests.
- **H2 — dedup self-match can swallow a nugget permanently** (`vector.py:252` returns key; `archivist.py:69-70` discards it; upsert at `:75` precedes `insert_nugget` at `:81`). Kill between the two statements → next run sees own vector ≈1.0 ≥ threshold → "duplicate", batch processed, comments marked ingested; nothing recorded. **Remediation:** skip matches whose payload key equals the nugget's own `unique_key`; own-vector-not-in-DB must proceed to insert; tests in `test_archivist.py`.
- **H3 — negative families cannot veto a buyer verdict** (`filters.py:265-280` gates only on score; vendor `signal_rules.yaml:432-457` says a launch post is never a buyer). Vendor post with maximal buyer vocabulary minus `vendor_pitch` still clears 0.60 → BUYER with "against it" printed in a reason nobody gates on. **Remediation:** veto role forcing `NONE`; fixture case for the launch-post shape.

### MEDIUM
- **M1 — labeller's `coherent:false` is dropped** (`labeller.py:198-201` intent; `clustering.py:568-579` `to_payload` omits it; no console reader). Consensus: persist `incoherent` per cluster; render it.
- **M2 — re-clustering destroys cluster identity** (`store.py:794-834` delete-all re-insert, no FKs; `cluster_ideas` orphaned — drafts disappear from `list_clusters`/`get_cluster`). **Remediation:** carry `cluster_ideas` to the max-overlap successor cluster.
- **M3 — single global 0.87; no labelled pairs exist** (`archivist.py:69`; `dedup-sweep` has no `--category`, `cli.py:3726-3736`; repo glob `testdata/dedup` empty; property-doc precision 5/7, recall unmeasured). **Remediation:** per-category map + `--per-category` sweep + seeded pair files.
- **M4 — property semantic layer structurally unreachable** (readers only; empty result → `(0.0, None)` bypasses the unknown-semantics branch; per-candidate re-embed cost). **Remediation:** unknown→structured-only gate + `sync_property_vectors` + hoist target embedding.
- **M5 — `INDEXING_THRESHOLD=1000` applies only to newly created collections** (create branch `vector.py:152-161`; exists-branch only checks dimension). Old stores keep the 20,000 default silently. **Remediation:** on exists, fetch+update optimizers config or report in doctor.
- **M6 — embedding-model change is not completable end-to-end** (`reembed --all` doesn't recreate the collection nor update `corpus_embedding_model`, so `ModelMismatch` persists; `--all` untested). **Remediation:** completing step after successful `--all`; `--all` test.
- **M7 — from-archive hiring records lose `[matched: …]` provenance** (`from_archive.py:261` stores `verdict.reason` only; live path keeps matched phrases). One-line mirror of `apply_to`.

### LOW
L1 hard-coded `embedding_model` `insert_nugget` (`store.py:764`); L2 near-miss band not tied to threshold (`archivist.py:16-17`); L3 reembed drops `category` payload (`cli.py:1940,1952`); L4 doctor wording overclaims ("excluded from clustering"); L5 governance dates overdue/provisional (`signal_rules.yaml:49-55`, `hiring_rules.yaml:36-39`); L6 `chunks` collection shared cross-DB + destructively rebuilt (`clustering.py:54,293-298`); L7 production cluster reruns not bit-deterministic (HNSW path); L8 `dedup-sweep` offline only when config resolves `FakeEmbedding` (default provider is ollama).

**Qdrant-down duplicate-suppression answer:** down-at-open → nothing archived, queued, `n_dedup_deferred` (correct); flapping 408 → nugget archived `needs_reembed=1`, near-duplicates can slip through that window with no drain signal; the *unrecorded* loss is the H2 self-match ghost.

## 4. Measured vs hypothesized

Measured: 83→23 ms lookup (16958 points/8 segs, `vector.py:25-41`); clustering threshold sweep (342 chunks), brute-force timings (n=342/800/2000 → 8.2s/46.4s/293.3s); property precision 5/7 ≈ 71%, recall unmeasured; 65 price-only dups pre-floor; 2478 band candidates → 1; signals bases (184 posts; 461 HN records/6 buyers/4 vendors); 20 fixture cases gated; H1 hash instability **demonstrated by cross-process execution**.
Hypothesized: H2 frequency; M3 harm beyond property domain (no labels); M5 live-store state; M6 crash-mid-`--all`; L7 churn magnitude.

## 5. Candidate workstreams

1. **Deterministic point identities** — `vector.py` helper + 6 call sites; cross-process/idempotency tests.
2. **Self-match guard + ghost-nugget fix** — `archivist.py`; seeded-vector and crash-simulation tests.
3. **Labelled-pair seeds + per-category threshold + annotated P/R tables** — `config/thresholds.yaml`/`config.py`, `dedup_sweep.py` `--per-category`, `testdata/dedup/pairs/*.jsonl`; archivist applies per-category value with global fallback.
4. **Persist labeller disagreement + carry cluster drafts across re-clustering** — `clustering.py` payload + `store.py` migration/carry-forward + console render.
5. **Property semantic-layer honesty + vector sync** — `property_dedup.py`; unknown→structured gate, sync step, hoisted embedding.
6. **Negative-family veto + vendor fixture** — `signals/filters.py` + fixture case; gate totals updated.

*Editor note: the orchestrator verified the arXiv-free textual premises used for builder scoping: `testdata/` contains no pairs files; `PROMPT_VERSIONS`/`dedup_threshold` facts cross-checked.*
