-- How many records did each run day produce?
--
-- Dated on first_seen_utc (when WE collected it), not on when the author
-- posted: the question is whether the scheduled run happened and what it
-- returned. A day missing from this list is a day the collector did not run,
-- which is the "did it run on schedule" check in one query.
SELECT
    toDate(first_seen_utc)              AS collected_on,
    mode,
    count()                             AS records,
    countIf(audience = 'buyer')         AS buyer,
    countIf(audience = 'practitioner')  AS practitioner,
    countIf(run_status != 'ok')         AS errors,
    uniqExact(run_id)                   AS runs
FROM problem_signal FINAL
GROUP BY collected_on, mode
ORDER BY collected_on DESC, mode;
