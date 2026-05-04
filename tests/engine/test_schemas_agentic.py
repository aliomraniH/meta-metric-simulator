"""Tests for engine/schemas_agentic.py."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.schemas_agentic import (
    AudienceFilter,
    CompiledScenario,
    EvaluatorVerdict,
    Perturbation,
)


# ---------------------------------------------------------------------------
# Perturbation
# ---------------------------------------------------------------------------

def test_perturbation_op_enum():
    """op outside the closed set must be rejected."""
    with pytest.raises(ValidationError):
        Perturbation(target="ad_load_pct", op="not_an_op", value=1.0,
                     rationale="test")


def test_perturbation_valid_op():
    p = Perturbation(target="ad_load_pct", op="multiply", value=1.25,
                     rationale="Q4 ad demand uplift")
    assert p.op == "multiply"


# ---------------------------------------------------------------------------
# AudienceFilter
# ---------------------------------------------------------------------------

def test_audience_filter_dim_enum():
    """dim outside the closed set must be rejected."""
    with pytest.raises(ValidationError):
        AudienceFilter(dim="age", op="lt", value=18)


def test_audience_filter_op_enum():
    """op outside {eq, in, gt, lt} must be rejected."""
    with pytest.raises(ValidationError):
        AudienceFilter(dim="viewer_segment", op="contains", value="teens_13_17")


def test_audience_filter_valid():
    f = AudienceFilter(dim="viewer_segment", op="eq", value="teens_13_17")
    assert f.dim == "viewer_segment"
    assert f.value == "teens_13_17"


# ---------------------------------------------------------------------------
# CompiledScenario
# ---------------------------------------------------------------------------

def _scenario(**overrides) -> CompiledScenario:
    base = dict(
        name="test_scenario",
        description="A test scenario",
        natural_language_intent="what if foo?",
        perturbations=[],
        time_horizon="28d",
        audience_filters=[],
        confidence=0.85,
    )
    base.update(overrides)
    return CompiledScenario(**base)


def test_compiled_scenario_time_horizon_enum():
    """time_horizon must be in {1d, 7d, 28d, 90d}."""
    with pytest.raises(ValidationError):
        _scenario(time_horizon="14d")
    with pytest.raises(ValidationError):
        _scenario(time_horizon="forever")


def test_compiled_scenario_confidence_bounds():
    with pytest.raises(ValidationError):
        _scenario(confidence=1.5)
    with pytest.raises(ValidationError):
        _scenario(confidence=-0.1)


def test_compiled_scenario_default_seed():
    s = _scenario()
    assert s.seed == 42


def test_compiled_scenario_extra_forbid():
    """extra='forbid' — unknown fields rejected."""
    with pytest.raises(ValidationError):
        CompiledScenario(
            name="x", description="y", natural_language_intent="z",
            time_horizon="1d", confidence=0.5,
            mystery_field="bad",
        )


def test_compiled_scenario_preserves_natural_language_intent():
    """The user's prompt must round-trip byte-for-byte."""
    intent = "what if Reels-only mode launched for teens for 90 days?"
    s = _scenario(natural_language_intent=intent)
    assert s.natural_language_intent == intent


# ---------------------------------------------------------------------------
# EvaluatorVerdict
# ---------------------------------------------------------------------------

def test_evaluator_verdict_score_bounds():
    with pytest.raises(ValidationError):
        EvaluatorVerdict(score=1.5)
    with pytest.raises(ValidationError):
        EvaluatorVerdict(score=-0.1)


def test_evaluator_verdict_passes_property():
    """`passes` is True iff score >= accept_threshold (default 0.7)."""
    above = EvaluatorVerdict(score=0.8)
    assert above.passes is True
    below = EvaluatorVerdict(score=0.6)
    assert below.passes is False


def test_evaluator_verdict_passes_with_custom_threshold():
    v = EvaluatorVerdict(score=0.65, accept_threshold=0.6)
    assert v.passes is True


def test_evaluator_verdict_rubric_breakdown_is_dict():
    v = EvaluatorVerdict(
        score=0.85,
        rubric_breakdown={
            "schema_validity": 1.0,
            "perturbation_realism": 0.7,
            "simulator_constrainability": 0.9,
            "audience_filter_coherence": 0.8,
        },
    )
    assert v.rubric_breakdown["schema_validity"] == pytest.approx(1.0)
