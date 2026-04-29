-- !METRIC
-- name: creator_posts_per_active_creator
-- layer: leading
-- unit: count
-- description: |
--   Mean number of creator_post events per active creator per group.
--   The §11.5 calibration anchor for creator earnings → posting frequency.
--   Emitted by engine/tick.py (M11.5b) when algorithms/creator_response
--   computes posts_today > 0.  An "active creator" is one with at least
--   one creator_post event in the group window — denominator counts only
--   creators who posted, so the metric measures intensity of posting
--   among the still-active cohort, not gross supply collapse.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - creator_tier
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(CASE WHEN event_type = 'creator_post' THEN 1 ELSE 0 END) AS REAL) /
  NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'creator_post' THEN creator_id END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
