-- Which communities actually produce buyers, and which produce content?
--
-- The source-quality table. `buyer_rate` is the number that decides whether a
-- community stays in scope: a room with a rate near zero is costing requests
-- and returning nothing outreach can act on, however busy it looks.
SELECT
    community,
    count()                                        AS records,
    countIf(audience = 'buyer')                    AS buyer,
    countIf(audience = 'practitioner')             AS practitioner,
    round(countIf(audience = 'buyer') / count(), 4) AS buyer_rate,
    round(avgIf(match_confidence, audience = 'buyer'), 3) AS avg_buyer_confidence
FROM problem_signal FINAL
WHERE mode = 'live'
GROUP BY community
ORDER BY buyer DESC, records DESC;
