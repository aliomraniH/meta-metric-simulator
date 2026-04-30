"""Schema validation tests for baselines/schemas.py.

Pins the closed enums and the [0, 1] bound on every confidence field.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from baselines.schemas import (
    ExtractedMetric,
    ExtractedMetricSource,
    SanitizerFlag,
    SynthesizerDecision,
)


def _good_source() -> ExtractedMetricSource:
    return ExtractedMetricSource(
        doc_title="Meta Q4 2025 press release",
        page=3,
        quoted_text="DAP of 3.58 billion, +7% YoY",
        url="https://investor.atmeta.com/...",
    )


# -----------------------------------------------------------------------------
# ExtractedMetric
# -----------------------------------------------------------------------------

def test_extracted_metric_valid():
    m = ExtractedMetric(
        metric_id="dap_billion",
        value=3.58,
        unit="users_billion",
        period="Dec 2025",
        source=_good_source(),
        confidence=0.99,
    )
    assert m.metric_id == "dap_billion"
    assert m.value == 3.58


def test_extracted_metric_confidence_above_one_rejected():
    with pytest.raises(ValidationError):
        ExtractedMetric(
            metric_id="x", value=1.0, unit="ratio", period="x",
            source=_good_source(), confidence=1.5,
        )


def test_extracted_metric_confidence_negative_rejected():
    with pytest.raises(ValidationError):
        ExtractedMetric(
            metric_id="x", value=1.0, unit="ratio", period="x",
            source=_good_source(), confidence=-0.1,
        )


def test_extracted_metric_unit_outside_enum_rejected():
    with pytest.raises(ValidationError, match="not in allowed set"):
        ExtractedMetric(
            metric_id="x", value=1.0, unit="furlongs", period="x",
            source=_good_source(), confidence=0.5,
        )


def test_extracted_metric_extra_field_rejected():
    """extra='forbid' on the model_config rejects unknown fields."""
    with pytest.raises(ValidationError):
        ExtractedMetric(
            metric_id="x", value=1.0, unit="ratio", period="x",
            source=_good_source(), confidence=0.5,
            secret_field="sneaky",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# SanitizerFlag
# -----------------------------------------------------------------------------

def test_sanitizer_flag_severity_outside_enum_rejected():
    with pytest.raises(ValidationError):
        SanitizerFlag(
            kind="schema", severity="catastrophic",  # type: ignore[arg-type]
            evidence=["x"],
        )


def test_sanitizer_flag_kind_outside_enum_rejected():
    with pytest.raises(ValidationError):
        SanitizerFlag(
            kind="vibes", severity="info",  # type: ignore[arg-type]
            evidence=[],
        )


def test_sanitizer_flag_minimal():
    f = SanitizerFlag(kind="schema", severity="info")
    assert f.evidence == []
    assert f.span is None
    assert f.suggested_fix is None


# -----------------------------------------------------------------------------
# SynthesizerDecision
# -----------------------------------------------------------------------------

def test_synthesizer_decision_valid():
    d = SynthesizerDecision(
        decision="accept",
        confidence=0.92,
        rationale="Tier-1 SEC source, no conflicting extractions.",
    )
    assert d.decision == "accept"


def test_synthesizer_decision_decision_outside_enum_rejected():
    with pytest.raises(ValidationError):
        SynthesizerDecision(
            decision="approve_with_pizza",  # type: ignore[arg-type]
            confidence=0.5,
            rationale="r",
        )


def test_synthesizer_decision_confidence_above_one_rejected():
    with pytest.raises(ValidationError):
        SynthesizerDecision(
            decision="accept",
            confidence=1.01,
            rationale="r",
        )


def test_synthesizer_decision_empty_rationale_rejected():
    with pytest.raises(ValidationError):
        SynthesizerDecision(
            decision="reject",
            confidence=0.0,
            rationale="",
        )
