-- !METRIC
-- name: completion_rate
-- layer: leading
-- unit: pct
-- description: |
--   Share of impressions where view_end watch_duration_sec >=
--   0.95 * reel_duration_sec.  Captures full-reel viewing, not just
--   play-through.  The captioned-vs-uncaptioned completion split in
--   reels_metrics_comprehensive_v2.md §4.4 (~65% vs ~37%) anchors the
--   industry baseline range.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - creator_tier
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(SUM(
    CASE
      WHEN event_type = 'view_end'
        AND reel_duration_sec IS NOT NULL
        AND reel_duration_sec > 0
        AND watch_duration_sec >= 0.95 * reel_duration_sec
      THEN 1 ELSE 0
    END
  ) AS REAL) /
  NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
