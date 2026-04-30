"""sanitize_schema — deterministic structural validation of an ExtractedMetric.

First of three sanitize_* tools on the Layer 4 baselines server (the
others come in M12b: sanitize_policy, sanitize_source_tier).  Per
`docs/AGENTIC_ARCHITECTURE_INDEX.md` §2.1 / PDF §10.2:

  - readOnlyHint=True — the layer-isolation hook treats this as a
    read-only tool that is allowed to bypass the synthesizer-as-sole-
    writer rule.
  - Deterministic only — no LLM calls, no file or DB I/O at this stage.
  - Returns zero or more SanitizerFlag records.  Empty = clean.

The flags emitted here are aggregated by the synthesizer (Opus 4.7),
which weighs them against the proposal and decides accept / reject /
escalate.
"""
from __future__ import annotations

from pydantic import ValidationError

from baselines.schemas import ExtractedMetric, SanitizerFlag
from baselines.server import baselines_server


# Numeric bound rules per unit.  Adding a unit means updating this dict
# AND the ALLOWED_UNITS enum in baselines/schemas.py.
_UNIT_BOUNDS: dict[str, tuple[float | None, float | None, str]] = {
    # unit             (min,    max,    severity_on_violation)
    "pct":             (0.0,   100.0,  "high"),
    "ratio":           (0.0,    10.0,  "high"),
    "usd_billion":     (0.0,    None,  "critical"),  # negatives are critical
    "usd":             (0.0,    None,  "high"),
    "count":           (0.0,    None,  "high"),
    "minutes":         (0.0,  10000.0, "high"),
    "days":            (0.0,    None,  "high"),
    "users_million":   (0.0,    None,  "high"),
    "users_billion":   (0.0,    None,  "high"),
}

_MIN_QUOTED_TEXT_LEN = 5


@baselines_server.tool(annotations={"readOnlyHint": True})
async def sanitize_schema(extracted: ExtractedMetric) -> list[SanitizerFlag]:
    """Validate ExtractedMetric structurally.  Deterministic; no LLM."""
    flags: list[SanitizerFlag] = []

    # ---- 1. Schema re-validation (belt + braces) ---------------------------
    # Pydantic already validated on construction, but a re-validation guards
    # against in-place mutation between extractor emit and sanitize.
    try:
        ExtractedMetric.model_validate(extracted.model_dump())
    except ValidationError as exc:
        flags.append(SanitizerFlag(
            kind="schema",
            severity="critical",
            evidence=[f"Pydantic re-validation failed: {exc!s}"],
            suggested_fix="Re-emit ExtractedMetric conforming to canonical schema",
        ))
        # Continue running the rest of the checks so the synthesizer sees
        # every flag, not just the first one.

    # ---- 2. Unit bound check ------------------------------------------------
    bounds = _UNIT_BOUNDS.get(extracted.unit)
    if bounds is not None:
        lo, hi, severity = bounds
        if lo is not None and extracted.value < lo:
            flags.append(SanitizerFlag(
                kind="bound",
                severity=severity,  # type: ignore[arg-type]
                evidence=[
                    f"value={extracted.value} below floor {lo} for unit={extracted.unit!r}"
                ],
                suggested_fix=f"Re-extract; values for unit={extracted.unit!r} should be >= {lo}",
            ))
        if hi is not None and extracted.value > hi:
            flags.append(SanitizerFlag(
                kind="bound",
                severity=severity,  # type: ignore[arg-type]
                evidence=[
                    f"value={extracted.value} above ceiling {hi} for unit={extracted.unit!r}"
                ],
                suggested_fix=f"Re-extract; values for unit={extracted.unit!r} should be <= {hi}",
            ))

    # ---- 3. Provenance presence --------------------------------------------
    has_url = bool(extracted.source.url and extracted.source.url.strip())
    has_doc_title = bool(extracted.source.doc_title and extracted.source.doc_title.strip())
    if not has_url and not has_doc_title:
        flags.append(SanitizerFlag(
            kind="provenance",
            severity="critical",
            evidence=["source has neither url nor doc_title"],
            suggested_fix="Re-extract with at least one of source.url or source.doc_title populated",
        ))

    # ---- 4. Quoted-text traceability ---------------------------------------
    qt = extracted.source.quoted_text or ""
    if len(qt.strip()) < _MIN_QUOTED_TEXT_LEN:
        flags.append(SanitizerFlag(
            kind="provenance",
            severity="medium",
            evidence=[f"source.quoted_text length {len(qt)} below minimum {_MIN_QUOTED_TEXT_LEN}"],
            suggested_fix="Include source quoted_text for citation traceability",
        ))

    return flags
