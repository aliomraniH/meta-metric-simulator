"""Pydantic v2 schemas for the Layer 4 baselines agentic ingestion server.

Three core models cover the full sanitize/synthesize lifecycle of a
baseline write:

  ExtractedMetric        — what the baseline-extractor subagent emits.
  SanitizerFlag          — what each sanitize_* tool emits (zero or more).
  SynthesizerDecision    — what the synthesizer subagent emits before
                           the synthesize_baseline tool decides whether
                           to commit to disk.

Schemas + closed enums per docs/AGENTIC_ARCHITECTURE_INDEX.md §2.4.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Closed enums.  The unit set is intentionally small — it's the subset of
# units that show up across params/ + baselines/data/ today.  Adding a
# unit means updating this enum AND adding the corresponding bound check
# in baselines/tools/sanitize_schema.py.
ALLOWED_UNITS: frozenset[str] = frozenset({
    "usd",
    "usd_billion",
    "pct",
    "ratio",
    "count",
    "minutes",
    "days",
    "users_million",
    "users_billion",
})

ALLOWED_SEVERITIES: frozenset[str] = frozenset({
    "info", "low", "medium", "high", "critical",
})

ALLOWED_SANITIZER_KINDS: frozenset[str] = frozenset({
    "schema", "policy", "source_tier", "bound", "provenance",
})

ALLOWED_DECISIONS: frozenset[str] = frozenset({"accept", "reject", "escalate"})


class ExtractedMetricSource(BaseModel):
    """Provenance block on every ExtractedMetric.  All four fields exist;
    the sanitize_schema tool flags missing critical fields."""

    model_config = ConfigDict(extra="forbid")

    doc_title: str = Field(default="", description="Human-readable source title")
    page: Optional[int] = Field(default=None, ge=0, description="1-based page number; 0/None if not page-anchored")
    quoted_text: str = Field(default="", description="Verbatim quoted span supporting the value")
    url: str = Field(default="", description="Canonical URL for the source artefact")


class ExtractedMetric(BaseModel):
    """One metric value produced by the baseline-extractor subagent.

    The agentic flow:
      extractor LLM → ExtractedMetric → sanitize_* tools → SanitizerFlag[]
        → synthesizer LLM → SynthesizerDecision → synthesize_baseline writes yaml.
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1, description="Canonical metric name (e.g. reels_run_rate_usd_billion)")
    value: float
    unit: str
    period: str = Field(min_length=1, description="e.g. FY 2025, Q4 2025, 2026-04-28")
    source: ExtractedMetricSource
    confidence: float

    @field_validator("unit")
    @classmethod
    def _check_unit(cls, v: str) -> str:
        if v not in ALLOWED_UNITS:
            raise ValueError(
                f"unit={v!r} not in allowed set {sorted(ALLOWED_UNITS)}"
            )
        return v

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v


class SanitizerFlag(BaseModel):
    """One concern raised by a sanitize_* tool.

    Sanitizers are deterministic (or Haiku-augmented) and run in parallel.
    Each emits zero or more flags; the synthesizer aggregates them."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["schema", "policy", "source_tier", "bound", "provenance"]
    severity: Literal["info", "low", "medium", "high", "critical"]
    evidence: list[str] = Field(default_factory=list, description="Short strings explaining the flag")
    span: Optional[tuple[int, int]] = Field(default=None, description="Character span in source if applicable")
    suggested_fix: Optional[str] = None


class SynthesizerDecision(BaseModel):
    """The Opus-4.7 synthesizer's verdict.

    decision='accept' + confidence ≥ 0.85 → synthesize_baseline writes.
    decision='escalate' → ctx.elicit() for human approval.
    decision='reject'  → persist to syntheses table; no write.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject", "escalate"]
    confidence: float
    rationale: str = Field(min_length=1, description="Synthesizer's reasoning summary")
    accepted_fixes: list[str] = Field(default_factory=list, description="Sanitizer suggestions the synthesizer accepted")
    unresolved_disputes: list[str] = Field(default_factory=list, description="Flags judged ambiguous; not blocking")

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v
