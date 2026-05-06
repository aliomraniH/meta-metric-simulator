# Build Log — M0 → M16 (session wrap, branch `claude/hybrid-agent-mcp-build-fA4Ov`)

This file is the single point-in-time snapshot of what was built across
this session.  It is not a rolling design doc — for that, see
`docs/ARCHITECTURE.md`, `docs/AGENTIC_ARCHITECTURE_INDEX.md`, and the
per-layer contracts in `docs/LAYER_CONTRACTS.md`.  This log documents
**what milestones landed, in what order, and the verifiable state at
session end** so a successor can pick up at M17 without re-deriving
context.

## Headline numbers (HEAD = `16d2b9a`)

  * **Tests:** 442 passed, 1 skipped (Voyage roundtrip behind
    `VOYAGE_API_KEY` + `RUN_INTEGRATION_TESTS` gate).
  * **Calibration lock:** valid — `verify_lock_against_current_state()`
    returns `(True, [])`.
  * **Provenance audit:** PASS — `files_scanned=18`,
    `values_scanned=279`, `errors=0`, `warnings=161`.
  * **Calibration runner:** PASS — 6/6 tests at
    `git_head=51469488…` lock-time.
  * **Front-door tools:** 18 across 4 namespaces (l4=5, l5=5, l6=5, l8=3).

## Milestone ledger

Each entry: commit hash → milestone → headline outcome.

### Documentation prelude (pre-M12)

  * `73a50d3` — chore: move architecture_research.pdf into docs/
  * `1187466` — chore: ignore docs/architecture_research.txt (pdftotext derivative)
  * `9df97e0` — `AGENTIC_ARCHITECTURE_INDEX.md` §1 layer map
  * `e46da22` — §3 FastMCP composition rule (mount, never chained Client)
  * `9ea064c` — §4 Replit deployment constraints
  * `ed5a0cf` — §5+§6 anti-patterns + sampling channel preservation
  * `662391a` — §7+§8 code skeletons + binding rules
  * `7c7a2b8` — §2 sanitize/synthesize gate policy

### M12 — Layer 4 (baselines, strict Option B)

  * `1343e71` — **M12a**: baselines schemas + sanitize_schema tool + FastMCP scaffold.
    Pydantic v2 `ExtractedMetric`, `SanitizerFlag`, `SynthesizerDecision`
    with closed enums; Layer-import discipline established.
  * `57ed45b` — **M12b**: sanitize_policy + sanitize_source_tier + tests.
    Module-level singleton load of `curation/sources_registry.yaml`,
    `HIGH_STAKES_METRIC_IDS` frozenset.  Competitor-asymmetry guard wired.
  * `2c7ce57` — **M12c-i**: extract_metric tool, two-call pattern
    (Citations API → strict tool use) per PDF §7.3.  Sonnet 4.6,
    ttl=1h cache.
  * `e987030` — **M12c-ii**: synthesize_baseline (SOLE WRITER) +
    layer-isolation PreToolUse hook + `@requires_calibration_unlocked`
    decorator.  Opus 4.7 + thinking + atomic `.tmp`+`os.rename` write.
  * `4eaa395` — **M12c-iii**: mount baselines into `infra/server.py` at
    namespace `l4`, layer-isolation hook wired into `hooks_for_layer()`,
    strict-mode integration test (sampling channel + sole-writer canary).

### M13 — Layer 5 (curation, strict Option D)

  * `11fa93c` — **M13a**: curation schemas (`DiffProposal`,
    `ConstitutionFlag`, `CurationDecision`) + tools (web_search with
    `allowed_domains`, web_fetch with citations, diff_proposal two-call
    pattern, sanitize_constitution deterministic 5-rule check).
    Reuses `HIGH_STAKES_METRIC_IDS` from baselines.
  * `a5b2577` — **M13b**: synthesize_diff (SOLE WRITER to
    `curation/sources_registry.yaml`) + mount at l5 + strict-mode
    integration tests.  Critique-revise loop with N=2; never auto-applies
    (always `ctx.elicit` for diff-confirm); calibration-impact escalation
    via separate elicit.

