-- !METRIC
-- name: monetization_per_active_creator
-- layer: composed
-- unit: usd
-- description: |
--   Sum of ad_revenue_usd attributed to a creator's reels divided by
--   count of distinct creators with at least one impression.  Joins
--   ad_impression events to creator_post events via reel_id.  Calibration
--   test_05 anchor (creator earnings → posting frequency, per
--   reels_metrics_comprehensive_v2.md §11.5).
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - creator_tier
--   - tick_day
-- !END
-- Composed: requires JOIN of ad_impression (revenue) AND creator_post
-- (attribution) event types.  This is what makes the metric composed —
-- a leading metric reads one event type at a time; this one cross-joins
-- two within the same scenario via reel_id.
SELECT
  {group_by} AS dim,
  CAST(COALESCE(SUM(revenue_usd), 0) AS REAL) /
  NULLIF(COUNT(DISTINCT creator_id), 0) AS value
FROM (
  SELECT
    cp.creator_id   AS creator_id,
    cp.creator_tier AS creator_tier,
    ai.tick_day     AS tick_day,
    SUM(ai.ad_revenue_usd) AS revenue_usd
  FROM events ai
  JOIN events cp
    ON cp.scenario_id = ai.scenario_id
   AND cp.event_type = 'creator_post'
   AND cp.reel_id    = ai.reel_id
  WHERE ai.scenario_id = :scenario_id
    AND ai.event_type  = 'ad_impression'
    AND ai.reel_id IS NOT NULL
    AND ai.ad_revenue_usd IS NOT NULL
  GROUP BY cp.creator_id, cp.creator_tier, ai.tick_day
) per_creator
GROUP BY {group_by}
ORDER BY {group_by};
