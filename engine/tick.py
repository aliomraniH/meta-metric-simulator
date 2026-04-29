"""Single-day tick orchestration.

run_tick composes the eight Layer-2 algorithms in a fixed order and
writes the resulting events through the M2 substrate writer.  Order
matters — engagement on a given impression must see the post-perturbation
ad-load, ranking must see the post-cascade segment shares, etc.

Determinism: every random call goes through state.rng.  The rng is
mutated in place across algorithm calls; that is what makes a fixed
seed produce a fixed event sequence.
"""
from __future__ import annotations

from typing import Any, Mapping

from algorithms import (
    creator_response,
    engagement_response,
    guardrails,
    integrity_dynamics,
    monetization,
    ranking,
    segment_cascades,
)

from engine.perturbations import apply_active_perturbations
from engine.state import (
    DEFAULT_CANDIDATE_POOL_SIZE,
    DEFAULT_IMPRESSIONS_PER_VIEWER_PER_TICK,
    WorldState,
)


# Default per-tick ad-load policy.  Lives in the engine because the
# scenario's perturbations may overwrite `monetization.ad_load_policy.probability`
# via a dotted-path target.
_DEFAULT_AD_LOAD_PROBABILITY = 0.20


async def run_tick(
    state: WorldState,
    perturbation_defs: list[dict[str, Any]],
    params: Mapping[str, Mapping[str, Any]],
    writer: Any,
    *,
    candidate_pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE,
    impressions_per_viewer: int = DEFAULT_IMPRESSIONS_PER_VIEWER_PER_TICK,
) -> WorldState:
    """Run a single tick.  Returns the updated WorldState.

    Args:
        state: WorldState at the start of the tick.
        perturbation_defs: scenario.perturbations.
        params: dict of all six loaded params yaml dicts.
        writer: substrate.writer.EventWriter instance scoped to a scenario.
        candidate_pool_size: how many reels to consider per viewer.
        impressions_per_viewer: top-K cap on impressions emitted per viewer.

    The tick step order:
      1. Apply active perturbations to a tick-state bag.
      2. Update creator supply from last tick's earnings (lagged inside
         the algorithm).
      3. For each viewer, build candidate pool, rank, emit impressions
         and engagement / ad events.
      4. Evolve integrity_prevalence.
      5. Run guardrails on the tick state and emit guardrail_fired events.
      6. Propagate segment cascades.
      7. Increment tick_day.
    """
    tick_day = state.tick_day
    scenario_id = writer.scenario_id  # type: ignore[attr-defined]
    seq_counter = {"n": 1}  # mutable counter so all callees can advance it

    # --- 1. Perturbations -----------------------------------------------------
    # Pre-build the monetization view so we can seed tick_state_bag with the
    # current per-param values; perturbations then step them per tick.
    monetization_curves = _monetization_view(params)
    tick_state_bag: dict[str, Any] = {
        "monetization": {
            "ad_load_policy": {"probability": _DEFAULT_AD_LOAD_PROBABILITY},
            # Conversion-lift propagation lives in the bag so calibration
            # test_04 (and any other GEM-style scenario) can step it via
            # the perturbation system.  Default comes from monetization_curves.
            "conversion_lift_propagation": float(monetization_curves["conversion_lift_propagation"]),
        },
        "ranking_weights": _ranking_weights_view(params),
        "integrity_dynamics": {
            "organic_decay_per_day": float(
                (params.get("integrity_dynamics") or {})
                .get("organic_decay_per_day", {})
                .get("value", 0.05)
            )
        },
    }
    tick_state_bag = apply_active_perturbations(tick_state_bag, perturbation_defs, tick_day)

    ad_load_policy = tick_state_bag["monetization"]["ad_load_policy"]
    # Build a per-tick monetization-params dict that overrides the static
    # conversion_lift_propagation with whatever the tick's perturbation
    # produced.  algorithms/monetization.maybe_ad reads it from this dict.
    monetization_params_this_tick = {
        **monetization_curves,
        "conversion_lift_propagation": float(
            tick_state_bag["monetization"]["conversion_lift_propagation"]
        ),
    }

    # --- 2. Creator supply update --------------------------------------------
    last_earnings = state.scratch.get("last_tick_earnings_by_creator", {})
    creator_econ = _creator_econ_view(params)
    next_creators: dict[str, dict[str, Any]] = {}
    for cid, cstate in state.creators.items():
        earnings = float(last_earnings.get(cid, 0.0))
        next_creators[cid] = creator_response.update_supply(cstate, earnings, creator_econ)
    state.creators = next_creators

    # --- 3. Per-viewer ranking + engagement ----------------------------------
    earnings_this_tick: dict[str, float] = {}
    tick_events: list[dict[str, Any]] = []  # for guardrails read-only inspection

    # monetization_curves was already built above; we want the per-tick
    # variant that carries the perturbed conversion_lift_propagation.
    ranking_params = _ranking_view(params)
    seg_props = _segment_props_view(params)

    reel_ids = list(state.reels.keys())
    # Sort viewer ids so determinism doesn't depend on dict iteration order
    # (Python preserves insertion, but this is belt-and-braces).
    for viewer_id in sorted(state.viewers.keys()):
        viewer_state = state.viewers[viewer_id]
        candidate_pool = _candidate_pool(state, reel_ids, candidate_pool_size)

        scored = ranking.rank(viewer_state, candidate_pool, ranking_params, state.rng)
        for sr in scored[:impressions_per_viewer]:
            seq = seq_counter["n"]
            seq_counter["n"] = seq + 1
            timestamp = float(tick_day * 86400 + seq)

            impression = {
                "scenario_id":         scenario_id,
                "tick_day":            tick_day,
                "intra_day_seq":       seq,
                "timestamp":           timestamp,
                "event_type":          "impression",
                "viewer_id":           viewer_id,
                "viewer_segment":      viewer_state.get("segment"),
                "viewer_geo":          viewer_state.get("geo"),
                "viewer_tenure_days":  viewer_state.get("tenure_days"),
                "creator_id":          sr.creator_id,
                "creator_tier":        sr.creator_tier,
                "reel_id":             sr.reel_id,
                "reel_duration_sec":   sr.reel_duration_sec,
                "reel_topic_cluster":  sr.reel_topic_cluster,
                "surface":             "reels_tab",
                "pool":                sr.pool,
                "rank_score":          sr.score,
            }
            await writer.write(impression)
            tick_events.append(impression)

            # Decide ad vs organic engagement.  We do NOT chain both — an
            # ad slot replaces the organic engagement on this impression.
            ad_event = monetization.maybe_ad(
                impression, ad_load_policy, monetization_params_this_tick, state.rng
            )
            if ad_event is not None:
                seq_counter["n"] = int(ad_event["intra_day_seq"]) + 1
                await writer.write(ad_event)
                tick_events.append(ad_event)
                creator_id = sr.creator_id
                if creator_id is not None:
                    earnings_this_tick[creator_id] = (
                        earnings_this_tick.get(creator_id, 0.0)
                        + float(ad_event.get("ad_revenue_usd", 0.0))
                    )
                continue

            engagement_events = engagement_response.respond(
                viewer_state, impression, seg_props, state.rng,
                observed_ad_load_pct=float(ad_load_policy.get("probability", 0.0)),
                ad_load_elasticity=float(monetization_curves.get("ad_load_to_skip_elasticity", 0.0)),
            )
            for ev in engagement_events:
                seq_counter["n"] = int(ev["intra_day_seq"]) + 1
                await writer.write(ev)
                tick_events.append(ev)

    # --- 4. Integrity dynamics -----------------------------------------------
    integrity_params = _integrity_view(params)
    integrity_perts_for_tick = [
        p for p in (perturbation_defs or [])
        if isinstance(p.get("target"), str)
        and p["target"].startswith(("violating_prevalence", "detection_rate", "by_topic."))
    ]
    state.integrity_prevalence = integrity_dynamics.evolve(
        state.integrity_prevalence,
        integrity_perts_for_tick,
        integrity_params,
    )

    # --- 5. Guardrails -------------------------------------------------------
    seg_state_map = _summarise_segments(state.viewers, tick_events)
    guardrail_tick_state = {
        "scenario_id": scenario_id,
        "tick_day": tick_day,
        "intra_day_seq": seq_counter["n"],
        "timestamp": float(tick_day * 86400 + seq_counter["n"]),
        "prevalence_state": state.integrity_prevalence,
        "segment_state_map": seg_state_map,
        "creator_supply": _avg_posting_rate(state.creators),
        "ad_load_observed": _observed_ad_load(tick_events),
    }
    guardrail_params = (params.get("guardrail_thresholds") or {})
    # Build a per-kind threshold dict the algorithm expects.
    g_params = {
        "integrity_prevalence":     _guardrail_block(guardrail_params, "integrity_prevalence"),
        "teen_exposure":            _guardrail_block(guardrail_params, "teen_exposure"),
        "creator_supply_collapse":  _guardrail_block(guardrail_params, "creator_supply_collapse"),
        "ad_load_ceiling":          _guardrail_block(guardrail_params, "ad_load_ceiling"),
    }
    guardrail_events = guardrails.check(guardrail_tick_state, tick_events, g_params)
    for ev in guardrail_events:
        seq_counter["n"] = int(ev["intra_day_seq"]) + 1
        await writer.write(ev)

    # --- 6. Segment cascades -------------------------------------------------
    new_seg_map = segment_cascades.propagate(seg_state_map, _cascades_view(params))
    _redistribute_viewers(state, new_seg_map)

    # --- 7. Advance tick + carry earnings forward ----------------------------
    state.tick_day = tick_day + 1
    state.scratch["last_tick_earnings_by_creator"] = earnings_this_tick
    state.active_perturbations = list(perturbation_defs or [])
    return state


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _candidate_pool(state: WorldState, reel_ids: list[str], size: int) -> list[dict[str, Any]]:
    if not reel_ids:
        return []
    n = min(size, len(reel_ids))
    chosen = state.rng.sample(reel_ids, n)
    return [state.reels[rid] for rid in chosen]


