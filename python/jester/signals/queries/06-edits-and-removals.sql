-- Records that changed or disappeared under us.
--
-- A post edited after collection and a post deleted after collection are both
-- facts about the record, not reasons to drop it. Removal is recorded, never
-- deleted — an archive that silently loses rows cannot reconcile its counts,
-- and this query is how that promise is checked.
SELECT
    record_id,
    community,
    revisions,
    first_seen_utc,
    edited_utc,
    removed_utc,
    last_seen_utc,
    source_url
FROM problem_signal FINAL
WHERE revisions > 0 OR edited_utc IS NOT NULL OR removed_utc IS NOT NULL
ORDER BY coalesce(removed_utc, edited_utc) DESC;
