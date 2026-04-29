-- !METRIC
-- name: sends_per_reach
-- layer: leading
-- unit: ratio
-- description: |
--   Number of unique senders divided by unique impressioned viewers.
--   Mosseri-anchored top-3 ranking signal — sends weighted ~3-5× likes
--   for unconnected reach per reels_metrics_comprehensive_v2.md §10.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - pool
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(COUNT(DISTINCT CASE WHEN event_type = 'send' THEN viewer_id END) AS REAL) /
  NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'impression' THEN viewer_id END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