def _ranking_weights_view(params: Mapping[str, Any]) -> dict[str, float]:
    """Echo the params block into a flat dict the perturbations bag can mutate."""
    rw = params.get("ranking_weights") or {}
    return {
        "alpha_watch_time":           _v(rw, "alpha_watch_time"),
        "beta_like_rate_connected":   _v(rw, "beta_like_rate_connected"),
        "gamma_send_rate_unconnected":_v(rw, "gamma_send_rate_unconnected"),
        "delta_skip_penalty_3s":      _v(rw, "delta_skip_penalty_3s"),
        "epsilon_quality_bonus":      _v(rw, "epsilon_quality_bonus"),
        "zeta_repetition_penalty":    _v(rw, "zeta_repetition_penalty"),
        "exploration_sigma":          _v(rw, "exploration_sigma"),
    }


def _ranking_view(params: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the M5 yaml shape into what algorithms/ranking.rank expects."""
    rw = params.get("ranking_weights") or {}
    pool_mix = rw.get("connected_vs_unconnected_pool_mix") or {}
    connected = float((pool_mix.get("connected_share_pct") or {}).get("value", 35)) / 100.0
    return {
        "weights": {
            # Map the Greek-prefixed M5 names to the feature names
            # algorithms/ranking._features_for emits.
            "predicted_watch_time":  _v(rw, "alpha_watch_time"),
            "predicted_engagement":  _v(rw, "beta_like_rate_connected"),
            "predicted_send":        _v(rw, "gamma_send_rate_unconnected"),
            "novelty":               _v(rw, "epsilon_quality_bonus"),
            "topic_affinity":        max(0.0, _v(rw, "epsilon_quality_bonus")),
            "creator_tier_bonus":    0.5,
            "integrity_score":       max(0.0, -_v(rw, "delta_skip_penalty_3s")),
        },
        "pool_split": {"connected": connected, "unconnected": 1.0 - connected},
        "top_n": DEFAULT_IMPRESSIONS_PER_VIEWER_PER_TICK,
    }


def _monetization_view(params: Mapping[str, Any]) -> dict[str, Any]:
    mc = params.get("monetization_curves") or {}
    ecpm = mc.get("ecpm_distribution") or {}
    return {
        "ecpm_mean":    _v(ecpm, "mean", default=8.0),
        "ecpm_sd":      _v(ecpm, "sd", default=2.0),
        "ecpm_floor":   _v(ecpm, "floor", default=0.5),
        "ecpm_ceiling": _v(ecpm, "ceiling", default=25.0),
        # M11.5 wirings — algorithms/monetization reads these directly.
        "reels_vs_feed_efficiency_ratio": _v(mc, "reels_vs_feed_efficiency_ratio", default=0.90),
        "conversion_lift_propagation":    _v(mc, "conversion_lift_propagation", default=1.0),
        # Used by engine/tick to plumb ad-load → p_skip feedback into
        # engagement_response (M11.5 wiring for calibration test_02).
        "ad_load_to_skip_elasticity":     _v(mc, "ad_load_to_skip_elasticity", default=0.0),
    }


def _segment_props_view(params: Mapping[str, Any]) -> dict[str, Any]:
    """Pass-through: engagement_response expects {segment_propensities: {seg: {...}}}."""
    sp = params.get("segment_propensities") or {}
    flat: dict[str, Any] = {}
    for seg_name, block in (sp.get("segments") or {}).items():
        # Each leaf in block is {value, source, ...}.  Flatten to plain numbers
        # the algorithm can consume.  Skip non-leaf nested blocks.
        flat[seg_name] = {}
        for k, v in block.items():
            if isinstance(v, dict) and "value" in v:
                flat[seg_name][k] = v["value"]
        # engagement_response also looks for p_skip / completion_pct ranges;
        # synthesize from skip_threshold_sec when not present.
        flat[seg_name].setdefault("p_skip", 0.30)
        flat[seg_name].setdefault("completion_pct_min", 0.55)
        flat[seg_name].setdefault("completion_pct_max", 1.00)
        flat[seg_name].setdefault("view_end_threshold_pct", 0.95)
    return {"segment_propensities": flat}


def _creator_econ_view(params: Mapping[str, Any]) -> dict[str, Any]:
    ce = params.get("creator_economics") or {}
    tm = ce.get("tier_multipliers") or {}
    return {
        "posting_lag_days":               int(_v(ce, "posting_lag_days", default=7)),
        "earnings_to_posting_elasticity": _v(ce, "earnings_to_posting_elasticity", default=0.5),
        "smoothing":                      _v(ce, "smoothing", default=0.5),
        "posting_floor_per_day":          _v(ce, "posting_floor_per_day", default=0.05),
        "posting_ceiling_per_day":        _v(ce, "posting_ceiling_per_day", default=5.0),
        "tier_multipliers": {
            tier: _v(tm, tier, default=1.0) for tier in tm.keys()
        },
    }


def _integrity_view(params: Mapping[str, Any]) -> dict[str, Any]:
    intg = params.get("integrity_dynamics") or {}
    return {
        "organic_decay_per_day":     _v(intg, "organic_decay_per_day", default=0.05),
        "detection_growth_per_day":  _v(intg, "detection_growth_per_day", default=0.01),
        "floor":                     _v(intg, "floor", default=0.0),
        "ceiling":                   _v(intg, "ceiling", default=1.0),
    }


def _cascades_view(params: Mapping[str, Any]) -> dict[str, Any]:
    """Identity matrix by default — M5 yaml does not yet ship a substitution
    matrix (that's M5-followup territory)."""
    sp = params.get("segment_propensities") or {}
    seg_names = list((sp.get("segments") or {}).keys())
    matrix = {seg: {seg: 1.0} for seg in seg_names}  # identity
    return {"substitution_matrix": matrix, "mass_tolerance": 1e-6}


def _guardrail_block(guardrail_params: Mapping[str, Any], kind: str) -> dict[str, Any]:
    block = guardrail_params.get(kind) or {}
    direction = block.get("direction", "above")
    out: dict[str, Any] = {"direction": direction}
    for sev in ("warn", "breaker", "halt"):
        sub = block.get(sev) or {}
        if "value" in sub:
            out[sev] = sub["value"]
    return out


def _summarise_segments(
    viewers: Mapping[str, dict[str, Any]],
    tick_events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build segment_state_map: {segment: {population_share_pct, integrity_exposure}}."""
    counts: dict[str, int] = {}
    for v in viewers.values():
        seg = v.get("segment", "unknown")
        counts[seg] = counts.get(seg, 0) + 1
    total = sum(counts.values()) or 1
    return {
        seg: {
            "population_share_pct": 100.0 * n / total,
            "integrity_exposure": 0.0008,  # baseline; perturbed by integrity_dynamics in higher-order setups
        }
        for seg, n in counts.items()
    }


def _avg_posting_rate(creators: Mapping[str, dict[str, Any]]) -> float:
    rates = [float(c.get("posting_rate_per_day", 0.0)) for c in creators.values()]
    return sum(rates) / max(len(rates), 1)


def _observed_ad_load(tick_events: list[dict[str, Any]]) -> float:
    impressions = sum(1 for e in tick_events if e["event_type"] == "impression")
    ads = sum(1 for e in tick_events if e["event_type"] == "ad_impression")
    if impressions + ads == 0:
        return 0.0
    return ads / (impressions + ads)


def _redistribute_viewers(
    state: WorldState,
    new_seg_map: Mapping[str, dict[str, Any]],
) -> None:
    """Cascade output may shift segment shares; we don't physically reassign
    viewers (that would break per-viewer continuity).  Stash the new shares
    on the WorldState scratch so downstream tooling can read them."""
    state.scratch["segment_shares"] = {
        seg: float(s.get("population_share_pct", 0.0)) for seg, s in new_seg_map.items()
    }


def _v(block: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    sub = block.get(key)
    if isinstance(sub, dict) and "value" in sub:
        try:
            return float(sub["value"])
        except (TypeError, ValueError):
            return default
    if isinstance(sub, (int, float)):
        return float(sub)
    return default
