"""Tests for insights/tools/synthesize_insight.py — Layer 8 sole writer.

The architectural canary: the deterministic invented-number check
must catch hypotheses whose numbers don't trace back to input
Anomaly fields.  The check is what enforces "narrator MUST NOT
invent numbers" mechanically (PDF §3.6).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import insights.tools.synthesize_insight as syn_module
from infra.db import insights as insights_table, metadata
from insights.schemas import Hypothesis, Narrative


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )
    yield


@pytest.fixture
async def session_factory():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


@pytest.fixture
def patched_persist(monkeypatch, session_factory):
    async def _persist(row):
        async with session_factory() as s:
            await s.execute(insights_table.insert(), [row])
            await s.commit()
    monkeypatch.setattr(syn_module, "_persist_row", _persist)
    return session_factory


def _anomaly_input(observed: float = 200.0, expected: float = 100.0,
                   z: float = 4.5, tick: int = 14, metric: str = "rev") -> dict:
    return {
        "ref":            f"{metric}:{tick}",
        "metric_name":    metric,
        "tick_day":       tick,
        "observed_value": observed,
        "expected_value": expected,
        "z_score":        z,
        "direction":      "above" if z > 0 else "below",
    }


def _narrative(hypothesis_text: str, **overrides) -> Narrative:
    return Narrative(
        scenario_id="scn-A",
        metric_name="rev",
        hypotheses=[Hypothesis(
            anomaly_ref=overrides.get("anomaly_ref", "rev:14"),
            hypothesis_text=hypothesis_text,
            confidence=0.7,
        )],
        overall_confidence=overrides.get("overall_confidence", 0.7),
    )


async def _fetch_rows(factory):
    async with factory() as s:
        rows = (await s.execute(select(insights_table))).mappings().all()
    return rows


# ---------------------------------------------------------------------------
# 1. Clean narrative → row written, disputed=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_clean_narrative(patched_persist):
    """Hypothesis only mentions numbers that are in the Anomaly input."""
    n = _narrative("The metric jumped to 200.0 on day 14 (z=4.5), unusual.")
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=AsyncMock(),
    )
    assert result["disputed"] is False
    assert result["narrator_flags"] == []
    rows = await _fetch_rows(patched_persist)
    assert len(rows) == 1
    assert rows[0]["disputed"] == 0


# ---------------------------------------------------------------------------
# 2. Invented number → flagged, row still written (soft mode)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_flags_invented_number(patched_persist):
    """156 is NOT in the anomaly input → invented_number flag, disputed=True."""
    n = _narrative("Day 14 saw a 156% increase, unusual relative to baseline.")
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=AsyncMock(),
    )
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert "invented_number" in flag_kinds
    assert result["disputed"] is True
    # Soft-mode contract: the row IS still written with disputed=True.
    rows = await _fetch_rows(patched_persist)
    assert len(rows) == 1
    assert rows[0]["disputed"] == 1


# ---------------------------------------------------------------------------
# 3. Missing anomaly_ref → flagged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_flags_missing_anomaly_ref(patched_persist):
    """Hypothesis cites an anomaly_ref that wasn't in the input."""
    n = _narrative(
        "The metric on day 14 was unusual.",
        anomaly_ref="rev:99",
    )
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input(tick=14)],
        ctx=AsyncMock(),
    )
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert "missing_anomaly_ref" in flag_kinds
    assert result["disputed"] is True


# ---------------------------------------------------------------------------
# 4. Weak hypothesis → medium-severity flag, disputed remains False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_flags_weak_hypothesis(patched_persist):
    """Short hypothesis_text (<20 chars) → medium flag, NOT disputed."""
    n = _narrative("Short.")
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=AsyncMock(),
    )
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert "weak_hypothesis" in flag_kinds
    # Medium severity does NOT trigger disputed.
    assert result["disputed"] is False
    rows = await _fetch_rows(patched_persist)
    assert rows[0]["disputed"] == 0


