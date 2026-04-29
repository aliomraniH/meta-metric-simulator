# Layer 7 — Metrics DSL

Metrics are SQL templates run on demand against the M2 event substrate.
The simulator does not compute these — they are queries.  Adding a new
metric requires zero changes to `algorithms/`, `engine/`, `params/`,
`baselines/`, or any layer below.  Drop a `.sql` file in
`metrics/definitions/{leading,lagging,composed}/<name>.sql` and it is
available via `MetricRuntime.compute(metric_name, scenario_id)`.

This is the architectural claim M9 establishes; M10 (composed metrics)
exercises it by joining across event types in pure SQL, and M11
(calibration) consumes metrics rather than reaching into engine
internals.

## File anatomy

A metric file is one `.sql` file with two parts: a yaml frontmatter
block and the SQL body.

### Frontmatter

The frontmatter is a yaml block at the top of the file, delimited by
SQL line comments so the file is still valid SQL syntactically.  Every
line in the block starts with `-- ` and the parser strips that prefix
before yaml-loading the body.

```sql
-- !METRIC
-- name: sends_per_reach
-- layer: leading
-- unit: ratio
-- description: |
--   Number of unique senders divided by unique impressioned viewers.
--   Mosseri-anchored; sends weighted ~3-5× likes for unconnected reach
--   per reels_metrics_comprehensive_v2.md §10.
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - pool
--   - tick_day
-- !END
SELECT ...
```

Required frontmatter fields:

| Field | Type | Notes |
|---|---|---|
| `name` | str | snake_case; must match the file's basename. |
| `layer` | enum {`leading`, `lagging`, `composed`} | Drives the directory the file lives in. |
| `unit` | str | Free-form: `ratio`, `pct`, `count`, `usd`, etc.  Used by Layer 8 narrator for formatting. |
| `description` | str | One-paragraph human description; reference-doc citations welcome. |
| `owner` | str | Team owning the metric (typically `metrics`). |
| `parameters` | list[str] | Names of additional run-time parameters (e.g. `[tick_day_start, tick_day_end]`).  Empty list when none. |
| `group_by_dimensions` | list[str] | Subset of the closed whitelist below.  At run time the user may pass any one of these as `group_by`. |

### SQL body

The SQL body sits below `-- !END`.  It MUST:

- Reference `:scenario_id` at least once.  Metrics are scenario-scoped.
- Use named parameters (`:scenario_id`, `:tick_day_start`, …) for every
  value bound at run time.  These are passed through SQLAlchemy
  `text()` parameter binding.
- Reference only the substrate tables: `events` and (rarely) `scenarios`.
  Any other table is a layer violation.

It MUST NOT:

- Use `%s` / `%(name)s` / `?` placeholders — those are positional /
  unsafe paths.
- Contain Python format-string patterns `{name}` for any name OTHER than
  `{group_by}`.  See substitution below.
- Reference any table other than `events` or `scenarios`.
- Mutate state.  `SELECT` only.  No `INSERT` / `UPDATE` / `DELETE` / `DDL`.

## Group-by substitution

`{group_by}` is the one exception to the no-string-interpolation rule.
It is the placeholder where the metric file authors a slice dimension
(`viewer_segment`, `tick_day`, etc).  The compiler validates the
dimension against a closed whitelist, quotes the identifier, and
substitutes via `str.replace`.  No user-supplied data ever flows into
the substitution; the only valid values are the seven below.

**Closed whitelist of group_by dimensions:**

- `viewer_segment`
- `viewer_geo`
- `creator_tier`
- `surface`
- `pool`
- `tick_day`
- `reel_topic_cluster`

Each metric file declares its supported subset in
`group_by_dimensions`.  At run time the user's `group_by` argument MUST
be in BOTH (a) the metric's declared list AND (b) the closed whitelist.
A metric that lists `viewer_id` would itself fail validation at compile
time.

## Run-time interface

```python
from metrics.runtime import MetricRuntime

runtime = MetricRuntime(definitions_dir="metrics/definitions/", db_session_factory=...)

# List
runtime.list_metrics(layer="leading")  # -> list[MetricDefinition]

# Compute
rows = await runtime.compute(
    "sends_per_reach",
    scenario_id="scn-abc",
    group_by="viewer_segment",
)
# -> [{"dim": "snackers", "value": 0.012}, {"dim": "deep_watchers", "value": 0.045}, ...]
```

`compute` is async because it goes through the substrate's
`AsyncSession`.  Default `group_by` falls back to the first dimension in
the metric's declared list (typically `tick_day`).  When the caller
passes a name unknown to `MetricRuntime`, a `MetricNotFound` is raised
with the available metric names enumerated in the message — that error
text is the M11 calibration author's first feedback loop.

## Auto-discovery

`MetricRuntime` walks `definitions_dir` recursively at construction.
Every `.sql` file is parsed and compiled.  A parse / compile error
raises `MetricCompilationError` immediately with the file path
referenced — fail fast at boot rather than at the first compute().

## Worked example: `sends_per_reach.sql`

```sql
-- !METRIC
-- name: sends_per_reach
-- layer: leading
-- unit: ratio
-- description: |
--   Number of unique senders divided by unique impressioned viewers.
--   Mosseri-anchored top-3 signal (reels_metrics_comprehensive_v2.md §10).
-- owner: metrics
-- parameters: []
-- group_by_dimensions:
--   - viewer_segment
--   - surface
--   - pool
--   - tick_day
-- !END
SELECT
  {group_by} AS dim,
  CAST(COUNT(DISTINCT CASE WHEN event_type = 'send' THEN viewer_id END) AS REAL) /
  NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'impression' THEN viewer_id END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY {group_by}
ORDER BY {group_by};
```

After compilation and a call to `runtime.compute("sends_per_reach", scenario_id="scn-x", group_by="viewer_segment")`,
the substituted SQL is:

```sql
SELECT
  "viewer_segment" AS dim,
  CAST(COUNT(DISTINCT CASE WHEN event_type = 'send' THEN viewer_id END) AS REAL) /
  NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'impression' THEN viewer_id END), 0) AS value
FROM events
WHERE scenario_id = :scenario_id
GROUP BY "viewer_segment"
ORDER BY "viewer_segment";
```

…with `:scenario_id` bound to `"scn-x"` via SQLAlchemy `text()`.

## What this gives you

- **Adding a metric is one file.**  No imports, no algorithm changes, no
  param updates, no engine changes.  M11 calibration tests prove this
  by reading metrics through the runtime, not by computing them
  inline.
- **Metric semantics are visible in source control.**  Reviewers see the
  exact SQL the calibration suite is anchored on.  Compare to a hidden
  Python function — much easier to audit.
- **Composed metrics are pure SQL.**  M10 layers integrity-weighted
  engagement, creator earnings concentration (Gini), and cross-segment
  substitution on top of these leading metrics by joining the same
  event substrate.  Zero engine changes.

## Layer-import discipline

`metrics/` MAY import from: `substrate/`, stdlib, sqlalchemy.

`metrics/` MAY NOT import from: `algorithms/`, `engine/`, `params/`,
`baselines/`, `calibration/`, `interview/`, `tools/`.

The M18 layer-import linter enforces this.
