| field | sqlite | clickhouse | meaning |
|---|---|---|---|
| `record_id` | TEXT | String | our key: <platform>:<source_id> |
| `mode` | TEXT | LowCardinality(String) | live \| synthetic — never inferred, always stored |
| `platform` | TEXT | LowCardinality(String) | hackernews \| himalayas \| remotive \| jobicy — the public APIs the collectors read; reddit only for rooms scored from the worker's archive (no Reddit requests) |
| `source_id` | TEXT | String | the platform's own id (HN item number, board listing id, Reddit post id) |
| `source_url` | TEXT | String | permalink; the evidence link that travels with the signal |
| `community` | TEXT | LowCardinality(String) | hn/comment \| hn/ask \| hn/show \| hn/story \| hn/hiring \| himalayas/contract \| remotive/jobs \| jobicy/jobs \| a Reddit hiring room from the archive, set apart pending access |
| `kind` | TEXT | LowCardinality(String) | post \| comment |
| `author` | TEXT | String | handle as published; never enriched, never contacted |
| `title` | TEXT | String | post title, empty for a comment |
| `excerpt` | TEXT | String | permitted text, truncated to EXCERPT_CHARS |
| `text_hash` | TEXT | String | sha1 of the full permitted text — how an edit is detected |
| `query` | TEXT | String | the community/search term that matched this record |
| `match_reason` | TEXT | String | why it was kept, in words a human can check |
| `match_confidence` | REAL | Float32 | 0–1; the classifier's own confidence, not a score |
| `audience` | TEXT | LowCardinality(String) | buyer (outreach can act on it) \| practitioner (content material) \| none |
| `relevant` | INTEGER | UInt8 | 1 kept as a problem signal, 0 collected and rejected |
| `company` | TEXT | LowCardinality(String) | 'unknown' unless the source states it (a job advert names its company) |
| `buyer_intent` | TEXT | LowCardinality(String) | 'unknown' for a post; a hiring advert's own engagement words |
| `created_utc` | TEXT | Nullable(DateTime64(3)) | when the author posted it |
| `edited_utc` | TEXT | Nullable(DateTime64(3)) | when we first saw the text change |
| `removed_utc` | TEXT | Nullable(DateTime64(3)) | when the source stopped returning it |
| `first_seen_utc` | TEXT | DateTime64(3) | our first sighting — never overwritten |
| `last_seen_utc` | TEXT | DateTime64(3) | our latest sighting |
| `revisions` | INTEGER | UInt16 | how many times the text changed under us |
| `run_id` | TEXT | String | the run that last touched this row |
| `run_status` | TEXT | LowCardinality(String) | ok \| error |
| `error` | TEXT | String | what went wrong on this record, empty when ok |
| `outcome` | TEXT | LowCardinality(String) | what came of it: '' untouched \| contacted \| replied \| meeting \| won \| no \| unfit |
| `outcome_at` | TEXT | Nullable(DateTime64(3)) | when the outcome was recorded |
| `outcome_note` | TEXT | String | one line from whoever worked it; empty is fine |
