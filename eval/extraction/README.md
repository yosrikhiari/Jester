# Extraction quality labels (S-06)

The harness lives in `python/jester/extraction_eval.py`; the command is:

```bash
python -m jester.cli eval extract --labels eval/extraction/labels.jsonl
```

No labels is not an error: the command says what to write and exits 0. Once
labels exist it prints n, κ, precision, recall, F1, fabrication, unchecked rate,
nuggets/comment and the sample seed, then exits 1 if a published floor fails
(F1 < 0.70, fabrication > 5%, or more than 1.2 nuggets/comment).

## Files

| File | Purpose |
|---|---|
| `sample.jsonl` | The drawn comments. Deterministic from the seed; committed so the set is reproducible. **Owner action:** drawing it reads `data/jester.db`. |
| `labels.jsonl` | One JSON object per rater verdict. Committed once labelled. |

## Sample

150 comments, stratified by platform × category × length bucket
(`short` ≤ 280 chars, `medium` ≤ 1000, `long` above), with a fixed seed
(default `20261031`). Draw with `sample_from_db(db, sizes, seed)`; the strata
sizes are the sample design and belong in the eventual report. The same seed
and the same archive produce the same ids: rows are ordered by `unique_key`
before sampling.

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
