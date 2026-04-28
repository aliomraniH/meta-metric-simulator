-- =============================================================================
-- Layer 1 — Event substrate
-- =============================================================================
-- Append-only log of every interaction the simulator produces.
-- See docs/EVENT_SCHEMA.md for the column-by-column rationale and the
-- event_type enum.
--
-- This file is the canonical DDL for the events table.  infra/db.py
-- mirrors it as a SQLAlchemy Table; the two definitions MUST match.
--
-- Postgres-flavoured.  Compatible with Replit Postgres add-ons.  We do
-- NOT support SQLite — Replit Deployment redeploys do not preserve
-- filesystem writes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id    VARCHAR(64)  PRIMARY KEY,
    name           VARCHAR(256) NOT NULL,
    description    TEXT,
    perturbations  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    horizon_days   INTEGER      NOT NULL,
    seed           BIGINT       NOT NULL,
    disputed       INTEGER      NOT NULL DEFAULT 0,
    confidence     DOUBLE PRECISION,
    created_at     DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id            VARCHAR(32) PRIMARY KEY,           -- ULID, 26 chars
    scenario_id         VARCHAR(64) NOT NULL REFERENCES scenarios(scenario_id),
    tick_day            INTEGER     NOT NULL,
    intra_day_seq       INTEGER     NOT NULL,
    timestamp           DOUBLE PRECISION NOT NULL,
    event_type          VARCHAR(32) NOT NULL,
    viewer_id           VARCHAR(64),
    viewer_segment      VARCHAR(32),
    viewer_geo          VARCHAR(8),
    viewer_tenure_days  INTEGER,
    creator_id          VARCHAR(64),
    creator_tier        VARCHAR(16),
    reel_id             VARCHAR(64),
    reel_duration_sec   DOUBLE PRECISION,
    reel_topic_cluster  VARCHAR(32),
    surface             VARCHAR(16),
    pool                VARCHAR(16),
    rank_score          DOUBLE PRECISION,
    watch_duration_sec  DOUBLE PRECISION,
    ad_impression       INTEGER,
    ad_revenue_usd      DOUBLE PRECISION,
    payload             JSONB
);

-- Most-common query shape: events for a scenario on a given day.
CREATE INDEX IF NOT EXISTS idx_events_scenario_day
    ON events (scenario_id, tick_day);

-- Viewer-trajectory queries.
CREATE INDEX IF NOT EXISTS idx_events_scenario_viewer
    ON events (scenario_id, viewer_id);

-- Type filters (e.g. count(*) where event_type = 'impression').
CREATE INDEX IF NOT EXISTS idx_events_scenario_type
    ON events (scenario_id, event_type);

-- Segment slicing used by leading metrics.
CREATE INDEX IF NOT EXISTS idx_events_scenario_segment
    ON events (scenario_id, viewer_segment);

-- Per-tick deterministic ordering — the determinism test compares scenarios
-- on (tick_day, intra_day_seq, event_type, viewer_id), not on event_id.
CREATE INDEX IF NOT EXISTS idx_events_ordering
    ON events (scenario_id, tick_day, intra_day_seq);
