# Layer 2 — Algorithm Contracts

This document is the contract for the eight pure-function algorithms that
compose the simulator's behaviour.  Each algorithm is its own module under
`algorithms/`; none of them import from each other, none of them read from
disk, and none of them touch global state.

## House rules (non-negotiable)

These rules are enforced by the layer-import linter (`tools/lint_layer_imports.py`,
M18) and by the per-algorithm tests (M4):

1. **Pure functions.**  Inputs come in via arguments; outputs come out as
   return values or as event dicts ready for `substrate/writer.py`.  No
   `print`, no logging side-effects beyond `log.debug`, no mutation of
   inputs that the caller did not opt into.
2. **No cross-imports.**  An algorithm MAY NOT import from `baselines/`,
   `engine/`, `metrics/`, `calibration/`, `interview/`, `tools/`, or any
   other algorithm in `algorithms/`.  It MAY import from `params/` only
   in the sense that the engine passes parameter dicts in as arguments —
   the algorithm itself never opens a YAML file.
3. **Stochastic behaviour uses an injected rng.**  Algorithms accept a
   `rng: random.Random` argument and MUST NOT touch `random.random` or
   `numpy.random` at module level.  Determinism (M7's acceptance test)
   depends on this.
4. **Algorithms do not write events.**  They return event dicts.  The
   engine (M7) is the only layer that calls `EventWriter.write()`.  This
   keeps algorithms unit-testable without a database.
5. **Emitted event types MUST exist in `docs/EVENT_SCHEMA.md`.**  Adding
   a new event type touches `substrate/schema.sql` and `EVENT_SCHEMA.md`
   first — never an algorithm in isolation.
6. **State slices are passed in, mutations are returned.**  An algorithm
   that updates creator state takes the relevant slice and returns the
   new slice; the engine merges.  No in-place updates to shared dicts.

## Shared types (informal)

The contracts below refer to a few shapes that live in plain Python
dicts.  These are not Pydantic models in Layer 2 — algorithms stay
dependency-light.  The engine (Layer 6) materialises typed objects when
useful.

- **`ViewerState`** — `{viewer_id, segment, geo, tenure_days, recent_topic_clusters: list[str], fatigue: float, integrity_exposure: float}`.
- **`CreatorState`** — `{creator_id, tier, posting_rate_per_day: float, recent_earnings_usd: float, supply_intent: float}`.
- **`Reel`** — `{reel_id, creator_id, creator_tier, duration_sec, topic_cluster, integrity_score: float, novelty: float}`.
- **`ScoredReel`** — `Reel ∪ {rank_score: float, pool: "connected"|"unconnected"}`.
- **`SegmentStateMap`** — `{segment_name: {reach, watch_time, integrity_exposure, ad_load, ...}}`.
- **`PrevalenceState`** — `{violating_prevalence: float, detection_rate: float, by_topic: dict[str, float]}`.
- **`PerturbationDef`** — `{type: "ramp"|"step"|"spike", target: str, ...}` (full schema in M7).
- **`Event`** — a dict matching one row of `events` per `EVENT_SCHEMA.md`.

`rng` is always `random.Random` so callers can seed deterministically.

---

## 1 — ranking

**Owner:** `algorithms/ranking.py`

**Inputs**
- State slice: `viewer_state: ViewerState`.
- Params: a sub-dict of `params/ranking_weights.yaml` containing
  per-feature weights (engagement vs watch-time vs send), pool split
  policy (connected:unconnected ratio), and top-N for impression emission.
- Events from this tick: none read directly — the engine supplies the
  candidate pool the ranker scores against.
- Other inputs: `candidate_pool: list[Reel]` (split into connected and
  unconnected by the engine before being handed in), `rng: random.Random`.

**Outputs**
- Return value: `list[ScoredReel]` ordered by `rank_score` desc, length
  ≤ params["top_n"].  The list carries pool labels so the engine can
  emit impressions with the correct `pool` value.
- New events emitted (returned as a parallel list): one `impression`
  event per top-N entry.  No other types.

**Pure-function signature**
```python
def rank(
    viewer_state: dict,
    candidate_pool: list[dict],
    params: dict,
    rng: random.Random,
) -> tuple[list[dict], list[dict]]:
    """Returns (scored_top_n, impression_events)."""
```

**Invariants**
- Same `(viewer_state, candidate_pool, params, rng_seed)` → identical
  outputs.  No wall-clock, no `os.environ`, no module random.
- Output length ≤ `params["top_n"]`.  Connected:unconnected split
  matches `params["pool_split"]` (the engine pre-bucketises; the ranker
  honours the requested ratio).
- `rank_score` is a finite float; ties broken by `rng.random()` so
  identical scores still produce a stable order under a fixed seed.
- Impression events carry `viewer_id`, `viewer_segment`, `reel_id`,
  `creator_id`, `surface`, `pool`, `rank_score` (per `EVENT_SCHEMA.md`).

**Dependencies**
- `params/ranking_weights.yaml` only.  Does NOT import from baselines/,
  other algorithms, or any layer above 2.

---

## 2 — engagement_response

**Owner:** `algorithms/engagement_response.py`

**Inputs**
- State slice: `viewer_state: ViewerState`.
- Params: a sub-dict of `params/segment_propensities.yaml` keyed by
  `viewer_state["segment"]` with per-action base rates (watch/skip/like/
  send/save/comment/replay) plus reel-feature elasticities (duration,
  topic-cluster affinity, novelty).
- Events from this tick: the single `impression` event being responded
  to.
- Other inputs: `rng: random.Random`.

**Outputs**
- Return value: an updated `ViewerState` slice (e.g. `fatigue` increment,
  `recent_topic_clusters` rotation).
- New events emitted (returned as a list):
  - Exactly one of `watch`, `skip`, or `view_end` per impression.
  - Zero or more of `like`, `send`, `save`, `comment`, `replay` chained
    onto a `watch` (gated by per-action propensities).

**Pure-function signature**
```python
def respond(
    viewer_state: dict,
    impression: dict,
    params: dict,
    rng: random.Random,
) -> tuple[dict, list[dict]]:
    """Returns (next_viewer_state, engagement_events)."""
```

**Invariants**
- Probabilities sum to 1 across the mutually-exclusive `{watch, skip,
  view_end}` outcome.  `like/send/save/comment/replay` are independent
  Bernoulli trials conditioned on `watch`.
- `watch_duration_sec` ≤ `reel_duration_sec` from the impression.
- A `skip` event always has `watch_duration_sec ≤ 3.0` (the substrate
  contract).
- The returned `viewer_state` differs from the input only in fields
  documented in the doc-string; never mutates the input dict.

**Dependencies**
- `params/segment_propensities.yaml` only.  Does NOT import from
  ranking, monetization, or any other algorithm.

---

## 3 — monetization

**Owner:** `algorithms/monetization.py`

**Inputs**
- State slice: none beyond what the engine threads in.
- Params: a sub-dict of `params/monetization_curves.yaml` containing
  ad-load policy (impressions-per-session ceiling, slot frequency),
  eCPM curves keyed by `(viewer_segment, viewer_geo)` or a default,
  and ad-impression-to-skip elasticity.
- Events from this tick: the `impression` event under consideration.
- Other inputs: `ad_load_policy: dict` (already merged with any active
  perturbation by the engine), `rng: random.Random`.

**Outputs**
- Return value: `Event | None`.  Returns `None` when this slot is not
  monetised; otherwise returns an `ad_impression` event with
  `ad_impression=1` and a sampled `ad_revenue_usd`.
- The original organic `impression` event is unchanged.  When an ad
  fires, the engine emits both events; the ranker still recorded the
  organic impression upstream.

**Pure-function signature**
```python
def maybe_ad(
    impression: dict,
    ad_load_policy: dict,
    params: dict,
    rng: random.Random,
) -> dict | None:
    """Returns an ad_impression event or None."""
```

**Invariants**
- `ad_revenue_usd` is non-negative and finite.
- The probability of returning a non-None event matches the ad-load
  policy's slot frequency for the viewer's session position (the engine
  passes `position` inside `ad_load_policy`).
- `ad_impression` events carry `viewer_id`, `viewer_segment`,
  `ad_impression=1`, `ad_revenue_usd` (per `EVENT_SCHEMA.md`).
- Does not call engagement_response — the viewer's reaction to the ad
  (skip elasticity) is handled by engagement_response on the next
  impression.

**Dependencies**
- `params/monetization_curves.yaml` only.  Does NOT import from
  ranking, engagement_response, or any other algorithm.

---

## 4 — integrity_dynamics

**Owner:** `algorithms/integrity_dynamics.py`

**Inputs**
- State slice: `prevalence_state: PrevalenceState` —
  `{violating_prevalence, detection_rate, by_topic}`.
- Params: a sub-dict of `params/integrity_dynamics.yaml` with the
  organic decay constant, detection-rate growth/decay, the per-topic
  injection rates, and the floor/ceiling for prevalence.
- Events from this tick: none read directly — perturbations come in
  pre-applied (the perturbations algorithm runs first per `tick.py`'s
  step order).
- Other inputs: `perturbations: list[PerturbationDef]` for any
  integrity-targeted perturbation that should adjust prevalence
  this tick (the engine filters them by `target` before passing).

**Outputs**
- Return value: a new `PrevalenceState` with updated
  `violating_prevalence` and `by_topic` slices.
- New events emitted: none.  The `guardrails` algorithm (§7) is the one
  that emits `guardrail_fired` events when prevalence crosses thresholds.

**Pure-function signature**
```python
def evolve(
    prevalence_state: dict,
    perturbations: list[dict],
    params: dict,
) -> dict:
    """Returns the next PrevalenceState."""
```

**Invariants**
- `violating_prevalence` stays within `[params["floor"], params["ceiling"]]`
  after the update; values are clamped, not silently truncated.
- `detection_rate` ∈ [0, 1].
- Pure: same inputs → same outputs.  No `rng` argument because
  prevalence evolves deterministically; stochastic shocks come in via
  perturbations from Layer 6.
- Does not read or modify the `events` slice.

**Dependencies**
- `params/integrity_dynamics.yaml` only.  Does NOT import from
  guardrails, perturbations, or any other algorithm.

---

## 5 — creator_response

**Owner:** `algorithms/creator_response.py`

**Inputs**
- State slice: `creator_state: CreatorState` —
  `{creator_id, tier, posting_rate_per_day, recent_earnings_usd, supply_intent}`.
- Params: a sub-dict of `params/creator_economics.yaml` with the
  earnings-to-posting elasticity per tier, a posting-rate floor and
  ceiling, and a smoothing factor so a single thin tick doesn't
  collapse supply to zero.
- Events from this tick: none read directly.  The engine sums the
  creator's `ad_revenue_usd` from this tick's events and passes the
  total in as `earnings_this_tick`.
- Other inputs: `earnings_this_tick: float`, `rng: random.Random`
  (a small noise term breaks deterministic sawtooth patterns when many
  creators share an identical state).

**Outputs**
- Return value: an updated `CreatorState` with new
  `posting_rate_per_day`, `recent_earnings_usd`, and `supply_intent`.
- New events emitted (returned as a list): zero or more `creator_post`
  events for the next tick — the engine schedules the actual reels
  later, but the algorithm is the source of truth for how many posts
  this creator intends.

**Pure-function signature**
```python
def update_supply(
    creator_state: dict,
    earnings_this_tick: float,
    params: dict,
    rng: random.Random,
) -> tuple[dict, list[dict]]:
    """Returns (next_creator_state, scheduled_creator_post_events)."""
```

**Invariants**
- `posting_rate_per_day` stays within `[params["floor"], params["ceiling"]]`.
- A creator with zero earnings does not get a negative posting rate;
  the smoothing factor pulls them toward `floor`, never below.
- `creator_post` events carry the required fields from `EVENT_SCHEMA.md`
  (`creator_id`, `creator_tier`, `reel_id`, `reel_duration_sec`,
  `reel_topic_cluster`).
- Does not read events from the substrate.  The engine summarises the
  prior tick's `ad_impression` events and hands in `earnings_this_tick`.

**Dependencies**
- `params/creator_economics.yaml` only.  Does NOT import from
  monetization, ranking, or any other algorithm.

---

## 6 — segment_cascades

**Owner:** `algorithms/segment_cascades.py`

**Inputs**
- State slice: `segment_state_map: SegmentStateMap` keyed by segment
  name (`teen`, `young_adult`, `snacker`, `lean_back`, …) with each
  segment's reach, watch time, integrity exposure, and ad load for the
  tick that just finished.
- Params: a sub-dict of `params/segment_propensities.yaml` containing
  the cross-segment substitution matrix (e.g. when teens lose share,
  how much flows to young_adult vs snacker), and per-segment
  inertia/dampening factors.
- Events from this tick: none read directly.  The engine summarises
  the tick's events into the `segment_state_map` before this runs.

**Outputs**
- Return value: an updated `SegmentStateMap` that reflects the
  redistribution.  Total reach across segments is conserved (within
  rounding tolerance) — substitution moves mass, it does not create or
  destroy.
- New events emitted: none.  This algorithm is state-only.

**Pure-function signature**
```python
def propagate(
    segment_state_map: dict,
    params: dict,
) -> dict:
    """Returns the next SegmentStateMap after cross-segment redistribution."""
```

**Invariants**
- `sum(seg["reach"] for seg in result.values())` equals the input sum
  to within `params["mass_tolerance"]` (default 1e-6).  This is the
  conservation rule the cross-segment substitution metric (M10) relies
  on.
- No segment ends with negative reach, watch time, or ad load.
- Pure-deterministic: no `rng` argument.  Cascades are linear; noise
  belongs in the upstream segment_propensities, not here.

**Dependencies**
- `params/segment_propensities.yaml` only.  Does NOT import from
  ranking, engagement_response, or any other algorithm.

---

## 7 — guardrails

**Owner:** `algorithms/guardrails.py`

**Inputs**
- State slice: `tick_state` — a small bag the engine assembles from
  the post-tick view: `{prevalence_state, segment_state_map, ad_load_observed}`.
- Params: a sub-dict of `params/guardrail_thresholds.yaml` with the
  four circuit-breaker thresholds:
    - integrity prevalence ceiling
    - teen exposure ceiling
    - creator supply collapse floor
    - ad-load ceiling
  plus per-guardrail hysteresis (so a flapping signal doesn't fire
  every tick).
- Events from this tick: `recent_events: list[Event]` — read only to
  detect short-window patterns (e.g. ad-load spike inside a single
  session).  Read, never mutated.

**Outputs**
- Return value: nothing besides the events list (the engine carries
  guardrail-driven state changes, e.g. tightening distribution, in a
  separate path that reads these events).
- New events emitted (returned as a list): zero or more
  `guardrail_fired` events with `payload = {guardrail_kind, threshold,
  observed}`.  May also carry `viewer_segment` or `viewer_geo` when
  the guardrail is segment-scoped.

**Pure-function signature**
```python
def check(
    tick_state: dict,
    recent_events: list[dict],
    params: dict,
) -> list[dict]:
    """Returns guardrail_fired events; never mutates inputs."""
```

**Invariants**
- A guardrail fires at most once per tick per kind (the function's
  internal hysteresis state lives in `tick_state["guardrails"]`, which
  the engine carries forward).
- The four required guardrail kinds — `integrity_prevalence`,
  `teen_exposure`, `creator_supply_collapse`, `ad_load_ceiling` — are
  always evaluated even if their threshold is not crossed; the
  function returns an empty list rather than skipping the check.
- `guardrail_fired` events satisfy the `EVENT_SCHEMA.md` payload rule
  (`guardrail_kind`, `threshold`, `observed` all present).
- No `rng` argument.  Guardrails are deterministic threshold checks.

**Dependencies**
- `params/guardrail_thresholds.yaml` only.  Does NOT import from
  integrity_dynamics, segment_cascades, or any other algorithm.

---

## 8 — perturbations

**Owner:** `algorithms/perturbations.py`

**Inputs**
- State slice: the full mutable tick `state` bag (params overrides,
  ad-load policy, integrity injection rate, etc).  Perturbations adjust
  the *parameters in flight* — they do not directly emit user-facing
  effects; engagement / monetization / integrity see the adjusted
  inputs on the same tick.
- Params: none of its own.  The perturbation manifest itself is the
  spec; this algorithm interprets it.
- Other inputs: `perturbation_def: PerturbationDef`, `tick_day: int`.

The three supported perturbation shapes (matching the M7 schema):
- `{type: "ramp",  target, from, to, days}` — linear ramp over `days`.
- `{type: "step",  target, value}` — single-tick set-and-hold.
- `{type: "spike", target, to, duration_days}` — return to baseline
  after `duration_days`.

**Outputs**
- Return value: the updated `state` bag with the targeted parameter
  set to its tick-day-appropriate value.
- New events emitted (returned as a list): exactly one
  `scenario_perturbation` event per applied perturbation, with
  `payload = {kind, target, value}`.

**Pure-function signature**
```python
def apply(
    state: dict,
    perturbation_def: dict,
    tick_day: int,
) -> tuple[dict, list[dict]]:
    """Returns (next_state, scenario_perturbation_events)."""
```

**Invariants**
- `target` must be a recognised key path inside `state`; unknown keys
  raise `KeyError` rather than silently no-op.  This is intentional —
  the engine validates the manifest at scenario-compile time (M15).
- Same `(state, perturbation_def, tick_day)` → identical outputs.  No
  `rng`; perturbations are deterministic by construction.
- A `ramp` evaluated at `tick_day == 0` returns `from`; at
  `tick_day == days` returns `to`; in between, linear interpolation.
- A `spike` returns to baseline at `tick_day == start_day +
  duration_days` (the engine tracks `start_day` and passes the
  effective `tick_day` offset).
- `scenario_perturbation` events satisfy `EVENT_SCHEMA.md`'s payload
  rule (`kind`, `target`, `value` all present).

**Dependencies**
- No params file.  The perturbation manifest is the spec.  Does NOT
  import from any other algorithm.

---

## Cross-reference table

| #  | Algorithm             | Module path                                | Reads from (params)                                | Emits event types                                           |
|----|-----------------------|--------------------------------------------|----------------------------------------------------|-------------------------------------------------------------|
| 1  | ranking               | `algorithms/ranking.py`                    | `params/ranking_weights.yaml`                      | `impression`                                                |
| 2  | engagement_response   | `algorithms/engagement_response.py`        | `params/segment_propensities.yaml`                 | `watch`, `skip`, `view_end`, `like`, `send`, `save`, `comment`, `replay` |
| 3  | monetization          | `algorithms/monetization.py`               | `params/monetization_curves.yaml`                  | `ad_impression`                                             |
| 4  | integrity_dynamics    | `algorithms/integrity_dynamics.py`         | `params/integrity_dynamics.yaml`                   | (none — emits state delta only)                             |
| 5  | creator_response      | `algorithms/creator_response.py`           | `params/creator_economics.yaml`                    | `creator_post`                                              |
| 6  | segment_cascades      | `algorithms/segment_cascades.py`           | `params/segment_propensities.yaml`                 | (none — emits state delta only)                             |
| 7  | guardrails            | `algorithms/guardrails.py`                 | `params/guardrail_thresholds.yaml`                 | `guardrail_fired`                                           |
| 8  | perturbations         | `algorithms/perturbations.py`              | (none — manifest-driven)                           | `scenario_perturbation`                                     |

Every event type in the third column is defined in `docs/EVENT_SCHEMA.md`.
The union across algorithms covers all 13 substrate event types except for
`creator_post` reels actually being viewed (those are turned into
`impression` events by ranking when the engine schedules them).

## What is intentionally NOT here

- **No baseline references.**  Algorithms describe HOW behaviour evolves
  given parameters.  WHAT the real platform's KPIs are belongs in
  `baselines/data/*.yaml` (Layer 4) and is loaded by calibration tests
  (Layer 9), not by algorithms.
- **No cross-algorithm orchestration.**  The order in which algorithms
  run inside a tick — perturbations → creator_response → ranking →
  engagement_response → monetization → integrity_dynamics → guardrails
  → segment_cascades — is `engine/tick.py`'s job (M7), not anything in
  Layer 2.
- **No metric definitions.**  Aggregations over emitted events live in
  `metrics/definitions/` (M9).  Algorithms produce the raw rows; metrics
  turn rows into KPIs.

