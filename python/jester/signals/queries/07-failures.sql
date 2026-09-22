-- Every record whose fetch went wrong, grouped by what went wrong.
--
-- Silent failures are the thing to avoid. A failed fetch is stored
-- as a row with run_status='error', so it is counted rather than missing, and
-- this query is the one that has to be empty before a run is called clean.
SELECT
    toDate(first_seen_utc) AS collected_on,
    community,
    error,
    count()                AS records,
    max(last_seen_utc)     AS last_attempt
FROM problem_signal FINAL
WHERE run_status != 'ok'
GROUP BY collected_on, community, error
ORDER BY collected_on DESC, records DESC;
