-- What did we collect, split live vs fixture?
--
-- Grouped by the column, never asserted in a report: a fixture row cannot be
-- counted as a live one because the mode travels with the row. This is the
-- table the daily update quotes.
SELECT
    mode,
    count()                                    AS records,
    sum(relevant)                              AS relevant,
    countIf(audience = 'buyer')                AS buyer,
    countIf(audience = 'practitioner')         AS practitioner,
    countIf(run_status != 'ok')                AS errors,
    countIf(edited_utc IS NOT NULL)            AS edited,
    countIf(removed_utc IS NOT NULL)           AS removed,
    min(first_seen_utc)                        AS first_collected,
    max(last_seen_utc)                         AS last_collected
FROM problem_signal FINAL
GROUP BY mode
ORDER BY mode;
