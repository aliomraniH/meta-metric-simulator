-- !METRIC
-- name: ad_impressions_per_session
-- layer: leading
-- unit: count
-- description: |
--   Mean ad_impression events per distinct viewer per tick.  Proxy for
--   ad load — Meta does not publicly disclose per-session ad load
--   directly (reels_metrics_comprehensive_v2.md §0 item 10).
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(CASE WHEN event_type = 'ad_impression' THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(COUNT(DISTINCT viewer_id), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
