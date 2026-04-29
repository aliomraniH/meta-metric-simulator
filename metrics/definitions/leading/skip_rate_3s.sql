-- !METRIC
-- name: skip_rate_3s
-- layer: leading
-- unit: pct
-- description: |
--   Share of impressions where watch_duration < 3 seconds.  The 3-second
--   skip metric is what Instagram now exposes to creators (Aug 2025
--   "Skip Rate" metric per reels_metrics_comprehensive_v2.md §4.5).
--   Calibration test_02 anchors on the change in this metric under an
--   ad-load ramp.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - tick_day
--   - reel_topic_cluster
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(CASE WHEN event_type = 'skip' AND watch_duration_sec < 3.0 THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
