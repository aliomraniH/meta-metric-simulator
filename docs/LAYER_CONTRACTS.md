# Layer Contracts

The simulator is divided into 11 layers (0 through 10).  Each row below is
the canonical contract for that layer: what it owns, what it may import
from, what it must NOT import from, how often it refreshes, who owns it,
and — for layers that involve LLM judgment — its agentic mode.

The "May NOT Import From" column is the load-bearing one.  The layer-import
linter (`tools/lint_layer_imports.py`, M18) enforces it.

| Layer | Owns                                                                    | May Import From                                                | May NOT Import From                                                          | Refresh Cadence       | Owner                  | Agentic? | Sanitize/Synthesize Mode                                                    |
|-------|-------------------------------------------------------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------|-----------------------|------------------------|----------|-----------------------------------------------------------------------------|
| 0     | `infra/` — FastMCP front door, OAuth, EventStore, DB, telemetry         | (stdlib + third-party)                                         | every business layer (1–10)                                                  | as needed             | platform               | No       | —                                                                           |
| 1     | `substrate/` — events table DDL, EventWriter, EventReader               | infra                                                          | algorithms, params, baselines, engine, metrics, insights, calibration        | static                | substrate              | No       | —                                                                           |
| 2     | `algorithms/` — 8 pure-function algorithms (ranking, engagement, …)     | infra, params (passed in as dict)                              | substrate (read directly), baselines, engine, metrics, calibration, insights | code-change           | algorithms             | No       | —                                                                           |
| 3     | `params/` — per-algorithm YAML with provenance                          | (yaml only)                                                    | every code path                                                              | code-change           | algorithms             | No       | —                                                                           |
| 4     | `baselines/` — Layer 4 yaml + agentic ingestion server (M12)            | infra, curation (sources_registry), orchestrator               | algorithms, params, engine, metrics, calibration, insights                   | 90 days (M12 refresh) | external_facts         | **Yes**  | **Strict** — Option B (sanitizer-as-tool / synthesizer-as-judge)            |
| 5     | `curation/` — sources_registry.yaml + agentic refresh server (M13)      | infra, baselines (data shape), orchestrator                    | algorithms, params, engine, metrics, calibration, insights                   | 90 days               | external_facts         | **Yes**  | **Strict** — Option D (constitutional critique-revise)                      |
| 6     | `engine/` — deterministic core + agentic scenario compiler (M15)        | infra, substrate, algorithms, params, baselines (read), orchestrator | metrics, calibration, insights, interview                                | code-change           | engine                 | **Yes** (compiler only) | **Soft** — Option C (evaluator-optimizer, N=2)                              |
| 7     | `metrics/` — SQL templates over the event substrate                     | infra, substrate (reader)                                      | algorithms, engine, baselines, params, calibration, insights, interview      | code-change           | metrics                | No       | —                                                                           |
| 8     | `insights/` — deterministic detectors + agentic narrator (M16)          | infra, metrics (runtime), substrate (reader), orchestrator     | algorithms, engine, baselines, params, calibration, interview                | code-change           | insights               | **Yes** (narrator only) | **Soft** — Option A (hook-only gate)                                        |
| 9     | `calibration/` — six inequality tests + Phase-2 lock                    | infra, metrics, baselines (read), engine (run scenarios)       | algorithms internals, params internals, insights, interview                  | code-change           | calibration            | No       | —                                                                           |
| 10    | `interview/` — rubric, question_gen, evaluator                          | infra, metrics, baselines, params, calibration (lock)          | algorithms internals, engine internals, insights internals                   | code-change           | interview              | No       | — (LLM-driven, but Calibration-gated, not in deliberation loop)             |

## Notes

- **"May NOT Import From"** lists are deliberately conservative.  When in
  doubt, import less.  Cross-layer reads happen via the read API of the
  upstream layer (e.g. `metrics.runtime.MetricRuntime`), never by reaching
  into another layer's internals.
- **Agentic layers** (4, 5, 6 compiler, 8 narrator) all share the same
  deliberation contract pattern (see `docs/AGENT_DELIBERATION.md`, M1).  The
  mode column tells you which of the four options the layer uses.
- **Layer 6 split**: the deterministic engine (`engine/scenario.py`,
  `engine/perturbations.py`, `engine/tick.py`, `engine/simulator.py`) is
  agent-free.  The scenario compiler (`engine/server.py`, M15) is agentic.
  Calibration tests run against the deterministic engine; agentic compiler
  output must reduce to deterministic perturbation manifests.
- **Layer 8 split**: detectors (`insights/deterministic.py`) are pure
  functions and emit numeric anomalies.  The narrator (`insights/server.py`,
  M16) only narrates detector output — it never invents numbers.
- **Refresh cadence** for Layer 4/5 is 90 days for routine baseline data;
  ad-hoc refreshes happen via the agentic server when, e.g., Meta releases
  Q-end earnings.

## Single-writer summary

The canonical writer for each agentic layer's primary artifact:

| Artifact                       | Sole writer                              |
|--------------------------------|------------------------------------------|
| `baselines/data/*.yaml`        | `baselines.tools.synthesize_baseline`    |
| `curation/sources_registry.yaml` | `curation.tools.synthesize_diff`        |
| `scenarios` table (db)         | `engine.tools.synthesize_scenario`       |
| `insights` table (db)          | `insights.tools.synthesize_insight`      |

The PreToolUse hook in `orchestrator/hooks.py` enforces this by denying any
other tool's attempt to write to these targets.
