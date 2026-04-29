-- !METRIC
-- name: like_rate
-- layer: leading
-- unit: pct
-- description: |
--   Like events / impressions.  Mosseri-anchored ranking signal; "likes
--   per reach" is one of the three metrics Instagram surfaces to
--   creators (reels_metrics_comprehensive_v2.md §10).  Likes weight
--   slightly more for connected content per Mosseri Jan 22 2025.
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
  CAST(SUM(CASE WHEN event_type = 'like' THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
