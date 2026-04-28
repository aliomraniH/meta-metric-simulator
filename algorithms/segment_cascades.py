"""Segment cascades — pure function, agent-free.

Per docs/ALGORITHMS.md §segment_cascades, this module redistributes
population share across segments via a substitution matrix.  Total
population is conserved (sum of `population_share_pct` is invariant
within float tolerance) — the cascade moves mass, it does not create
or destroy.

Mathematically a Markov-style propagation: for each segment src, the
matrix row `M[src]` says what fraction of src's share lands in each
destination dst (including back to src).  Each row sums to 1, which is
what gives us conservation.

Output is a new SegmentStateMap; emits no events.

House rules:
  * Pure function.  No rng (cascades are linear; noise belongs in
    upstream propensities, not here).
  * Reads only the params dict.
  * Imports nothing from baselines/, engine/, or other algorithms.
"""
from __future__ import annotations

from typing import Any, Mapping


_FALLBACK_MASS_TOLERANCE = 1e-6


def propagate(
    segment_state_map: Mapping[str, Mapping[str, Any]],
    params: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Apply one tick of cross-segment population redistribution.

    Args:
        segment_state_map: keyed by segment name (`teen`, `young_adult`,
            `snacker`, `lean_back`, …).  Each value carries at minimum
            `population_share_pct` (the conserved quantity) and may
            carry `watch_time_per_session`, `integrity_exposure`,
            `ad_load`, etc.  Non-conserved fields pass through unchanged.
        params: segment_propensities.yaml sub-dict.  Recognised keys:
            `substitution_matrix` (mapping src → {dst: fraction}) and
            `mass_tolerance` (default 1e-6) for the conservation check.

    Returns:
        New SegmentStateMap with redistributed `population_share_pct`.
        The input is not mutated.
    """
    matrix = params.get("substitution_matrix") or {}
    tolerance = float(params.get("mass_tolerance", _FALLBACK_MASS_TOLERANCE))

    # Snapshot current shares so the propagation is single-pass and
    # doesn't see partially-updated values.
    current_shares: dict[str, float] = {
        seg: float(state.get("population_share_pct", 0.0))
        for seg, state in segment_state_map.items()
    }

    # Compute new shares: new[dst] = sum over src of M[src][dst] * old[src].
    # Segments not present as a row in the matrix default to identity
    # (M[src][src] = 1) — they keep their full share.
    new_shares: dict[str, float] = {seg: 0.0 for seg in current_shares}
    for src, src_share in current_shares.items():
        row = matrix.get(src)
        if not row:
            new_shares[src] = new_shares.get(src, 0.0) + src_share
            continue
        for dst, fraction in row.items():
            new_shares[dst] = new_shares.get(dst, 0.0) + src_share * float(fraction)

    # Conservation check.  If a substitution row didn't sum to 1.0, mass
    # has leaked; rescale to the original total so downstream invariants
    # (population_share_pct sum) hold.  This is a safety net — params
    # files should be authored with rows summing to 1.
    original_total = sum(current_shares.values())
    new_total = sum(new_shares.values())
    if new_total > 0 and abs(new_total - original_total) > tolerance:
        scale = original_total / new_total
        new_shares = {seg: v * scale for seg, v in new_shares.items()}

    # Build the result, preserving non-conserved fields.
    out: dict[str, dict[str, Any]] = {}
    for seg, state in segment_state_map.items():
        out[seg] = {**state, "population_share_pct": new_shares.get(seg, 0.0)}
    # Segments introduced by the matrix that didn't exist in the input
    # (rare but legal — e.g. a new segment appearing because a guardrail
    # spawned it) get a fresh entry.
    for seg, share in new_shares.items():
        if seg not in out:
            out[seg] = {"population_share_pct": share}

    return out
