-- !METRIC
-- name: violating_view_share
-- layer: leading
-- unit: pct
-- description: |
--   Share of impressions where the underlying reel was tagged violating
--   in payload.  Calibration test_03 (Feb 26 2025 incident anchor in
--   reels_metrics_comprehensive_v2.md §4.7) reads this metric.  Per §0
--   item 9 we do NOT use a specific peak prevalence number — the
--   calibration test only asserts a spike above the per-tick baseline.
--   The check uses payload-substring matching to stay portable across
--   SQLite (test substrate) and Postgres (production); a JSONB-native
--   form is a future enhancement.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - viewer_geo
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(
    CASE
      WHEN event_type = 'impression'
        AND payload IS NOT NULL
        AND payload LIKE '%"violating": true%'
      THEN 1 ELSE 0
    END
  ) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
