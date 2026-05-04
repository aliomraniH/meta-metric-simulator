"""Pydantic v2 schemas for the Layer 6 scenario synthesis sublayer.

Named with the `_agentic` suffix to keep them distinct from the M7
deterministic `Scenario` dataclass in `engine/scenario.py` — these are
the agentic-side proposals before the engine consumes them.

Per AGENTIC_ARCHITECTURE_INDEX.md §1, Layer 6 is "Mixed": the engine
itself is deterministic (M7, locked); only the synthesis sublayer is
agentic with soft mode + Option C (evaluator-optimizer with N=2).

The compiler emits a `CompiledScenario`; the evaluator emits an
`EvaluatorVerdict`; the synthesizer (M15c) is the sole writer to the
canonical scenarios table.

Closed enums + Pydantic v2 validators per `docs/AGENTIC_ARCHITECTURE_INDEX.md`
§2.4.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_PERTURBATION_OPS: frozenset[str] = frozenset({
    "set", "multiply", "add", "toggle", "ramp", "spike",
})

ALLOWED_AUDIENCE_DIMS: frozenset[str] = frozenset({
    "viewer_segment", "viewer_geo", "viewer_tenure_days", "creator_tier",
})

ALLOWED_AUDIENCE_OPS: frozenset[str] = frozenset({"eq", "in", "gt", "lt"})

ALLOWED_TIME_HORIZONS: frozenset[str] = frozenset({"1d", "7d", "28d", "90d"})


class Perturbation(BaseModel):
    """A single perturbation in a CompiledScenario manifest.

    The engine consumes this through `engine/perturbations.py`; the
    underlying `algorithms/perturbations.apply` supports `ramp`, `step`,
    `spike` natively.  `set`, `multiply`, `add`, `toggle` map onto
    `step`-shaped applications at the engine boundary."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, description="Dotted path into engine state, e.g. 'ad_load_pct'")
    op: Literal["set", "multiply", "add", "toggle", "ramp", "spike"]
    value: float
    rationale: str = Field(min_length=1, description="One-sentence justification grounded in evidence")


class AudienceFilter(BaseModel):
    """Restrict a scenario to a viewer / creator subset."""

    model_config = ConfigDict(extra="forbid")

    dim: Literal["viewer_segment", "viewer_geo", "viewer_tenure_days", "creator_tier"]
    op: Literal["eq", "in", "gt", "lt"]
    value: Union[str, int, list[Union[str, int]]]


class CompiledScenario(BaseModel):
    """The agentic compiler's output.  The synthesizer (M15c) writes
    these to the scenarios table; the engine consumes them deterministically."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    natural_language_intent: str = Field(
        min_length=1,
        description="The user's original prompt, preserved verbatim for provenance",
    )
    perturbations: list[Perturbation] = Field(default_factory=list)
    time_horizon: Literal["1d", "7d", "28d", "90d"]
    audience_filters: list[AudienceFilter] = Field(default_factory=list)
    seed: int = Field(default=42)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence={v} outside [0, 1]")
        return v


class EvaluatorVerdict(BaseModel):
    """The scenario-evaluator's critique of a CompiledScenario.

    Four-axis rubric per AGENTIC_ARCHITECTURE_INDEX.md §2.3 (Option C):
      - schema_validity:           manifest fits the engine's expected shape.
      - perturbation_realism:      magnitudes / targets are plausible.
      - simulator_constrainability: the engine can actually run this.
      - audience_filter_coherence: filters mutually compatible.

    Each rubric axis is a [0, 1] sub-score; the overall `score` is
    typically a weighted mean (the evaluator decides the weights).
    `passes` checks against `accept_threshold` (default 0.7).
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    rubric_breakdown: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-axis sub-scores — keys: schema_validity, perturbation_realism, "
            "simulator_constrainability, audience_filter_coherence."
        ),
    )
    gaps: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    accept_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("score")
    @classmethod
    def _check_score(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"score={v} outside [0, 1]")
        return v

    @field_validator("accept_threshold")
    @classmethod
    def _check_threshold(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"accept_threshold={v} outside [0, 1]")
        return v

    @property
    def passes(self) -> bool:
        return self.score >= self.accept_threshold
