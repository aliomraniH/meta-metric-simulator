"""Per-layer deliberation contracts.

A DeliberationContract pins three things for an agentic layer:

  - mode: "strict" or "soft"
  - confidence_floor: synthesizer confidence required to commit
  - severity_block_threshold: sanitizer severity at which writes are denied

Strict mode (Layers 4, 5)
  PreToolUse hook denies writes below the confidence floor or at/above the
  severity block threshold.  Synthesizer must escalate to ctx.elicit() for
  human diff-confirm before commit.

Soft mode (Layers 6, 8)
  Writes are allowed; PostToolUse hook annotates them with
  {disputed, confidence, disputed_by} so consumers can see uncertainty.
  Layer 6 also runs an evaluator-optimizer loop bounded at N=2.

Severity ordering (lowest → highest):
  info < low < medium < high < critical
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Mapping

Mode = Literal["strict", "soft"]
Severity = Literal["info", "low", "medium", "high", "critical"]

SEVERITY_ORDER: tuple[Severity, ...] = ("info", "low", "medium", "high", "critical")
SEVERITY_RANK: dict[Severity, int] = {s: i for i, s in enumerate(SEVERITY_ORDER)}


def severity_at_or_above(observed: str, threshold: str) -> bool:
    """True if `observed` severity is at or above `threshold`."""
    if observed not in SEVERITY_RANK or threshold not in SEVERITY_RANK:
        return False
    return SEVERITY_RANK[observed] >= SEVERITY_RANK[threshold]  # type: ignore[index]


# Type alias for the human-escalation handler used in strict mode.
EscalationHandler = Callable[[dict], Awaitable[dict]]


@dataclass(frozen=True)
class DeliberationContract:
    """Layer-scoped policy for sanitize/synthesize gating."""

    layer: str  # "l4", "l5", "l6", "l8"
    mode: Mode
    confidence_floor: float
    severity_block_threshold: Severity = "high"
    # Soft-mode: how many evaluator-optimizer iterations are allowed
    # before the synthesizer commits with disputed=true.  Ignored in strict.
    max_optimizer_iterations: int = 2
    notes: str = ""

    def blocks_for_confidence(self, confidence: float) -> bool:
        return self.mode == "strict" and confidence < self.confidence_floor

    def blocks_for_severity(self, observed_severity: str) -> bool:
        # The threshold itself is the gate, regardless of mode.  Soft-mode
        # contracts set this to "critical" so only the highest-severity
        # findings stop a write; strict-mode contracts set it to "high".
        return severity_at_or_above(observed_severity, self.severity_block_threshold)


# -----------------------------------------------------------------------------
# Registry — module-level, read-only
# -----------------------------------------------------------------------------

CONTRACTS: Mapping[str, DeliberationContract] = {
    "l4": DeliberationContract(
        layer="l4",
        mode="strict",
        confidence_floor=0.85,
        severity_block_threshold="high",
        notes="Baselines: external-fact ingestion. Synthesizer escalates to ctx.elicit().",
    ),
    "l5": DeliberationContract(
        layer="l5",
        mode="strict",
        confidence_floor=0.85,
        severity_block_threshold="high",
        notes="Curation: constitutional critique-revise. Never auto-applies.",
    ),
    "l6": DeliberationContract(
        layer="l6",
        mode="soft",
        confidence_floor=0.70,
        severity_block_threshold="critical",  # soft mode: only critical blocks
        max_optimizer_iterations=2,
        notes="Scenario compiler: evaluator-optimizer N=2; on exhaustion, disputed=true.",
    ),
    "l8": DeliberationContract(
        layer="l8",
        mode="soft",
        confidence_floor=0.60,
        severity_block_threshold="critical",
        notes="Insight narrator: hook-only gate; never invents numeric values.",
    ),
}


def get(layer: str) -> DeliberationContract:
    if layer not in CONTRACTS:
        raise KeyError(f"no deliberation contract for layer {layer!r}")
    return CONTRACTS[layer]


def is_agentic(layer: str) -> bool:
    return layer in CONTRACTS