# ---------------------------------------------------------------------------
# 5. Soft-mode write-regardless contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_regardless_of_flags(patched_persist):
    """Even with critical-equivalent flags, the row is still written."""
    n = _narrative(
        "Day 99 saw a 555% increase from a 333 baseline, unusual.",
        anomaly_ref="rev:99",
    )
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input(tick=14)],
        ctx=AsyncMock(),
    )
    # Both invented_number AND missing_anomaly_ref fire.
    rows = await _fetch_rows(patched_persist)
    assert len(rows) == 1
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert {"invented_number", "missing_anomaly_ref"} <= flag_kinds


# ---------------------------------------------------------------------------
# 6. disputed_by carries the flag kinds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_disputed_by_records_flag_kinds(patched_persist):
    n = _narrative(
        "Day 14 saw a 555% increase, unusual.",
    )
    await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=AsyncMock(),
    )
    rows = await _fetch_rows(patched_persist)
    disputed_by = rows[0]["disputed_by"]
    if isinstance(disputed_by, str):
        import json as _json
        disputed_by = _json.loads(disputed_by)
    assert "invented_number" in (disputed_by or [])


# ---------------------------------------------------------------------------
# 7. Calibration drift aborts BEFORE the model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calibration_drift_aborts(patched_persist, monkeypatch):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )
    n = _narrative("The metric jumped to 200.0 on day 14, unusual.")
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_insight(
            narrative=n,
            anomalies_input=[_anomaly_input()],
            ctx=AsyncMock(),
        )
    rows = await _fetch_rows(patched_persist)
    assert rows == []


# ---------------------------------------------------------------------------
# 8. Soft-mode: never calls ctx.elicit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_does_not_elicit(patched_persist):
    n = _narrative("The metric jumped to 200.0 on day 14, unusual.")
    ctx = AsyncMock()
    ctx.elicit = AsyncMock(return_value=False)
    await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=ctx,
    )
    assert ctx.elicit.await_count == 0


# ---------------------------------------------------------------------------
# 9. Tool registration — NOT readOnlyHint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_registered_not_readonly():
    from insights.server import insights_server
    tools = await insights_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "synthesize_insight" in by_name
    expected = {
        "find_similar_scenarios", "narrate_anomalies", "synthesize_insight",
    }
    assert expected <= set(by_name)
    annotations = by_name["synthesize_insight"].annotations
    assert getattr(annotations, "readOnlyHint", None) is not True


# ---------------------------------------------------------------------------
# 10. Acceptable contextual numbers (years, small qualifiers) DON'T flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_acceptable_contextual_numbers(patched_persist):
    """Years 2024/2025/2026 and digits 0-10 are acceptable contextual numbers."""
    n = _narrative(
        "On day 14 of the 2026 simulation, the metric was unusual; "
        "this is the 2nd anomaly this quarter. Z-score 4.5 is the largest of 3 in the run."
    )
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_input()],
        ctx=AsyncMock(),
    )
    # 2026, 2, 14, 3 — all acceptable.
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert "invented_number" not in flag_kinds
    assert result["disputed"] is False


# ---------------------------------------------------------------------------
# 11. Helper checks — _check_hypothesis_invented_numbers + friends
# ---------------------------------------------------------------------------

def test_invented_number_check_pure():
    h = Hypothesis(anomaly_ref="rev:14",
                   hypothesis_text="The 156% increase is unusual.",
                   confidence=0.5)
    flags = syn_module._check_hypothesis_invented_numbers(
        h, allowed_numbers={"100", "200.0", "4.5", "14"}
    )
    assert len(flags) == 1
    assert flags[0].kind == "invented_number"
    assert flags[0].severity == "high"


def test_invented_number_check_pure_clean():
    h = Hypothesis(anomaly_ref="rev:14",
                   hypothesis_text="Observed 200.0 (z=4.5) on day 14.",
                   confidence=0.5)
    flags = syn_module._check_hypothesis_invented_numbers(
        h, allowed_numbers={"200.0", "4.5", "14", "100", "100.0"}
    )
    assert flags == []
