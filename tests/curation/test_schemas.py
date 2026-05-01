"""Tests for curation/schemas.py — DiffProposal / ConstitutionFlag / CurationDecision."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from curation.schemas import (
    ConstitutionFlag,
    CurationDecision,
    DiffProposal,
)


# ---------------------------------------------------------------------------
# DiffProposal
# ---------------------------------------------------------------------------

def test_diff_proposal_valid():
    """A well-formed DiffProposal should construct without error."""
    d = DiffProposal(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        current_value="https://old.example.com/x",
        proposed_value="https://investor.atmeta.com/q4-2025/",
        rationale="The current URL 404s; the new IR page hosts the same release.",
        cited_evidence=[{
            "url": "https://investor.atmeta.com/q4-2025/",
            "page": 1,
            "quoted_text": "Q4 2025 results",
        }],
        confidence=0.9,
    )
    assert d.registry_entry_id == "meta_q4_2025_press_release"
    assert d.field == "url"
    assert d.confidence == pytest.approx(0.9)


def test_diff_proposal_confidence_bounds():
    """Confidence > 1.0 must be rejected."""
    with pytest.raises(ValidationError, match="confidence"):
        DiffProposal(
            registry_entry_id="x",
            field="url",
            proposed_value="https://x",
            rationale="ok",
            cited_evidence=[],
            confidence=1.5,
        )
    with pytest.raises(ValidationError, match="confidence"):
        DiffProposal(
            registry_entry_id="x",
            field="url",
            proposed_value="https://x",
            rationale="ok",
            cited_evidence=[],
            confidence=-0.1,
        )


def test_diff_proposal_field_enum():
    """field outside the closed allowed-set must be rejected."""
    with pytest.raises(ValidationError):
        DiffProposal(
            registry_entry_id="x",
            field="not_a_real_field",
            proposed_value="anything",
            rationale="ok",
            cited_evidence=[],
            confidence=0.5,
        )


# ---------------------------------------------------------------------------
# ConstitutionFlag
# ---------------------------------------------------------------------------

def test_constitution_flag_valid():
    f = ConstitutionFlag(
        rule="uncited_numeric",
        severity="critical",
        evidence=["proposal had no citations"],
        suggested_fix="cite the SEC filing",
    )
    assert f.rule == "uncited_numeric"
    assert f.severity == "critical"


def test_constitution_flag_rule_enum():
    """rule outside the closed enum must be rejected."""
    with pytest.raises(ValidationError):
        ConstitutionFlag(
            rule="not_a_real_rule",
            severity="high",
        )


def test_constitution_flag_severity_enum():
    """severity outside the closed enum must be rejected."""
    with pytest.raises(ValidationError):
        ConstitutionFlag(
            rule="forbidden_domain",
            severity="catastrophic",  # not in {info,low,medium,high,critical}
        )


# ---------------------------------------------------------------------------
# CurationDecision
# ---------------------------------------------------------------------------

def test_curation_decision_valid():
    d = CurationDecision(
        decision="accept",
        confidence=0.92,
        rationale="proposal cites primary source and matches numbers",
    )
    assert d.decision == "accept"
    assert d.applied_diff is None
    assert d.rejection_flags == []


def test_curation_decision_decision_enum():
    """decision must be in {accept, reject, escalate, defer}."""
    with pytest.raises(ValidationError):
        CurationDecision(
            decision="not_a_decision",
            confidence=0.5,
            rationale="ok ok ok",
        )


def test_curation_decision_confidence_bounds():
    with pytest.raises(ValidationError, match="confidence"):
        CurationDecision(
            decision="accept",
            confidence=1.5,
            rationale="ok ok ok",
        )


def test_curation_decision_with_applied_diff():
    diff = DiffProposal(
        registry_entry_id="x",
        field="url",
        proposed_value="https://x",
        rationale="rationale longer than 20 characters here",
        cited_evidence=[],
        confidence=0.9,
    )
    d = CurationDecision(
        decision="accept",
        confidence=0.9,
        rationale="approved by human via ctx.elicit",
        applied_diff=diff,
    )
    assert d.applied_diff is diff


def test_curation_decision_extra_forbid():
    """extra='forbid' on the model — unknown keys must be rejected."""
    with pytest.raises(ValidationError):
        CurationDecision(
            decision="accept",
            confidence=0.9,
            rationale="ok",
            mystery_field="bad",
        )
