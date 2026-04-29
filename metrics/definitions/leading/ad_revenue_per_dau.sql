-- !METRIC
-- name: ad_revenue_per_dau
-- layer: leading
-- unit: usd
-- description: |
--   Sum of ad_revenue_usd / count of distinct active viewers.  Used by
--   calibration test_01 (time-share growth → revenue delta) and
--   test_02 (ad-load ramp → session length).  Anchored against Q4 2025
--   ad impressions +18% YoY / price per ad +6% YoY
--   (reels_metrics_comprehensive_v2.md §1.5).
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(COALESCE(SUM(ad_revenue_usd), 0) AS REAL) /
  NULLIF(COUNT(DISTINCT viewer_id), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
