"""Pydantic v2 schemas for the Layer 8 agentic narrator.

Three core models cover the narration lifecycle:

  Hypothesis    — one plain-English explanation tied to a specific
                  Anomaly via `anomaly_ref`.
  Narrative     — container for hypotheses across one (scenario_id,
                  metric_name) pair.
  NarratorFlag  — hook-only soft-gate flag.  Per
                  AGENTIC_ARCHITECTURE_INDEX.md §2.3 (Option A), flags
                  annotate the insight row with `disputed=true` rather
                  than blocking the write.

Closed enums + Pydantic v2 validators per
docs/AGENTIC_ARCHITECTURE_INDEX.md §2.4.

The non-negotiable rule (PDF §3.6): the narrator MUST NOT invent
numeric values.  The synthesize_insight code-level check is what
enforces this mechanically — see insights/tools/synthesize_insight.py.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_FLAG_KINDS: frozenset[str] = frozenset({
    "invented_number",
    "missing_anomaly_ref",
    "weak_hypothesis",
    "scope_creep",
})

ALLOWED_SEVERITIES: frozenset[str] = frozenset({
    "info", "low", "medium", "high",
})


class Hypothesis(BaseModel):
    """One plain-English hypothesis explaining a specific Anomaly."""

    model_config = ConfigDict(extra="forbid")

    anomaly_ref: str = Field(
        min_length=1,
        description="References the Anomaly's metric_name+tick_day key, e.g. 'ad_revenue_per_dau:14'",
    )
    hypothesis_text: str = Field(
        default="",
        description="The narration prose; 1-3 sentences, hypothesis-forward",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    cited_supporting_scenarios: list[str] = Field(
        default_factory=list,
        description="scenario_ids returned by find_similar_scenarios",
    )

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v


class Narrative(BaseModel):
    """Container for narrator output across multiple anomalies in a single
    (scenario_id, metric_name) context."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("overall_confidence")
    @classmethod
    def _check_overall_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"overall_confidence={v} outside [0, 1]")
        return v


class NarratorFlag(BaseModel):
    """Soft-gate flag emitted by the synthesize_insight code-level checks.

    Per AGENTIC_ARCHITECTURE_INDEX.md §2.2 (soft mode hook-only):
    flags ANNOTATE the insight row with `disputed=true` rather than
    blocking the write.  The `kind="invented_number"` flag is the
    structural enforcement of "narrator must not invent numbers"
    (PDF §3.6) — without it, the rule is just a prompt instruction.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "invented_number",
        "missing_anomaly_ref",
        "weak_hypothesis",
        "scope_creep",
    ]
    severity: Literal["info", "low", "medium", "high"]
    evidence: list[str] = Field(default_factory=list)
