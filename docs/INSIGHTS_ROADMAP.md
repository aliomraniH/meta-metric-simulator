# Insights Roadmap — M14 to M16+

This document captures the architectural choices made in M14 (deterministic
detectors + Voyage embeddings client) and the path forward to M16 (agentic
narrator) and beyond.  It exists because some of M14's choices look
arbitrary out of context: why Voyage and not OpenAI embeddings? Why
voyage-3-large by default?  Why z-score over a transformer-based detector?
The answers are below; without them, future maintainers will retread
ground we've already walked.

The architectural rule (per AGENTIC_ARCHITECTURE_INDEX.md §1 / PDF §3.6)
is this: **deterministic detectors compute the anomalies; the agentic
narrator describes them in prose.**  Every output of `insights/` is a
structured object with explicit numeric values that a narrator can only
quote, never invent.  M14 is the deterministic half of that contract.

## §1 — Why event-sequence embeddings beat aggregate-metric embeddings

The simplest possible scenario embedding is a single vector per scenario
built from its aggregate metrics: `[DAP, ad_revenue_per_dau, skip_rate,
violating_view_share, ...]`.  This is cheap (one vector per scenario,
one Voyage call) but loses everything that makes scenarios different.

Two scenarios can have **identical aggregate DAP** but radically
different event patterns.  Scenario A is steady at 3.58B for 30 days.
Scenario B drops to 3.45B on day 5, recovers by day 8, spikes to 3.68B
on day 22, settles at 3.58B by day 30.  An aggregate embedding cannot
distinguish them; for narrator-grade similarity, that distinction is
the whole point.

Event-sequence embeddings preserve trajectory.  The narrator gets to
say *"this scenario looks like the Feb 26 2025 incident"* instead of
*"DAP differs by 3%."*  The first is actionable; the second is noise.

The cost/value compromise we've landed on for M14:

  * **Now (M14 baseline):** per-tick aggregate vectors.  Each tick gets
    its own vector (one scenario × 30 ticks → 30 vectors).  Voyage's
    `voyage-3-large` produces 1024-dim vectors at ~$0.18/M tokens —
    well under the §6.5 cost discipline cap.  Indexed in pgvector.
  * **Next (M14.5):** event-sequence vectors over the raw events table.
    Each scenario contributes a single vector built from a synthetic
    document that summarises *the trajectory* — "DAP rose 7% by tick 8,
    integrity incident at tick 12, recovery at tick 26".  The
    synthetic-document approach is what Anthropic's own demo cookbook
    uses for similar trajectory-similarity tasks.
  * **Out of scope:** raw-event-token transformer embeddings.  These
    work but are 10× the cost.  Revisit when M16 narrator is operating
    at scale and is bottlenecked on retrieval recall.

## §2 — Candidate Voyage models

Voyage publishes several embedding + rerank model families; the
M14 client wires the three we expect to use:

  * **`voyage-3-large`** (1024-dim, general-purpose).  Default for the
    `VoyageClient.embed_*` methods.  Trained on a broad mix; good
    cosine-similarity behaviour on long-form text and scenario
    descriptions.  This is what M16 `l8_find_similar_scenarios` will
    use unless the narrator explicitly requests a different model.
  * **`voyage-finance-2`** (1536-dim, finance-tuned).  Available via
    explicit `model=` kwarg.  Use this when the narrative is about
    monetization-specific patterns — ad revenue trajectory, ARPU
    drift, capex/opex ratios.  The finance-tuning means a
    finance-domain query like *"ad revenue spike in Q2"* retrieves
    closer matches than the same query through `voyage-3-large`.
  * **`voyage-code-3`**.  Not used in M14 — we are not embedding source
    code.  Listed here so future readers don't ask "why didn't we wire
    this in?": the answer is "no use case yet; revisit if M17 starts
    embedding compiled scenario manifests as code-shaped artefacts."

The rerank tier:

  * **`rerank-2.5`**.  Two-stage retrieval is industry standard for
    high-recall systems: embed + cosine-similarity to get top-100,
    then rerank to top-5.  The cosine stage is cheap and approximate;
    the rerank stage is expensive and precise.  We pay for precision
    only on the candidates that survived recall.
  * Defaults: `top_k=5` for narrator context.  M16 may bump this to
    10 for the *compare-scenarios* tool.

