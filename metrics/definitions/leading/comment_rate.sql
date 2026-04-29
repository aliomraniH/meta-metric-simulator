-- !METRIC
-- name: comment_rate
-- layer: leading
-- unit: pct
-- description: |
--   Comment events / impressions.  Industry baseline ~0.05% on YouTube
--   Shorts per reels_metrics_comprehensive_v2.md §5.2; Reels per-segment
--   propensities in §9.3 modulate this.
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
  CAST(SUM(CASE WHEN event_type = 'comment' THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
