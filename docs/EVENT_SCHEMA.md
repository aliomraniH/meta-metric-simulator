# Event Schema (Layer 1)

The event substrate is a single append-only log.  Every interaction the
simulator produces — an impression served, a watch logged, an ad shown,
a guardrail firing — is one row in `events`.  Layer 7 (metrics) reads
this log via SQL templates; nothing else reads it directly.

## Identity & ordering

| Column           | Type             | Notes                                                                                  |
|------------------|------------------|----------------------------------------------------------------------------------------|
| `event_id`       | ULID (str, 26ch) | Lexically sortable; primary key.                                                       |
| `scenario_id`    | str(64) FK       | Scenario this event belongs to. Indexed.                                               |
| `tick_day`       | int              | Day index since scenario start (0-based). Indexed with scenario_id.                    |
| `intra_day_seq`  | int              | Per-tick deterministic ordering. Together with (tick_day, viewer_id) uniquely orders.  |
| `timestamp`      | float            | Wall-clock simulated seconds since scenario start. Monotonic within a viewer.          |

`(tick_day, intra_day_seq, event_type, viewer_id)` is the determinism
fingerprint.  The determinism test in M7 compares scenarios on this tuple,
NOT on `event_id` (which is wall-clock-derived and per-run unique).

## Actor & content

| Column                | Type            | Notes                                                                                         |
|-----------------------|-----------------|-----------------------------------------------------------------------------------------------|
| `event_type`          | str(32)         | Enum below.  Indexed.                                                                         |
| `viewer_id`           | str(64), null   | Who saw / did the thing.  Null for system events (`creator_post`, `scenario_perturbation`).   |
| `viewer_segment`      | str(32), null   | `teen`, `young_adult`, `snacker`, `lean_back`, …  Indexed.                                    |
| `viewer_geo`          | str(8), null    | ISO country code; null for non-viewer events.                                                 |
| `viewer_tenure_days`  | int, null       | Days since the viewer joined the platform at event time.                                      |
| `creator_id`          | str(64), null   | Author of the reel (or null when no reel involved).                                           |
| `creator_tier`        | str(16), null   | `head`, `mid`, `long_tail`.                                                                   |
| `reel_id`             | str(64), null   | The reel; null for `guardrail_fired` and `scenario_perturbation`.                             |
| `reel_duration_sec`   | float, null     | The reel's length, not the viewer's watch duration.                                           |
| `reel_topic_cluster`  | str(32), null   | Topic embedding cluster id.                                                                   |
| `surface`             | str(16), null   | `reels_tab`, `feed_inline`, `explore`, …                                                      |
| `pool`                | str(16), null   | `connected` or `unconnected`.  Set on impression / watch.                                     |
| `rank_score`          | float, null     | Score the ranker assigned to this reel for this viewer.  Set on impression.                   |

## Behaviour & monetization

| Column                 | Type           | Notes                                                                          |
|------------------------|----------------|--------------------------------------------------------------------------------|
| `watch_duration_sec`   | float, null    | How long the viewer watched.  Set on `watch`, `view_end`, `replay`, `skip`.    |
| `ad_impression`        | int (0/1), null| 1 when the row is itself an ad impression.  Set on `ad_impression`.            |
| `ad_revenue_usd`       | float, null    | Revenue attributable to this ad impression.                                    |
| `payload`              | JSONB, null    | Catch-all for event-specific extras (guardrail kind, perturbation params, …).  |

## Event types

The enum below is the contract.  Adding a new event type touches ONLY
`substrate/schema.sql` and this document.  No algorithm or metric needs
to change in lockstep.

| `event_type`              | Required fields                                                              | Optional fields                       | Emitted by (Layer 2)        |
|---------------------------|------------------------------------------------------------------------------|---------------------------------------|-----------------------------|
| `impression`              | viewer_id, viewer_segment, reel_id, creator_id, surface, pool, rank_score    | viewer_geo, viewer_tenure_days, creator_tier, reel_duration_sec, reel_topic_cluster | ranking                     |
| `watch`                   | viewer_id, reel_id, watch_duration_sec                                       | reel_duration_sec, payload            | engagement_response         |
| `skip`                    | viewer_id, reel_id, watch_duration_sec (≤3s)                                 |                                        | engagement_response         |
| `like`                    | viewer_id, reel_id                                                           |                                        | engagement_response         |
| `send`                    | viewer_id, reel_id                                                           | payload.recipient_kind                | engagement_response         |
| `save`                    | viewer_id, reel_id                                                           |                                        | engagement_response         |
| `comment`                 | viewer_id, reel_id                                                           | payload.comment_len                   | engagement_response         |
| `replay`                  | viewer_id, reel_id, watch_duration_sec                                       |                                        | engagement_response         |
| `view_end`                | viewer_id, reel_id, watch_duration_sec                                       | payload.completion_pct                | engagement_response         |
| `guardrail_fired`         | payload.guardrail_kind, payload.threshold, payload.observed                  | viewer_segment, viewer_geo            | guardrails                  |
| `ad_impression`           | viewer_id, viewer_segment, ad_impression=1, ad_revenue_usd                   | reel_id (the ad's creative)           | monetization                |
| `creator_post`            | creator_id, creator_tier, reel_id, reel_duration_sec, reel_topic_cluster     | payload.format                        | creator_response            |
| `scenario_perturbation`   | payload.kind, payload.target, payload.value                                  |                                        | perturbations               |

`payload` is the extension point.  When in doubt, push event-specific
fields there rather than adding new columns.

## Indexing strategy

The DDL in `substrate/schema.sql` provides:

  - `(scenario_id, tick_day)`        — the most common query shape ("events for scenario X on day Y").
  - `(scenario_id, viewer_id)`       — viewer-trajectory queries.
  - `(scenario_id, event_type)`      — type filters.
  - `(scenario_id, viewer_segment)`  — segment slices used by leading metrics.

These are intentionally narrow.  Composed metrics (M10) lean on the
indexes above plus query planner choices on `payload` — no dedicated
JSONB indexes by default.

## What lives elsewhere

The substrate knows nothing about ranking weights, segment definitions,
guardrail thresholds, ad load policy, or metric formulas.  All of those
are higher-layer concerns:

  - Layer 2 algorithms shape what events are emitted.
  - Layer 3 params control numeric behaviour.
  - Layer 4 baselines describe the external world (not the substrate).
  - Layer 7 metrics turn the log into KPIs via SQL.

A new metric that joins across event types (e.g. integrity-weighted
engagement) is a `metrics/definitions/composed/*.sql` file, not a
substrate change.