L2-normalisation is enforced on every `embed_*` call so retrieval can
use cheap dot products instead of full cosine.  Voyage already returns
normalised vectors but we normalise again defensively — it is cheap
and protects against API drift.

## §3 — Anomaly detection strategy

M14 ships **z-score on per-tick metrics** as the baseline.  This is
the right starting point for three reasons:

  1. **Explainable.**  Every anomaly carries `observed_value`,
     `expected_value` (window mean), and a z-score.  The narrator can
     quote those exactly without inventing context.
  2. **Cheap.**  No model load, no GPU, no retraining.  Pure pandas-
     style arithmetic over the metrics layer.
  3. **Bounded false-positive rate.**  At threshold 2.5 with a
     reasonable window (default 14 ticks), the FP rate on stationary
     series is ~1.2% — low enough that a 30-day scenario produces
     ≤ 1 spurious anomaly on average.

The rolling window is **look-back, not centred**.  The simulator runs
forward in time; a centred window would peek at the future and bias
the z-score.  For real-time narrative use this matters; for
post-hoc batch use we still keep the look-back convention so the
detector behaves the same in both modes.

What the M14 detector **does not** catch:

  * **Regime changes** — a slow drift over 30 days that ends with a
    new stable mean.  Each tick is "in window" against the recent
    past; the detector never sees the full drift.  Future work
    (**M14.5+**): change-point detection.  Bayesian Online
    Change-Point Detection (BOCPD, Adams & MacKay 2007) is the
    canonical choice — pure-Python, cheap, well-studied.  It plugs
    into the same `_MetricRuntimeLike` protocol so the M16 narrator
    sees a uniform interface.
  * **Cross-metric correlation anomalies** — when two normally-
    correlated metrics decouple.  Future work (**M14.6+**): covariance
    monitoring per metric pair.  Only worth the work after the
    narrator is operational and we have evidence that single-metric
    anomalies miss real incidents.

What we are deliberately **not** doing:

  * **Transformer-based or autoencoder-based anomaly detection.**
    Sophisticated, opaque, and produces anomaly scores the narrator
    cannot explain.  Per PDF §3.6: deterministic and explainable
    beats sophisticated and opaque, especially when the downstream
    consumer is an LLM that will be tempted to make up reasons for
    flagged anomalies.

## §4 — What new MCP tools light up once this layer exists

M16's agentic narrator (Layer 8, soft mode, Option A from §2.3)
exposes three tools that consume M14's outputs:

  * **`l8_narrate_anomalies`** — takes the `z_score_anomalies` output
    plus the scenario id and produces a plain-English narrative.
    Soft mode + sanitize/synthesize hook gate.  The narrator MUST NOT
    invent numeric values; every number in the prose is quoted from
    the Anomaly objects.  PostToolUse hook computes `disputed=true`
    if the prose contains a number not present in the Anomaly inputs.
  * **`l8_find_similar_scenarios`** — takes a scenario id, returns
    top-K similar scenarios via the Voyage two-stage retrieval
    (embed → cosine top-100 → rerank-2.5 top-5).  Read-only.  Used by
    the narrator to fetch comparison context — *"this looks like
    scenarios X, Y, Z"*.
  * **`l8_compare_scenarios`** — takes two scenario ids, narrates the
    `percent_diff` and `metric_rank` outputs.  Soft mode; the narrator
    quotes the deterministic numbers.

M17 interview tools reach into M14's outputs **read-only**.  Question
generators ground questions in actual simulator behaviour
("scenario 0123 had a 3.4σ ad revenue spike on day 14 — what would
explain that?") and evaluators check that interview answers match
the deterministic facts.  The interview tools never compute insights
themselves; they read M14's outputs.  This separation keeps the
deterministic detector logic in one place — a single source of
truth for *what counts as anomalous*.

## §5 — Layer-import discipline (recap)

`insights/` may import from `metrics/`, `substrate/`, `models/`,
stdlib, dataclasses, and `scipy`.  It MAY NOT import from
`algorithms/`, `engine/`, `params/`, `baselines/`, `calibration/`,
`interview/`, or `tools/`.  This is the same discipline applied
across every layer in the project (per AGENTIC_ARCHITECTURE_INDEX.md
§8.1).  The point: a single layer-import error catches the *kind* of
mistake that turns M14's "detector reads M9 metrics" into M14's
"detector reads M11 calibration *and* M9 metrics, somehow."  The
import graph is the contract.