### M14 — Layer 8 deterministic detectors (agent-free)

  * `6af53ca` — **M14**: `insights/deterministic.py` (`z_score_anomalies`
    rolling look-back, `percent_diff` symmetric, `metric_rank` stable
    via `scipy.stats.rankdata` method=min).  `insights/voyage_client.py`
    async wrapper for voyage-3-large + rerank-2.5 with L2 normalisation.
    `docs/INSIGHTS_ROADMAP.md` documenting event-sequence vs aggregate
    embeddings, BOCPD as M14.5+ successor.

### M15 — Layer 6 scenario synthesis sublayer (soft Option C)

  * `cf34630` — **M15a**: `engine/schemas_agentic.py` (`Perturbation`,
    `AudienceFilter`, `CompiledScenario`, `EvaluatorVerdict`).
    `engine/prompts/scenario_compiler.md` with 5 cached few-shot examples
    (PDF §6.2: high-quality examples > elaborate instructions).  Tools:
    `compile_scenario`, `get_audience_definition` (closed registry of 7
    cohorts), `lookup_seasonality` (4 named periods).
  * `a93c3eb` — **M15b**: `evaluate_scenario` (Sonnet 4.6 critic,
    four-axis rubric, accept_threshold=0.7).  Plain-async
    `critique_revise_loop` helper (NOT an `@mcp.tool`) with N=2
    bound, feedback wiring, stricter-of-thresholds gate.
  * `ea90def` — **M15c**: synthesize_scenario (SOLE WRITER to
    `scenarios` table) + mount at l6 + soft-mode integration tests.
    Soft-mode contract: **writes regardless of pass/fail**, marking
    `disputed=true` on N=2 exhaustion.  No `ctx.elicit`.  Op→engine-type
    translator (`set/multiply/add/toggle`→`step`, `ramp`→`ramp`,
    `spike`→`spike`) keeps the agentic↔deterministic bridge intact.
    `infra/db.py` `scenarios` table extended with five nullable columns
    (natural_language_intent, time_horizon, audience_filters,
    iterations_used, evaluator_verdict_json).

### M16 — Layer 8 narrator (soft Option A hook-only)

  * `16d2b9a` — **M16**: `insights/schemas.py` (`Hypothesis`,
    `Narrative`, `NarratorFlag`).  `insights/prompts/narrator.md`
    instructing "MUST NOT invent numbers".  Tools:
    `find_similar_scenarios` (two-stage Voyage retrieval),
    `narrate_anomalies` (Sonnet 4.6, empty-anomaly short-circuit),
    `synthesize_insight` (SOLE WRITER to `insights` table).
    Three deterministic narrator-flag checks: `invented_number` (high),
    `missing_anomaly_ref` (high), `weak_hypothesis` (medium).
    Mount at l8 replaces the placeholder.  Architectural canary
    `test_narrator_invention_caught` enforces "narrator MUST NOT
    invent numbers" mechanically — invented `156%` triggers
    `disputed=true` + `kind="invented_number"` flag.

## Layer status at session end

| Layer | Mode               | Writer                  | Confidence floor | Status     |
|-------|--------------------|-------------------------|------------------|------------|
| L1    | deterministic      | substrate writer        | n/a              | locked     |
| L2    | deterministic      | substrate writer        | n/a              | locked     |
| L3    | deterministic      | algorithms              | n/a              | locked     |
| L4    | agentic strict (B) | synthesize_baseline     | 0.85             | OPERATIONAL|
| L5    | agentic strict (D) | synthesize_diff         | 0.85             | OPERATIONAL|
| L6    | mixed              | engine + synthesize_scenario | 0.70 (soft) | OPERATIONAL|
| L7    | deterministic      | metrics runtime         | n/a              | locked     |
| L8    | mixed              | deterministic + synthesize_insight | 0.60 (soft) | OPERATIONAL|
| L9    | deterministic      | calibration runner      | n/a              | locked     |
| L10   | LLM-gated          | (M17 — interview)       | n/a (per-rubric) | unblocked  |

