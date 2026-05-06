"""synthesize_insight — sole writer to the insights table (Layer 8 soft mode).

Per AGENTIC_ARCHITECTURE_INDEX.md §2.4 (synthesize_insight → M16 →
insights table → Soft (A) → 0.60 confidence floor).

Architectural enforcement of the "narrator MUST NOT invent numbers"
rule (PDF §3.6).  The deterministic invented-number check below is
what makes the rule mechanical instead of advisory: hypotheses that
mention numbers absent from the input get flagged
``kind="invented_number", severity="high"`` and the row is annotated
``disputed=true``.  Without this check, the prompt instruction is
just a request.

Soft mode hook-only gate (Option A):
  * Flags ANNOTATE the insight row, never block the write.
  * No ctx.elicit — the disputed bit is the user-visible signal.
  * No calibration-impact elicit — insights are outputs, not
    calibration inputs.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import ulid

from calibration.lock import requires_calibration_unlocked
from insights.schemas import Hypothesis, Narrative, NarratorFlag
from insights.server import insights_server

log = logging.getLogger(__name__)


_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# Acceptable contextual numbers — small qualifiers and recent years.
# A hypothesis that says "tripled" or "in 2025" or "the top 3" is fine
# even though those aren't in the Anomaly's serialized fields.
_ACCEPTABLE_CONTEXTUAL: frozenset[str] = frozenset({
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "2024", "2025", "2026",
})

_DISPUTED_SEVERITIES: frozenset[str] = frozenset({"high"})


@insights_server.tool()
@requires_calibration_unlocked
async def synthesize_insight(
    narrative: Narrative,
    anomalies_input: list[dict[str, Any]] | None = None,
    ctx: Any = None,
) -> dict[str, Any]:
    """Sole writer to the `insights` table.

    Args:
        narrative:          the agentic narrator's output.
        anomalies_input:    the Anomaly objects that were passed to the
                            narrator (as dicts, since FastMCP serialises
                            tool args).  Used by the invented-number
                            check to determine which numbers are "in
                            input" and therefore allowed in narration.
                            When omitted, every numeric token in the
                            narrative is treated as potentially
                            invented (conservative).
        ctx:                FastMCP / MCP-sampling Context.  Unused by
                            this tool (no model call), but kept on the
                            signature for symmetry with other writers
                            and to surface in the Context-injection
                            channel for hook telemetry.

    Returns:
        Dict with keys:
          * `insight_id`        ULID
          * `disputed`          True iff any flag has severity=='high'
          * `confidence`        narrative.overall_confidence
          * `narrator_flags`    list of NarratorFlag dicts
    """
    flags = _check_narrative(narrative, anomalies_input or [])
    disputed = any(f.severity in _DISPUTED_SEVERITIES for f in flags)
    disputed_by = sorted({f.kind for f in flags if f.severity in _DISPUTED_SEVERITIES})

    insight_id = str(ulid.new())
    row = {
        "insight_id":          insight_id,
        "scenario_id":         narrative.scenario_id,
        "metric_name":         narrative.metric_name,
        "hypotheses_json":     narrative.model_dump(mode="json"),
        "confidence":          float(narrative.overall_confidence),
        "disputed":            1 if disputed else 0,
        "disputed_by":         disputed_by if disputed_by else None,
        "narrator_flags_json": [f.model_dump(mode="json") for f in flags] if flags else None,
        "created_at":          time.time(),
    }
    await _persist_row(row)

    log.info(
        "synthesize_insight wrote %s (disputed=%s, flags=%d)",
        insight_id, disputed, len(flags),
    )

    return {
        "insight_id":     insight_id,
        "disputed":       disputed,
        "confidence":     float(narrative.overall_confidence),
        "narrator_flags": [f.model_dump(mode="json") for f in flags],
    }


# ---------------------------------------------------------------------------
# Deterministic narrator-flag checks
# ---------------------------------------------------------------------------

def _check_narrative(
    narrative: Narrative,
    anomalies_input: list[dict[str, Any]],
) -> list[NarratorFlag]:
    """Run the three deterministic narrator-flag checks.

    Returns zero or more NarratorFlag.  Soft-mode contract: the
    presence of flags annotates the row, never blocks the write.
    """
    flags: list[NarratorFlag] = []

    # Build the set of allowed numeric strings from the anomalies'
    # serialised fields.  Tokenize each Anomaly value through the
    # same _NUMBER_PATTERN as the narrative so e.g. "3.4567" matches
    # "3.46" only when the narrator quotes the same precision.
    allowed_numbers = _allowed_numbers_from_anomalies(anomalies_input)

    # Pre-compute the set of valid anomaly_refs for the missing-ref check.
    valid_refs = {f"{a.get('metric_name')}:{a.get('tick_day')}" for a in anomalies_input}

    for h in narrative.hypotheses:
        flags.extend(_check_hypothesis_invented_numbers(h, allowed_numbers))
        flags.extend(_check_hypothesis_anomaly_ref(h, valid_refs))
        flags.extend(_check_hypothesis_weak(h))

    return flags


def _allowed_numbers_from_anomalies(
    anomalies_input: list[dict[str, Any]],
) -> set[str]:
    """Extract every numeric token (per _NUMBER_PATTERN) from the Anomaly
    inputs' values, plus the small contextual exemptions.
    """
    out: set[str] = set(_ACCEPTABLE_CONTEXTUAL)
    for a in anomalies_input:
        for key in ("observed_value", "expected_value", "z_score",
                    "tick_day"):
            val = a.get(key)
            if val is None:
                continue
            for tok in _NUMBER_PATTERN.findall(_format_number(val)):
                out.add(tok)
    return out


def _format_number(v: Any) -> str:
    """Format a numeric Anomaly field for tokenization.  Reproduces the
    common formattings the narrator might emit so the token-equality
    check matches on the natural surface forms (e.g. ``42.7`` and
    ``42.70`` both match against the underlying ``42.7``)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    # Two and four-decimal forms cover the narrator's likely outputs.
    return f"{f} {f:.2f} {f:.4f} {int(f)}"


