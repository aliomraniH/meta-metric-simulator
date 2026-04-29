-- !METRIC
-- name: save_rate
-- layer: leading
-- unit: pct
-- description: |
--   Save events / impressions.  Defining behaviour of the shoppers
--   segment (3× platform avg per reels_metrics_comprehensive_v2.md
--   §9.3) — used by interview question gen to surface segment-shape
--   differences.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - creator_tier
--   - tick_day
--   - reel_topic_cluster
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(CASE WHEN event_type = 'save' THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
