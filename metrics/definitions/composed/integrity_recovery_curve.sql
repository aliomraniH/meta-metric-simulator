-- !METRIC
-- name: integrity_recovery_curve
-- layer: composed
-- unit: ratio
-- description: |
--   Ratio of violating_view_share at tick_day t to baseline (mean over
--   the first 3 ticks of the scenario).  Used by calibration test_03
--   (Feb 26 2025 incident replay per
--   reels_metrics_comprehensive_v2.md §4.7) to verify the recovery
--   curve returns to baseline within 14 days post-spike.  Per §0
--   item 9, no specific peak prevalence is asserted — the curve only
--   tracks departure from and return to the per-scenario baseline.
-- owner: metrics
-- parameters:
--   - tick_day_start
--   - tick_day_end
-- group_by_dimensions:
--   - tick_day
-- !END
-- Composed: joins per-tick violating_view_share against a scenario-wide
-- baseline computed over the first 3 ticks.  Both halves come from the
-- same `events` table but with different aggregation windows, which is
-- the cross-window join a leading metric cannot express.
-- Portable across SQLite and Postgres — uses subqueries with CROSS JOIN
-- rather than CTEs; payload check uses LIKE to avoid JSONB-only operators.
SELECT
  {group_by} AS dim,
  ratio       AS value
FROM (
  SELECT
    pt.tick_day AS tick_day,
    CASE
      WHEN bl.baseline_share IS NULL OR bl.baseline_share = 0 THEN 1.0
      ELSE pt.tick_share / bl.baseline_share
    END AS ratio
  FROM (
    -- Per-tick violating share within the requested window.
    SELECT
      tick_day,
      CAST(SUM(
        CASE
          WHEN event_type = 'impression'
            AND payload IS NOT NULL
            AND payload LIKE '%"violating": true%'
          THEN 1 ELSE 0
        END
      ) AS REAL) /
      NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0)
        AS tick_share
    FROM events
    WHERE scenario_id = :scenario_id
      AND tick_day BETWEEN :tick_day_start AND :tick_day_end
    GROUP BY tick_day
  ) pt
  CROSS JOIN (
    -- Baseline: violating share aggregated across the first 3 ticks of
    -- the scenario.  Independent of the user-supplied window.
    SELECT
      CAST(SUM(
        CASE
          WHEN event_type = 'impression'
            AND payload IS NOT NULL
            AND payload LIKE '%"violating": true%'
          THEN 1 ELSE 0
        END
      ) AS REAL) /
      NULLIF(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0)
        AS baseline_share
    FROM events
    WHERE scenario_id = :scenario_id
      AND tick_day < 3
  ) bl
) curve
ORDER BY {group_by};
