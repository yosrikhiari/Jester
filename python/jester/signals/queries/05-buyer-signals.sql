-- The deliverable: buyer signals, newest first, with the link and the reason.
--
-- One row per problem someone described, the permalink that proves it, and the
-- words the classifier matched — so the reason a record is here can be checked
-- against the post itself in one click. company and buyer_intent stay
-- 'unknown': a post is a problem, never evidence of a budget.
SELECT
    first_seen_utc,
    community,
    kind,
    title,
    excerpt,
    match_reason,
    round(match_confidence, 2) AS confidence,
    source_url,
    query
FROM problem_signal FINAL
WHERE audience = 'buyer'
  AND mode = 'live'
  AND removed_utc IS NULL
ORDER BY first_seen_utc DESC, match_confidence DESC
LIMIT 200;
