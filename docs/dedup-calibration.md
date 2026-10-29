# Dedup threshold calibration — labelled pairs, per category

The `0.87` in `config/thresholds.yaml` (`dedup_threshold`) was one bar for the
whole archive. This note records what `jester dedup-sweep` now measures
against, where its labelled pairs come from, and the honest state of those
numbers. Nothing in `thresholds.yaml` was promoted on the strength of this
first run; promoting is a config edit after reading a table, per the command
itself.

Companion documents: `docs/realestate-dedup-validation.md` (the cross-portal
run whose verified pairs seed the real-estate file) and the pair-format
docstring in `python/jester/dedup_sweep.py`.

---

## What the sweep is

`jester dedup-sweep` is **offline and deterministic**: it embeds every distinct
text of a labelled pair file once, scores each `{a, b, same}` pair by cosine,
and prints precision/recall/F1 at every threshold on the 0.80–0.92 grid (plus
the configured bar when it is off-grid). A pair file being
`{"a": text, "b": text, "same": bool}` — two texts, and whether a human says
they describe the same thing. The `same` field and no embedder decides what
counts as a positive: **recall is measured against the labels only**. What is
backend-dependent is the cosine, so the realised P/R/F1 move with whatever
backend produced the embeddings.

Two invocation shapes:

```
jester dedup-sweep --pairs FILE.jsonl              # one file, one table
jester dedup-sweep --pairs-dir DIR [--category C]  # one annotated table per
                                                   # <category>.jsonl in DIR
```

`--pairs-dir` refuses to print a table for a file below the pair floor
(`--min-pairs`, default 20) and prints the refusal instead: precision and
recall over a handful of pairs describe the labeler's luck, and a grid next to
real bands would dress it as evidence. In single-file mode the older
note-and-continue behavior is kept — that path is a manual one-off whose
context travels with it.

## The seeded pair files

`testdata/dedup/pairs/` — one file per category, JSONL, with per-line
provenance so no label is silent:

| file | pairs | same | distinct | provenance |
|---|---|---|---|---|
| `pain_point.jsonl` | 24 | 12 | 12 | hand-authored: the same-paraphrase pairs are one complaint said two ways (deploy babysitting, restore-untested backups, order re-keying, mail deliverability, cert renewal, spreadsheet versions, inbox triage, alert fatigue, stock reconciliation, onboarding on stale docs, runbook drift, dropped payment webhooks); the distinct pairs deliberately share vocabulary while naming different work (deploy-speed vs rollback, backup-capacity vs restore-time, ...) |
| `feature_request.jsonl` | 20 | 10 | 10 | hand-authored: same-request paraphrases (two-way calendar sync, bulk actions past ten, surviving dark mode, CSV export, offline reply queue, ...) vs different feature asks in the same product area |
| `real_estate.jsonl` | 24 | 13 | 11 | **6 derived from `docs/realestate-dedup-validation.md`** (the four verified true positives and two verified false positives, composed only of that table's stated facts: street, price, bedroom count where stated, portal) + 18 synthetic hand-authored listing pairs written to its two failure shapes |

Provenance notes:

- Every line carries a `source` field stating where its LABEL rests
  (`docs/realestate-dedup-validation.md ...` for the six doc-derived rows,
  `hand-authored ...` for everything else) and, for the hand-authored groups,
  a `why` field naming the specific claim ("deploy speed vs deploy rollback").
  Unknown or borderline phrasings got LABELLED as the near-miss cases they
  are, not dropped: the sweep is only as honest as what it is shown.
- The doc-derived real-estate texts quote the validation run's confirmed
  overlaps and contain nothing that document does not state. The synthetic
  ones describe streets and portals that were never observed.
- Hand-authored texts are deterministic strings written once for these files;
  no generation, no randomness, no scraping.

## How to run it

```sh
# offline, no model server needed:
JESTER_ROLE_EMBEDDING=fake jester dedup-sweep --pairs-dir testdata/dedup/pairs

# the configured backend (whoever's user profile says `ollama`): no env needed
jester dedup-sweep --pairs-dir testdata/dedup/pairs
```

`JESTER_ROLE_<ROLE>` overrides act on the resolved role, like
`--role embedding=fake` does for a one-run override in the treat pipeline —
the same mechanism `roles.py` already exposes, so there is no second switch
for this command.

## Current output

Two runs, both against the exact committed files of this note, back end
recorded per table. **Backend-dependence is the headline**:

### Run 1 — offline stand-in

`JESTER_ROLE_EMBEDDING=fake` → `FakeEmbedding (dim 32)` (sha256 of the whole
text, spread across 32 buckets). With this backend the cosine between two
DIFFERENT strings is hash noise; measured on these exact files: same-pairs
max 0.242/0.260/0.295, distinct-pairs max 0.307/0.337/0.378 per file — same
and distinct are inside each other's range, which is why the tables below
show a degenerate all-zero recall. This is the honest picture of what the
offline sweep can and cannot tell you; it says nothing about thresholds.

```
embedding backend: FakeEmbedding (dim 32)

== feature_request ==
pairs: 20  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80 *| 1.00 | 0.00 | 0.00 | 0 0 10 10
0.81  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.82  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.83  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.84  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.85  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.86  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.87  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.88  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.89  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.90  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.91  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.92  | 1.00 | 0.00 | 0.00 | 0 0 10 10
best F1: 0.80 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.feature_request in config/thresholds.yaml and re-run this sweep to confirm

== pain_point ==
pairs: 24  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80 *| 1.00 | 0.00 | 0.00 | 0 0 12 12
0.81  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.82  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.83  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.84  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.85  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.86  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.87  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.88  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.89  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.90  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.91  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.92  | 1.00 | 0.00 | 0.00 | 0 0 12 12
best F1: 0.80 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.pain_point in config/thresholds.yaml and re-run this sweep to confirm

== real_estate ==
pairs: 24  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80 *| 1.00 | 0.00 | 0.00 | 0 0 13 11
0.81  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.82  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.83  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.84  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.85  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.86  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.87  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.88  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.89  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.90  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.91  | 1.00 | 0.00 | 0.00 | 0 0 13 11
0.92  | 1.00 | 0.00 | 0.00 | 0 0 13 11
best F1: 0.80 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.real_estate in config/thresholds.yaml and re-run this sweep to confirm
```

### Run 2 — the configured backend (the one the archive actually runs)

`OllamaEmbedding (dim 768)`, model `nomic-embed-text`, host `localhost:11434`
(the daemon reachable on the author's machine at commit time):

```
embedding backend: OllamaEmbedding (dim 768)

== feature_request ==
pairs: 20  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80 *| 1.00 | 0.40 | 0.57 | 4 0 6 10
0.81  | 1.00 | 0.40 | 0.57 | 4 0 6 10
0.82  | 1.00 | 0.30 | 0.46 | 3 0 7 10
0.83  | 1.00 | 0.30 | 0.46 | 3 0 7 10
0.84  | 1.00 | 0.30 | 0.46 | 3 0 7 10
0.85  | 1.00 | 0.20 | 0.33 | 2 0 8 10
0.86  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.87  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.88  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.89  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.90  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.91  | 1.00 | 0.00 | 0.00 | 0 0 10 10
0.92  | 1.00 | 0.00 | 0.00 | 0 0 10 10
best F1: 0.80 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.feature_request in config/thresholds.yaml and re-run this sweep to confirm

== pain_point ==
pairs: 24  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80 *| 1.00 | 0.50 | 0.67 | 6 0 6 12
0.81  | 1.00 | 0.42 | 0.59 | 5 0 7 12
0.82  | 1.00 | 0.42 | 0.59 | 5 0 7 12
0.83  | 1.00 | 0.25 | 0.40 | 3 0 9 12
0.84  | 1.00 | 0.17 | 0.29 | 2 0 10 12
0.85  | 1.00 | 0.17 | 0.29 | 2 0 10 12
0.86  | 1.00 | 0.17 | 0.29 | 2 0 10 12
0.87  | 1.00 | 0.08 | 0.15 | 1 0 11 12
0.88  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.89  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.90  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.91  | 1.00 | 0.00 | 0.00 | 0 0 12 12
0.92  | 1.00 | 0.00 | 0.00 | 0 0 12 12
best F1: 0.80 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.pain_point in config/thresholds.yaml and re-run this sweep to confirm

== real_estate ==
pairs: 24  skipped lines: 0
 thr |    P |    R |   F1 | tp fp fn tn
0.80  | 0.71 | 0.92 | 0.80 | 12 5 1 6
0.81  | 0.79 | 0.85 | 0.81 | 11 3 2 8
0.82  | 0.77 | 0.77 | 0.77 | 10 3 3 8
0.83  | 0.77 | 0.77 | 0.77 | 10 3 3 8
0.84 *| 0.91 | 0.77 | 0.83 | 10 1 3 10
0.85  | 1.00 | 0.69 | 0.82 | 9 0 4 11
0.86  | 1.00 | 0.69 | 0.82 | 9 0 4 11
0.87  | 1.00 | 0.61 | 0.76 | 8 0 5 11
0.88  | 1.00 | 0.61 | 0.76 | 8 0 5 11
0.89  | 1.00 | 0.39 | 0.56 | 5 0 8 11
0.90  | 1.00 | 0.39 | 0.56 | 5 0 8 11
0.91  | 1.00 | 0.31 | 0.47 | 4 0 9 11
0.92  | 1.00 | 0.15 | 0.27 | 2 0 11 11
best F1: 0.84 (configured: 0.87)
to adopt it: set dedup_threshold_by_category.real_estate in config/thresholds.yaml and re-run this sweep to confirm
```

What these two tables jointly establish, and why neither promoted a
threshold:

- **The number depends on the backend, always** — the fact the validation run
  already found (`docs/realestate-dedup-validation.md`). The offline backend
  cannot separate paraphrases at all (noise band ±0.4 for both classes); with
  the real embedder the same labels land P=1.00 at 0.85–0.88 for the
  real-estate texts but P=1.00/R=0.50 at 0.80 for the paraphrased comment
  complaints. A threshold adopted from either table is meaningless unless the
  table was produced by the backend the archive actually embeds with.
- **Labels are the ground truth; the rest is a ruler.** R is computed from
  the `same` field only. If a row's label is wrong, R lies, and no threshold
  will fix that — which is why each line states its provenance.
- **The sizes are floors, not conclusions.** 20–24 pairs per category is the
  minimum this sweep prints tables for; a real adoption table wants more,
  and it wants them from the LIVE archive's language, not only from
  hand-authored paraphrases. The next increment is exactly that.

## The near-miss band follows the bar

`classify_similarity` couples the near-miss band to the threshold actually
applied (`archivist.py: threshold - 0.02`, `NEAR_MISS_WIDTH`) instead of a
bare 0.85, so a per-category bar moves its own strip: at the global 0.87 the
band is [0.85, 0.87) — every recorded judgement unchanged — and a 0.80 bar
gets a [0.78, 0.80) strip rather than an unreachable one.

## Adoption workflow

1. Grow the pair files (labels first; they are the only ground truth).
2. `jester dedup-sweep --pairs-dir testdata/dedup/pairs` against the BACKEND
   the archive embeds with.
3. Read the per-category rows; the per-category "configured" value is the
   category's own bar when one is set, else the global.
4. Set `dedup_threshold_by_category.<category>` in `config/thresholds.yaml`
   (values are validated against the same [0.5, 0.995] boundary as the
   global knob, per entry).
5. Re-run the sweep and confirm the adopted bar still has the best row.