## Hard architectural rules now enforced in code

  1. **Sole-writer per layer.**  Synthesize_* tools are the only paths
     that touch their canonical artefact.  Verified by integration
     tests `test_only_synthesize_*_writes` in each layer.
  2. **Sanitize/synthesize gate.**  Sanitizers are `readOnlyHint=True`;
     synthesizers are not.  Verified by tool-list assertions.
  3. **Calibration lock guard.**  Every synthesize_* is wrapped by
     `@requires_calibration_unlocked` (M12c-ii decorator).  Drift →
     `CalibrationLockError` raised BEFORE any model call.
  4. **Layer isolation hook.**  PreToolUse hook denies any synthesize_*
     write to `params/`, `algorithms/`, `engine/`, `metrics/`,
     `calibration/` regardless of the tool's intent.  Belt-and-braces
     with the per-tool target_file checks.
  5. **Citations + Structured Outputs cannot combine** (PDF §7.3).
     Two-call pattern in extract_metric (M12c-i), diff_proposal
     (M13a), and the implicit two-call pattern of compile→evaluate
     (M15a/b).  Pinned by `test_extractor_two_call_pattern` and
     `test_two_call_pattern_in_diff_proposal`.
  6. **`cache_control.ttl="1h"` explicit on every cached system prompt**
     (PDF §6.1).  Pinned by `test_*_caches_system_prompt_at_one_hour`
     in every agentic tool.
  7. **Sonnet for workers, Opus only for orchestrator + final judge**
     (PDF §7.4).  Verified by `test_*_uses_sonnet` /
     `test_*_uses_opus` per tool.
  8. **Narrator MUST NOT invent numbers** (PDF §3.6).  Mechanically
     enforced by the deterministic invented-number check inside
     `synthesize_insight` — pinned by
     `test_narrator_invention_caught`.
  9. **Soft-mode writes regardless of flags.**  `disputed=true` is the
     user-visible signal; the row still lands.  Pinned by
     `test_synthesizer_writes_regardless_of_flags` (L8) and
     `test_n2_exhaustion_writes_disputed_true` (L6).
  10. **NEVER auto-apply curation diffs** (constitution Rule 3 in
      `curation/prompts/refresher.md`).  Every accept path goes through
      `ctx.elicit` for diff-confirm.  Pinned by
      `test_synthesizer_does_not_auto_apply` and
      `test_synthesizer_elicit_required` in M13b.

## Front-door tool surface (verified at session end)

```
l4_extract_metric             readOnly
l4_sanitize_schema            readOnly
l4_sanitize_policy            readOnly
l4_sanitize_source_tier       readOnly
l4_synthesize_baseline        WRITER  (baselines/data/*.yaml)

l5_web_search                 readOnly
l5_web_fetch                  readOnly
l5_diff_proposal              readOnly
l5_sanitize_constitution      readOnly
l5_synthesize_diff            WRITER  (curation/sources_registry.yaml)

l6_get_audience_definition    readOnly
l6_lookup_seasonality         readOnly
l6_compile_scenario           readOnly
l6_evaluate_scenario          readOnly
l6_synthesize_scenario        WRITER  (scenarios table)

l8_find_similar_scenarios     readOnly
l8_narrate_anomalies          readOnly
l8_synthesize_insight         WRITER  (insights table)
```

