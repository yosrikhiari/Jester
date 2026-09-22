-- Does the archive hold one row per record?
--
-- Three numbers that must agree. `rows_final` is what every other query sees.
-- `rows_raw` counts what is physically stored before the ReplacingMergeTree
-- has merged; it being larger is normal and harmless. `unique_ids` is the
-- truth: if rows_final is bigger, the dedup key is broken and a repeat import
-- HAS created duplicates.
SELECT
    (SELECT count() FROM problem_signal FINAL)        AS rows_final,
    (SELECT count() FROM problem_signal)              AS rows_raw,
    (SELECT uniqExact(record_id) FROM problem_signal) AS unique_ids,
    rows_final - unique_ids                           AS duplicates,
    rows_raw - rows_final                             AS unmerged_parts;