def _check_hypothesis_invented_numbers(
    h: Hypothesis,
    allowed_numbers: set[str],
) -> list[NarratorFlag]:
    """Flag a Hypothesis when its hypothesis_text contains numeric
    tokens that aren't in the Anomaly inputs.

    Matching rules:
      * Exact equality always allowed (contextual exemptions + anomaly
        fields).
      * Substring/prefix equivalence allowed only when BOTH tokens are
        ≥ 3 characters — this absorbs rounding (`42.7` vs `42.700`)
        without granting blanket immunity to single digits like `1`
        that would otherwise match `156` as a substring.
    """
    text = h.hypothesis_text or ""
    tokens = _NUMBER_PATTERN.findall(text)
    invented: list[str] = []
    for tok in tokens:
        if tok in allowed_numbers:
            continue
        if len(tok) >= 3 and any(
            len(a) >= 3 and (tok in a or a in tok)
            for a in allowed_numbers if a
        ):
            continue
        invented.append(tok)
    if not invented:
        return []
    return [NarratorFlag(
        kind="invented_number",
        severity="high",
        evidence=[
            f"hypothesis for {h.anomaly_ref!r} contains numeric token(s) "
            f"not in input: {invented!r}"
        ],
    )]


def _check_hypothesis_anomaly_ref(
    h: Hypothesis,
    valid_refs: set[str],
) -> list[NarratorFlag]:
    """Flag a Hypothesis whose anomaly_ref doesn't match any input."""
    if not valid_refs:
        # No anomaly inputs available — can't enforce.  Don't flag.
        return []
    if h.anomaly_ref in valid_refs:
        return []
    return [NarratorFlag(
        kind="missing_anomaly_ref",
        severity="high",
        evidence=[
            f"hypothesis.anomaly_ref={h.anomaly_ref!r} not in input "
            f"refs {sorted(valid_refs)!r}"
        ],
    )]


def _check_hypothesis_weak(h: Hypothesis) -> list[NarratorFlag]:
    """Flag a Hypothesis whose text is shorter than 20 characters."""
    text = (h.hypothesis_text or "").strip()
    if len(text) >= 20:
        return []
    return [NarratorFlag(
        kind="weak_hypothesis",
        severity="medium",
        evidence=[
            f"hypothesis_text length {len(text)} < 20 chars; "
            "ambiguous narration is hard to verify against the underlying numbers"
        ],
    )]


# ---------------------------------------------------------------------------
# Persistence helper — module-level so tests can monkey-patch
# ---------------------------------------------------------------------------

async def _persist_row(row: dict[str, Any]) -> None:
    """Insert one row into insights.  Best-effort — when the DB isn't
    configured (e.g. unit tests) the call is logged and skipped."""
    try:
        from infra.db import insights as insights_table
        from infra.db import session as _session
    except Exception as exc:  # noqa: BLE001
        log.debug("insights persistence skipped (no infra.db): %s", exc)
        return
    try:
        async with _session() as s:
            await s.execute(insights_table.insert(), [row])
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("insights persistence failed: %s", exc)
