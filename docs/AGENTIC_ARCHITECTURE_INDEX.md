# Agentic Architecture Index

Source: `docs/architecture_research.pdf` (April 2026 snapshot, 25 pages).
This index is the structural lookup for the agentic architecture. The PDF
is the authoritative explanation; this file is the fast-reference for
M12–M18 milestones.

## 1. Agentic vs deterministic layer map

| Layer | Owns                       | Agentic? | Mode    | Confidence floor | Severity threshold | Option                              | Rationale (PDF §)                                                                                                  |
|-------|----------------------------|----------|---------|------------------|--------------------|-------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| 0     | Infra                      | No       | n/a     | n/a              | n/a                | n/a                                 | Boilerplate; deterministic by definition. (§3 layer table)                                                          |
| 1     | Event substrate            | No       | n/a     | n/a              | n/a                | n/a                                 | Raw event log; agents have nothing to add. (§3 layer table)                                                         |
| 2     | Algorithms                 | No       | n/a     | n/a              | n/a                | n/a                                 | Pure functions; agent involvement breaks determinism. (§3 layer table; reinforced §7.5)                             |
| 3     | Parameters                 | No       | n/a     | n/a              | n/a                | n/a                                 | Inputs to algorithms; tuning is calibration's job, not an agent's. (§3.1, §7.5)                                     |
| 4     | Baselines (external facts) | Yes      | Strict  | 0.85             | high               | B — sanitize-as-tool + judge        | External facts (Meta DAP, run rate, competitor metrics) need full sanitize/synthesize ingestion. (§3.2, §9.2)       |
| 5     | Curation (sources registry) | Yes     | Strict  | 0.85             | high               | D — constitutional critique-revise  | Constitution: cite primary > secondary; never auto-apply diffs; ctx.elicit() always. (§3.3)                         |
| 6     | Engine + scenario compiler | Mixed    | Soft (sublayer only) | 0.70 | critical           | C — evaluator-optimizer (N=2)       | Engine deterministic (M7-locked); scenario synthesis sublayer is agentic with soft gate. (§3.4)                     |
| 7     | Metrics DSL                | No       | n/a     | n/a              | n/a                | n/a                                 | SQL templates; agent-written SQL is unsafe (injection, hallucinated joins, non-determinism). (§3.5, §7.9)           |
| 8     | Insights (narration)       | Yes      | Soft    | 0.60             | critical           | A — hook-only gate                  | Narrator must not invent numeric values; numbers come from `insights/deterministic.py`. PostToolUse-only. (§3.6)    |
| 9     | Calibration                | No       | n/a     | n/a              | n/a                | n/a                                 | Tests + lock; deterministic by design. The lock IS the gate. (§3 layer table; §7.7)                                  |
| 10    | Interview tools            | LLM-gated, not agentic | n/a | n/a   | n/a                | n/a                                 | Uses Sonnet 4.6 internally but no sanitize/synthesize / no observations table; gated by `@requires_calibration`. (§3.7) |

**Note on Layer 6's "Mixed" status.** The engine itself (`engine/scenario.py`,
`engine/perturbations.py`, `engine/tick.py`, `engine/simulator.py`,
`engine/state.py`) is deterministic and was locked under calibration in
M7/M11. Same `(scenario.perturbations, scenario.seed)` produces byte-
identical event sequences on the substrate ordering tuple. The agentic
piece is the **scenario-synthesis sublayer** introduced in M15: a Sonnet-
4.6 compiler translates natural-language hypotheticals (e.g., "what if
Meta launched Reels-only mode for teens") into structured perturbation
manifests via the evaluator-optimizer pattern (compiler proposes →
evaluator critiques against rubric → on score < 0.7, retry, bounded
N=2). Soft mode allows exploratory failure: on N=2 exhaustion the
synthesizer commits with `disputed=true` rather than rejecting outright.
The deterministic engine consumes whatever manifest the agentic
sublayer produces, so the calibration lock remains binding even on
agentic-authored scenarios. (PDF §3.4)
