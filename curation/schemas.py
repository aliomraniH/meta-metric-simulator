"""Pydantic v2 schemas for the Layer 5 curation refresh server.

Three core models cover the full critique-revise lifecycle of a
sources_registry.yaml refresh:

  DiffProposal       — what the curation-refresher subagent emits.
  ConstitutionFlag   — what sanitize_constitution / curation-critic emit.
  CurationDecision   — what synthesize_diff (M13b) emits before the
                       sources_registry.yaml write.

Schemas + closed enums per docs/AGENTIC_ARCHITECTURE_INDEX.md §2.3
(Option D: constitutional critique-revise).  The constitution itself
lives in curation/prompts/refresher.md and curation/prompts/critic.md.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Closed enums.  Each value here is one of the constitutional rules
# enumerated in curation/prompts/refresher.md.  Adding a rule means
# updating BOTH this enum and the prompt — they are paired by design.
ALLOWED_CONSTITUTION_RULES: frozenset[str] = frozenset({
    "primary_source_required",
    "uncited_numeric",
    "secondary_aggregator_used",
    "competitor_missing_asymmetry",
    "auto_apply_attempted",
    "forbidden_domain",
})

ALLOWED_SEVERITIES: frozenset[str] = frozenset({
    "info", "low", "medium", "high", "critical",
})

ALLOWED_DECISIONS: frozenset[str] = frozenset({
    "accept", "reject", "escalate", "defer",
})

ALLOWED_DIFF_FIELDS: frozenset[str] = frozenset({
    "url", "accessed_date", "trust_tier", "aliases",
    "confidence", "type", "verify_during_m12",
})


class DiffProposal(BaseModel):
    """One field-level diff for a sources_registry.yaml entry.

    The curation-refresher emits exactly one DiffProposal per registry
    entry per refresh cycle.  Multi-field changes are split into
    multiple proposals so each can be sanitized + synthesized
    independently."""

    model_config = ConfigDict(extra="forbid")

    registry_entry_id: str = Field(min_length=1, description="Source key in sources_registry.yaml")
    field: str = Field(min_length=1, description="Which field is being diffed")
    current_value: Any = Field(default=None)
    proposed_value: Any
    rationale: str = Field(default="", description="Plain-English reasoning")
    cited_evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of {url, page, quoted_text} entries from web_fetch citations",
    )
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v

    @field_validator("field")
    @classmethod
    def _check_field(cls, v: str) -> str:
        if v not in ALLOWED_DIFF_FIELDS:
            raise ValueError(
                f"field={v!r} not in allowed set {sorted(ALLOWED_DIFF_FIELDS)}"
            )
        return v


class ConstitutionFlag(BaseModel):
    """One constitutional violation raised by sanitize_constitution
    (deterministic) or the curation-critic subagent (LLM)."""

    model_config = ConfigDict(extra="forbid")

    rule: Literal[
        "primary_source_required",
        "uncited_numeric",
        "secondary_aggregator_used",
        "competitor_missing_asymmetry",
        "auto_apply_attempted",
        "forbidden_domain",
    ]
    severity: Literal["info", "low", "medium", "high", "critical"]
    evidence: list[str] = Field(default_factory=list)
    suggested_fix: Optional[str] = None


class CurationDecision(BaseModel):
    """The synthesizer's verdict on a DiffProposal (M13b emits these).

    decision='accept' + human approved via ctx.elicit → synthesize_diff writes.
    decision='reject' → flags persisted; no write.
    decision='escalate' → critique-revise loop exhausted; surface to operator.
    decision='defer' → no primary source available; revisit next cycle.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject", "escalate", "defer"]
    confidence: float
    rationale: str = Field(min_length=1)
    applied_diff: Optional[DiffProposal] = None
    rejection_flags: list[ConstitutionFlag] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v
