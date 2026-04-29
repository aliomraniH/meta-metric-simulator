-- !METRIC
-- name: quality_funnel
-- layer: composed
-- unit: pct
-- description: |
--   Funnel from impression to 3-second-watch to completion to engagement.
--   Returns survival rate at each stage as a single result set.  Mosseri
--   ranking signals (watch_time, likes/sends per reach) are the per-stage
--   anchors, per reels_metrics_comprehensive_v2.md §10.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - creator_tier
--   - tick_day
-- !END
-- Composed: requires aggregation across impression, watch/view_end, and
-- engagement event types within the same scenario.  The funnel is
-- monotonically decreasing by construction — each later stage's
-- numerator is the prior stage's numerator AND-conjuncted with an
-- additional condition, so stage1 >= stage2 >= stage3 always holds.
SELECT
  {group_by} AS dim,
  SUM(had_impression) AS stage0_impressions,
  CAST(SUM(CASE WHEN had_impression = 1 AND past_3s   = 1 THEN 1 ELSE 0 END) AS REAL) /
    NULLIF(SUM(had_impression), 0) AS stage1_3s_pct,
  CAST(SUM(CASE WHEN had_impression = 1 AND past_3s   = 1 AND completed = 1 THEN 1 ELSE 0 END) AS REAL) /
    NULLIF(SUM(had_impression), 0) AS stage2_completion_pct,
  CAST(SUM(CASE WHEN had_impression = 1 AND past_3s   = 1 AND completed = 1 AND engaged = 1 THEN 1 ELSE 0 END) AS REAL) /
    NULLIF(SUM(had_impression), 0) AS stage3_engagement_pct
FROM (
  -- One row per (tick_day, viewer_id, reel_id, viewer_segment, creator_tier)
  -- with boolean flags marking which funnel stages that session reached.
  SELECT
    tick_day,
    viewer_id,
    reel_id,
    viewer_segment,
    creator_tier,
    MAX(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END) AS had_impression,
    MAX(CASE
          WHEN event_type IN ('watch', 'view_end', 'replay')
            AND watch_duration_sec IS NOT NULL
            AND watch_duration_sec >= 3.0
          THEN 1 ELSE 0
        END) AS past_3s,
    MAX(CASE
          WHEN event_type = 'view_end'
            AND reel_duration_sec IS NOT NULL
            AND reel_duration_sec > 0
            AND watch_duration_sec >= 0.95 * reel_duration_sec
          THEN 1 ELSE 0
        END) AS completed,
    MAX(CASE
          WHEN event_type IN ('like', 'send', 'save', 'comment')
          THEN 1 ELSE 0
        END) AS engaged
  FROM events
  WHERE scenario_id = :scenario_id
  GROUP BY tick_day, viewer_id, reel_id, viewer_segment, creator_tier
) sessions
GROUP BY {group_by}
ORDER BY {group_by};