## Key files added/modified per layer (skim index)

  * **baselines/**: `schemas.py`, `server.py`, `prompts/{extractor,reconciler}.md`,
    `tools/{sanitize_schema,sanitize_policy,sanitize_source_tier,extract_metric,synthesize_baseline}.py`
  * **curation/**: `schemas.py`, `server.py`,
    `prompts/{refresher,critic}.md`,
    `tools/{web_search,web_fetch,diff_proposal,sanitize_constitution,synthesize_diff}.py`
  * **engine/**: `schemas_agentic.py`, `server.py`,
    `prompts/{scenario_compiler,scenario_evaluator}.md`,
    `tools/{get_audience_definition,lookup_seasonality,compile_scenario,evaluate_scenario,critique_revise_loop,synthesize_scenario}.py`.
    M7 deterministic engine (scenario.py, simulator.py, tick.py,
    state.py, perturbations.py) **unchanged**.
  * **insights/**: `schemas.py`, `server.py`, `voyage_client.py`,
    `deterministic.py`, `prompts/narrator.md`,
    `tools/{find_similar_scenarios,narrate_anomalies,synthesize_insight}.py`
  * **infra/**: `db.py` extended with `insights` table and 5 nullable
    `scenarios` columns; `server.py` mounts l4/l5/l6/l8 with the
    layer-isolation hook wired via `hooks_for_layer()`.
  * **calibration/**: `lock.py` extended with `CalibrationLockError`,
    `requires_calibration_unlocked` async decorator,
    `would_change_locked_hash` predicate.
  * **orchestrator/**: `hooks.py` extended with `make_layer_isolation_hook`
    (M12c-ii) and `_DETERMINISTIC_PATH_PREFIXES`.
  * **docs/**: `AGENTIC_ARCHITECTURE_INDEX.md` (8 sections),
    `INSIGHTS_ROADMAP.md` (5 sections), this `BUILD_LOG.md`.

## Test breakdown (442 passed)

```
tests/algorithms/                  ~92  (locked since M4)
tests/baselines/                    44  (M12a/b/c)
tests/calibration/                  15  (M11.5)
tests/curation/                     39  (M13a/b + M0/M11 audit)
tests/engine/                       65  (M7 + M15a/b/c)
tests/insights/                     34  (M14 + M16)
tests/integration/                  31  (M12c-iii + M13b + M14 + M15c + M16)
tests/metrics/                      24  (M9/M10 + M11.5d)
tests/orchestrator/                 20  (M1 + M12c-ii layer-isolation)
tests/substrate/                     8  (M2)
voyage roundtrip                     1 SKIPPED (env-gated)
```

## What is NOT done

  * **M17** — Layer 10 interview tools (LLM-gated calibration-locked
    question generators + evaluators per Meta AT framework
    GAME / L1→L4 / DEC / RICE+Reevaluate).  Rubric weights from yaml
    at runtime, never hardcoded.  Calibration-locked: every tool call
    asserts lock valid before doing work.  This is the next message.
  * **M18** — final acceptance canary suite (sampling channel canary
    against the production front-door auth path; full multi-layer
    end-to-end Reels-only-mode-for-teens scenario from natural-language
    prompt → compiled scenario → engine run → metrics → anomalies →
    narrative).
  * Voyage real-API roundtrip — the `tests/integration/test_voyage_roundtrip.py`
    test is wired but skipped without `VOYAGE_API_KEY` +
    `RUN_INTEGRATION_TESTS` env vars.  Run in CI integration job once
    the secret is provisioned.
  * **fastmcp `exclude_args` deprecation** — two of the M16 tools
    (`find_similar_scenarios` and `narrate_anomalies`) use the
    deprecated `exclude_args` parameter to hide non-LLM-friendly
    typed kwargs (`voyage_client`, `metric_runtime`,
    `metric_runtime_factory`) from the FastMCP schema generator.
    The migration is to `Depends()` per the FastMCP 2.14+ API.
    Tracked but not load-bearing for M17.

## How to verify the build state from a fresh clone

```bash
git checkout claude/hybrid-agent-mcp-build-fA4Ov
pip install -e .[dev]
pip install aiofiles redis
python -c "from calibration.lock import verify_lock_against_current_state; print(verify_lock_against_current_state())"
python -m curation.provenance_audit
python -m calibration.runner
pytest tests/ -v
```

Expected results:

  * Lock check → `(True, [])`
  * Provenance audit → `=== Provenance audit: PASS ===`,
    `errors=0`, `warnings=161`
  * Calibration runner → `=== Calibration: PASS (6/6 tests) ===`
  * pytest → `442 passed, 1 skipped`
