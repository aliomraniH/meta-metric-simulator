"""Metric runtime tests.

Covers parser / compiler validation, runtime auto-discovery, and live
metric execution against a small M7 scenario fingerprint.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from engine.scenario import Scenario
from engine.simulator import Simulator
from infra.db import metadata
from metrics.compiler import (
    MetricCompilationError,
    MetricNotFound,
    MetricValidationError,
    compile_metric,
    parse_metric_file,
)
from metrics.runtime import MetricRuntime


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DEFINITIONS = REPO_ROOT / "metrics" / "definitions"
PARAMS_DIR = REPO_ROOT / "params"

EXPECTED_LEADING_METRIC_NAMES: set[str] = {
    "sends_per_reach",
    "skip_rate_3s",
    "completion_rate",
    "like_rate",
    "save_rate",
    "comment_rate",
    "ad_impressions_per_session",
    "ad_revenue_per_dau",
    "violating_view_share",
    "creator_posts_per_active_creator",  # M11.5d — calibration test_05 anchor
}


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
async def session_factory():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


@pytest.fixture
def runtime(session_factory):
    return MetricRuntime(session_factory, definitions_dir=LIVE_DEFINITIONS)


# -----------------------------------------------------------------------------
# 1. Discovery
# -----------------------------------------------------------------------------

def test_runtime_discovers_all_leading_metrics(runtime):
    leading = runtime.list_metrics(layer="leading")
    names = {m.name for m in leading}
    assert names == EXPECTED_LEADING_METRIC_NAMES, (
        f"missing: {EXPECTED_LEADING_METRIC_NAMES - names}; "
        f"unexpected: {names - EXPECTED_LEADING_METRIC_NAMES}"
    )
    expected_count = len(EXPECTED_LEADING_METRIC_NAMES)
    assert runtime.stats["files_loaded"] >= expected_count
    assert runtime.stats["by_layer"]["leading"] == expected_count


# -----------------------------------------------------------------------------
# 2. Frontmatter parsing
# -----------------------------------------------------------------------------

def test_metric_definition_parsing_extracts_frontmatter():
    for name in EXPECTED_LEADING_METRIC_NAMES:
        path = LIVE_DEFINITIONS / "leading" / f"{name}.sql"
        d = parse_metric_file(path)
        assert d.name == name
        assert d.layer == "leading"
        assert d.unit
        assert d.description
        assert d.owner == "metrics"
        assert isinstance(d.parameters, tuple)
        assert isinstance(d.group_by_dimensions, tuple)
        assert len(d.group_by_dimensions) > 0
        # Body sanity: contains :scenario_id
        assert ":scenario_id" in d.sql_body


# -----------------------------------------------------------------------------
# 3. Validation rejections
# -----------------------------------------------------------------------------

def _write_metric(tmp_path: Path, name: str, sql: str, layer: str = "leading") -> Path:
    leading_dir = tmp_path / "definitions" / layer
    leading_dir.mkdir(parents=True, exist_ok=True)
    body = dedent(f"""\
        -- !METRIC
        -- name: {name}
        -- layer: {layer}
        -- unit: ratio
        -- description: test
        -- owner: metrics
        -- parameters: []
        -- group_by_dimensions:
        --   - tick_day
        -- !END
        {sql}
    """)
    path = leading_dir / f"{name}.sql"
    path.write_text(body)
    return path


def test_validation_rejects_string_interpolation(tmp_path):
    path = _write_metric(
        tmp_path, "bad_pct",
        "SELECT 1 AS dim FROM events WHERE scenario_id = :scenario_id AND event_type = %s",
    )
    with pytest.raises(MetricCompilationError, match="forbidden %"):
        compile_metric(parse_metric_file(path))


def test_validation_requires_scenario_id(tmp_path):
    path = _write_metric(
        tmp_path, "bad_no_scn",
        "SELECT 1 AS dim FROM events GROUP BY tick_day",
    )
    with pytest.raises(MetricValidationError, match=":scenario_id"):
        compile_metric(parse_metric_file(path))


def test_validation_rejects_unknown_table(tmp_path):
    path = _write_metric(
        tmp_path, "bad_table",
        "SELECT 1 AS dim FROM users WHERE scenario_id = :scenario_id",
    )
    with pytest.raises(MetricCompilationError, match="non-substrate"):
        compile_metric(parse_metric_file(path))


def test_validation_rejects_unknown_group_by_in_frontmatter(tmp_path):
    """A metric whose frontmatter declares a non-whitelisted dimension fails parse."""
    leading_dir = tmp_path / "definitions" / "leading"
    leading_dir.mkdir(parents=True, exist_ok=True)
    body = dedent("""\
        -- !METRIC
        -- name: bad_dim
        -- layer: leading
        -- unit: ratio
        -- description: test
        -- owner: metrics
        -- parameters: []
        -- group_by_dimensions:
        --   - creator_id
        -- !END
        SELECT 1 AS dim FROM events WHERE scenario_id = :scenario_id
    """)
    path = leading_dir / "bad_dim.sql"
    path.write_text(body)
    with pytest.raises(MetricValidationError, match="not in closed whitelist"):
        parse_metric_file(path)


def test_validation_rejects_curly_placeholder_other_than_group_by(tmp_path):
    path = _write_metric(
        tmp_path, "bad_curly",
        "SELECT {hack} AS dim FROM events WHERE scenario_id = :scenario_id",
    )
    with pytest.raises(MetricCompilationError, match="forbidden curly"):
        compile_metric(parse_metric_file(path))


def test_validation_rejects_ddl(tmp_path):
    path = _write_metric(
        tmp_path, "bad_ddl",
        "DELETE FROM events WHERE scenario_id = :scenario_id",
    )
    with pytest.raises(MetricCompilationError, match="forbidden DDL"):
        compile_metric(parse_metric_file(path))


# -----------------------------------------------------------------------------
# 4. Live compute against a small M7 scenario
# -----------------------------------------------------------------------------

@pytest.fixture
async def small_scenario_id(session_factory):
    """Run a 3-day scenario via the M7 simulator and return scenario_id."""
    sim = Simulator(
        session_factory,
        params_dir=PARAMS_DIR,
        viewers_n=12,
        creators_n=6,
        reels_n=30,
    )
    s = Scenario(name="metrics-fixture", horizon_days=3, seed=42)
    await sim.run(s)
    return s.scenario_id


@pytest.mark.asyncio
async def test_compute_runs_against_substrate(runtime, small_scenario_id):
    """Each of the 9 metrics computes successfully against a small scenario."""
    for name in EXPECTED_LEADING_METRIC_NAMES:
        rows = await runtime.compute(name, scenario_id=small_scenario_id)
        # Substrate has events; tick_day default group_by → at least 1 row.
        assert isinstance(rows, list)
        assert len(rows) >= 1, f"metric {name} returned no rows"
        for r in rows:
            assert "dim" in r
            # value may be None when the metric divides by zero (e.g. no
            # impressions in some tick); that is allowed by the SQL NULLIF.
            assert "value" in r


@pytest.mark.asyncio
async def test_compute_with_group_by_returns_multiple_segments(runtime, small_scenario_id):
    rows = await runtime.compute(
        "sends_per_reach",
        scenario_id=small_scenario_id,
        group_by="viewer_segment",
    )
    segments = {r["dim"] for r in rows if r["dim"] is not None}
    # Small population (12 viewers) but five-segment distribution should
    # cover at least 2 segments.
    assert len(segments) >= 2, f"expected ≥2 segments, got {segments}"


@pytest.mark.asyncio
async def test_compute_handles_empty_substrate(runtime):
    rows = await runtime.compute(
        "sends_per_reach",
        scenario_id="scenario-that-doesnt-exist",
    )
    # No rows match the scenario_id → empty result, no exception.
    assert rows == []


@pytest.mark.asyncio
async def test_metric_not_found_lists_available(runtime, small_scenario_id):
    with pytest.raises(MetricNotFound) as excinfo:
        await runtime.compute("bogus_metric_name", scenario_id=small_scenario_id)
    msg = str(excinfo.value)
    # Every available metric name must appear in the message.
    for name in EXPECTED_LEADING_METRIC_NAMES:
        assert name in msg, f"metric {name} missing from not-found message"


# -----------------------------------------------------------------------------
# 5. Misc validation behaviours
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_rejects_group_by_outside_metric_dimensions(runtime, small_scenario_id):
    # sends_per_reach declares group_by_dimensions [viewer_segment, surface,
    # pool, tick_day] — creator_tier is in the closed whitelist but NOT
    # in the metric's declared list, so the metric must reject it.
    with pytest.raises(MetricValidationError, match="does not support group_by"):
        await runtime.compute(
            "sends_per_reach",
            scenario_id=small_scenario_id,
            group_by="creator_tier",
        )


@pytest.mark.asyncio
async def test_compute_rejects_group_by_outside_whitelist(runtime, small_scenario_id):
    with pytest.raises(MetricValidationError, match="closed whitelist"):
        await runtime.compute(
            "sends_per_reach",
            scenario_id=small_scenario_id,
            group_by="event_id",  # not in any whitelist
        )


@pytest.mark.asyncio
async def test_compute_rejects_undeclared_extra_params(runtime, small_scenario_id):
    with pytest.raises(MetricValidationError, match="does not accept parameters"):
        await runtime.compute(
            "sends_per_reach",
            scenario_id=small_scenario_id,
            tick_day_start=0,  # this metric has parameters: []
        )
