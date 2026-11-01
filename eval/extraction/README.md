# Extraction quality labels (S-06)

The harness lives in `python/jester/extraction_eval.py`; the command is:

```bash
# Draw the sample (once; deterministic from the seed).
python -m jester.cli eval sample --db data/jester.db --out eval/extraction/sample.jsonl

# Grade the committed labels.
python -m jester.cli eval extract --labels eval/extraction/labels.jsonl
```

Draw the sample **before** any retention sweep: `retention` NULLs `raw_text`
past the TTL, and `raw_text` is the sampling universe.

No labels is not an error: the command says what to write and exits 0. Once
labels exist it prints n, κ, precision, recall, F1, fabrication, unchecked rate,
nuggets/comment and the sample seed, then exits 1 if a published floor fails
(F1 < 0.70, fabrication > 5%, or more than 1.2 nuggets/comment).

## Files

| File | Purpose |
|---|---|
| `sample.jsonl` | The drawn comments. Drawn 2026-11-01 from a verified snapshot: 150 of 115,199 comments, seed `20261031`. Committed so the set survives the retention sweep. Reproducible from the same archive *contents* and seed — a new comment changes the pool and can change the ids. |
| `sample.design.json` | Provenance for the draw: archive basename, pool size, every stratum's allocation, seed, and what was actually drawn. No filesystem paths: it is committed. |
| `labels.jsonl` | One JSON object per rater verdict. Committed once labelled. **Owner gate.** |

The draw reads keys and lengths first and fetches text only for the rows it
chose, so sampling a 990 MB archive does not load it into memory. Strata are
allocated in proportion to their pool sizes, with at least one row per
non-empty stratum and never more than a stratum has.

## Sample

150 comments, stratified by platform × category × length bucket
(`short` ≤ 280 chars, `medium` ≤ 1000, `long` above), with a fixed seed
(default `20261031`). Draw with `sample_from_db(db, sizes, seed)`; the strata
sizes are the sample design and belong in the eventual report. The same seed
and the same archive contents produce the same ids: every row is ranked by
`sha256(seed, unique_key)` and the lowest ranks in each stratum are taken, so
the order rows arrive in cannot change the draw. Lengths are measured on the
raw text exactly as SQL `length(raw_text)` measures them.

50 of the 150 are labelled twice (second sitting at least a day later, or a
second labeller) so the report can carry Cohen's κ.

## Label schema

```json
{"unique_key": "reddit:sub:comment",
 "platform": "reddit",
 "rater": "owner",
 "keep": true,
 "gold": ["the complaint the human found"],
 "predicted": [{"insight": "what the extractor said",
                "verdict": "supported"}]}
```

`verdict` is `supported`, `fabricated` (the comment does not say it), or
`unchecked` (not reviewed). Counted, never imputed:

- precision = supported / (supported + fabricated)
- recall = supported / |gold|
- F1 = harmonic mean of the two
- fabrication rate = fabricated / all predictions
- unchecked rate = unchecked / all predictions
- nuggets/comment = predictions / comments in the label set

`keep` is the binary decision two raters are compared on for κ.

## Owner gate

Drawing the sample from the archive and labelling it are owner work (~3 hours).
The pipeline is paused; this work costs no model calls and no quota.
