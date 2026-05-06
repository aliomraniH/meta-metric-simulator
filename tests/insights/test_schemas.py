"""Tests for insights/schemas.py."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from insights.schemas import (
    Hypothesis,
    Narrative,
    NarratorFlag,
)


def _hypothesis(**overrides) -> Hypothesis:
    base = dict(
        anomaly_ref="ad_revenue_per_dau:14",
        hypothesis_text="The metric jumped to 42.7 on day 14, which is unusual.",
        confidence=0.7,
    )
    base.update(overrides)
    return Hypothesis(**base)


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------

def test_hypothesis_valid():
    h = _hypothesis()
    assert h.anomaly_ref == "ad_revenue_per_dau:14"
    assert h.confidence == pytest.approx(0.7)
    assert h.cited_supporting_scenarios == []


def test_hypothesis_confidence_bounds():
    with pytest.raises(ValidationError):
        _hypothesis(confidence=1.5)
    with pytest.raises(ValidationError):
        _hypothesis(confidence=-0.1)


def test_hypothesis_extra_forbid():
    with pytest.raises(ValidationError):
        Hypothesis(
            anomaly_ref="m:1",
            hypothesis_text="ok ok ok ok ok ok ok",
            confidence=0.5,
            mystery="bad",
        )


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def test_narrative_valid():
    n = Narrative(
        scenario_id="scn-A",
        metric_name="m",
        hypotheses=[_hypothesis()],
        overall_confidence=0.7,
    )
    assert n.scenario_id == "scn-A"
    assert len(n.hypotheses) == 1


def test_narrative_overall_confidence_bounds():
    with pytest.raises(ValidationError):
        Narrative(scenario_id="x", metric_name="m", hypotheses=[],
                  overall_confidence=1.5)
    with pytest.raises(ValidationError):
        Narrative(scenario_id="x", metric_name="m", hypotheses=[],
                  overall_confidence=-0.01)


# ---------------------------------------------------------------------------
# NarratorFlag
# ---------------------------------------------------------------------------

def test_narrator_flag_valid():
    f = NarratorFlag(kind="invented_number", severity="high",
                     evidence=["100% increase not in input"])
    assert f.kind == "invented_number"
    assert f.severity == "high"


def test_narrator_flag_kind_enum():
    with pytest.raises(ValidationError):
        NarratorFlag(kind="not_a_real_kind", severity="medium")


def test_narrator_flag_severity_enum():
    with pytest.raises(ValidationError):
        NarratorFlag(kind="weak_hypothesis", severity="catastrophic")


def test_narrator_flag_critical_not_allowed():
    """severity is the 4-value variant {info, low, medium, high}."""
    with pytest.raises(ValidationError):
        NarratorFlag(kind="invented_number", severity="critical")
