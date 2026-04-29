"""Metric compiler — parses .sql template files into runnable CompiledMetric.

Each metric file has a yaml frontmatter block (line-comment-prefixed) and
a SQL body.  The compiler:
  - splits the two halves on the !METRIC / !END markers,
  - yaml-loads the frontmatter into a MetricDefinition,
  - validates the SQL body (parameterised, scenario-scoped, references
    only the substrate tables, declares group_by_dimensions in the
    closed whitelist),
  - returns a CompiledMetric whose .run() does the value-bound execute
    plus whitelisted identifier substitution for `{group_by}`.

See docs/METRICS_DSL.md for the file-format spec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# -----------------------------------------------------------------------------
# Allowed sets
# -----------------------------------------------------------------------------

# Substrate tables the metric SQL may reference.  Anything else is a layer
# violation and the compiler rejects it.
ALLOWED_TABLES: frozenset[str] = frozenset({"events", "scenarios"})

# Closed whitelist of group_by dimensions.  Every metric file's
# group_by_dimensions list MUST be a subset of this.  At run time the
# user's group_by arg MUST also be in this set.
ALLOWED_GROUP_BY: frozenset[str] = frozenset({
    "viewer_segment",
    "viewer_geo",
    "creator_tier",
    "surface",
    "pool",
    "tick_day",
    "reel_topic_cluster",
})

ALLOWED_LAYERS: frozenset[str] = frozenset({"leading", "lagging", "composed"})

# Frontmatter delimiters (line-comment style so the file remains valid SQL).
_START = "-- !METRIC"
_END = "-- !END"


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class MetricCompilationError(Exception):
    """Raised when a metric file's SQL body fails compile-time validation."""


class MetricValidationError(Exception):
    """Raised on frontmatter or run-time argument validation failures."""


class MetricNotFound(KeyError):
    """Raised by MetricRuntime.compute when an unknown metric name is requested."""


# -----------------------------------------------------------------------------
# Dataclasses
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricDefinition:
    name: str
    layer: str
    unit: str
    description: str
    owner: str
    parameters: tuple[str, ...]
    group_by_dimensions: tuple[str, ...]
    sql_body: str
    source_path: str


@dataclass
class CompiledMetric:
    definition: MetricDefinition

    async def run(
        self,
        session: AsyncSession,
        scenario_id: str,
        group_by: str | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Execute this metric and return rows as plain dicts."""
        dim = self._resolve_group_by(group_by)
        sql_body = _substitute_group_by(self.definition.sql_body, dim)

        bind_params: dict[str, Any] = {"scenario_id": scenario_id}
        for k in self.definition.parameters:
            if k in params:
                bind_params[k] = params[k]

        # Reject any extra params the metric did not declare — surfaces
        # typos at the compute() boundary rather than silently ignoring.
        extras = set(params) - set(self.definition.parameters)
        if extras:
            raise MetricValidationError(
                f"metric {self.definition.name!r} does not accept parameters: "
                f"{sorted(extras)}; declared parameters: "
                f"{list(self.definition.parameters)}"
            )

        result = await session.execute(text(sql_body), bind_params)
        return [dict(row._mapping) for row in result]

    def _resolve_group_by(self, requested: str | None) -> str:
        allowed = self.definition.group_by_dimensions
        if requested is None:
            # Default: prefer tick_day; else first declared dimension.
            if "tick_day" in allowed:
                return "tick_day"
            if allowed:
                return allowed[0]
            raise MetricValidationError(
                f"metric {self.definition.name!r} declares no group_by_dimensions"
            )
        if requested not in ALLOWED_GROUP_BY:
            raise MetricValidationError(
                f"group_by={requested!r} not in closed whitelist: "
                f"{sorted(ALLOWED_GROUP_BY)}"
            )
        if requested not in allowed:
            raise MetricValidationError(
                f"metric {self.definition.name!r} does not support "
                f"group_by={requested!r}; allowed: {list(allowed)}"
            )
        return requested


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------

def parse_metric_file(path: str | Path) -> MetricDefinition:
    """Read a metric .sql file, split frontmatter and body, validate frontmatter."""
    p = Path(path)
    text_blob = p.read_text()

    front, body = _split_frontmatter(text_blob, source=str(p))
    fm = yaml.safe_load(front) or {}
    if not isinstance(fm, dict):
        raise MetricValidationError(
            f"{p}: frontmatter must be a yaml mapping, got {type(fm).__name__}"
        )

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        raise MetricValidationError(f"{p}: frontmatter missing/invalid `name`")
    if p.stem != name:
        raise MetricValidationError(
            f"{p}: frontmatter name {name!r} does not match filename stem {p.stem!r}"
        )

    layer = fm.get("layer")
    if layer not in ALLOWED_LAYERS:
        raise MetricValidationError(
            f"{p}: layer={layer!r} not in {sorted(ALLOWED_LAYERS)}"
        )

    unit = fm.get("unit")
    if not isinstance(unit, str) or not unit:
        raise MetricValidationError(f"{p}: frontmatter missing/invalid `unit`")

    description = fm.get("description") or ""
    if not isinstance(description, str):
        raise MetricValidationError(f"{p}: frontmatter `description` must be a string")

    owner = fm.get("owner") or "metrics"
    if not isinstance(owner, str):
        raise MetricValidationError(f"{p}: frontmatter `owner` must be a string")

    raw_params = fm.get("parameters", [])
    if not isinstance(raw_params, list) or not all(isinstance(x, str) for x in raw_params):
        raise MetricValidationError(
            f"{p}: frontmatter `parameters` must be a list of strings"
        )

    raw_dims = fm.get("group_by_dimensions", [])
    if not isinstance(raw_dims, list) or not all(isinstance(x, str) for x in raw_dims):
        raise MetricValidationError(
            f"{p}: frontmatter `group_by_dimensions` must be a list of strings"
        )
    bad = [d for d in raw_dims if d not in ALLOWED_GROUP_BY]
    if bad:
        raise MetricValidationError(
            f"{p}: group_by_dimensions {bad} not in closed whitelist "
            f"{sorted(ALLOWED_GROUP_BY)}"
        )

    return MetricDefinition(
        name=name,
        layer=layer,
        unit=unit,
        description=description.strip(),
        owner=owner,
        parameters=tuple(raw_params),
        group_by_dimensions=tuple(raw_dims),
        sql_body=body.strip(),
        source_path=str(p),
    )


def _split_frontmatter(blob: str, *, source: str) -> tuple[str, str]:
    """Extract frontmatter yaml + SQL body.  Returns (yaml_text, sql_text)."""
    lines = blob.splitlines()
    if not any(line.strip() == _START for line in lines):
        raise MetricCompilationError(
            f"{source}: missing `{_START}` frontmatter start marker"
        )
    if not any(line.strip() == _END for line in lines):
        raise MetricCompilationError(
            f"{source}: missing `{_END}` frontmatter end marker"
        )

    in_fm = False
    fm_lines: list[str] = []
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == _START:
            in_fm = True
            continue
        if stripped == _END:
            in_fm = False
            continue
        if in_fm:
            # Strip the leading "-- " (or "--") that prefixes every fm line.
            if line.startswith("-- "):
                fm_lines.append(line[3:])
            elif line.startswith("--"):
                fm_lines.append(line[2:].lstrip())
            else:
                # Bare yaml lines (rare) are passed through.
                fm_lines.append(line)
        else:
            body_lines.append(line)
    return "\n".join(fm_lines), "\n".join(body_lines)


# -----------------------------------------------------------------------------
# Compile-time validation
# -----------------------------------------------------------------------------

# Match `FROM <table>` and `JOIN <table>` — case-insensitive, single-word table.
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

# Forbidden positional / format placeholders.
_FORBIDDEN_PERCENT = re.compile(r"%[sd]|%\([^)]+\)[sd]")

# Curly-brace placeholders other than {group_by}.
_CURLY_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Mutating statements we reject outright.
_FORBIDDEN_DDL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

# SQL comment strippers — applied before any of the content-scanning rules
# so prose in `-- this metric joins X to Y ...` doesn't trip the table /
# DDL / placeholder regexes.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    """Remove `-- ...` line comments and `/* ... */` block comments.

    Used by the validation regexes so author commentary inside the SQL
    body cannot accidentally match table / DDL / placeholder patterns.
    Returns SQL with comments replaced by single spaces (so adjacent
    tokens that wrapped a comment stay separated).
    """
    no_block = _BLOCK_COMMENT_RE.sub(" ", sql)
    return _LINE_COMMENT_RE.sub(" ", no_block)


def compile_metric(definition: MetricDefinition) -> CompiledMetric:
    """Run compile-time validation on the SQL body and wrap it for execution."""
    body = definition.sql_body
    src = definition.source_path

    # 1. :scenario_id presence (metric must be scenario-scoped).
    if ":scenario_id" not in body:
        raise MetricValidationError(
            f"{src}: SQL body must reference :scenario_id (metrics are scenario-scoped)"
        )

    # Strip SQL comments before applying the content-scanning rules below
    # so prose in `-- this metric does X` doesn't trip table/DDL/placeholder
    # regexes.  The :scenario_id check above intentionally runs against the
    # raw body — having :scenario_id only inside a comment should still fail.
    code_only = _strip_sql_comments(body)

    # 2. No string-format placeholders besides {group_by}.
    if _FORBIDDEN_PERCENT.search(code_only):
        raise MetricCompilationError(
            f"{src}: SQL body contains forbidden %s/%(name)s placeholders. "
            "Use named :params via SQLAlchemy text() bindings."
        )
    bad_curly = [m for m in _CURLY_PLACEHOLDER.findall(code_only) if m != "group_by"]
    if bad_curly:
        raise MetricCompilationError(
            f"{src}: SQL body contains forbidden curly placeholders: {bad_curly}. "
            "Only {group_by} is allowed (whitelisted identifier substitution); "
            "all other values must be bound as :params."
        )

    # 3. No mutating statements.
    if _FORBIDDEN_DDL.search(code_only):
        raise MetricCompilationError(
            f"{src}: SQL body contains forbidden DDL/DML keywords. "
            "Metrics are SELECT-only."
        )

    # 4. Tables must be in the substrate set.
    referenced = {t.lower() for t in _TABLE_RE.findall(code_only)}
    bad_tables = sorted(referenced - ALLOWED_TABLES)
    if bad_tables:
        raise MetricCompilationError(
            f"{src}: SQL references non-substrate tables {bad_tables}. "
            f"Allowed: {sorted(ALLOWED_TABLES)}"
        )

    # 5. group_by_dimensions already validated against ALLOWED_GROUP_BY in
    #    parse_metric_file.  No additional check here.

    return CompiledMetric(definition=definition)


# -----------------------------------------------------------------------------
# Group-by substitution (the one whitelisted identifier path)
# -----------------------------------------------------------------------------

def _substitute_group_by(sql_body: str, dimension: str) -> str:
    """Replace `{group_by}` with a double-quoted SQL identifier.

    `dimension` MUST already be in ALLOWED_GROUP_BY — the resolver in
    CompiledMetric.run validates this.  We assert here as a defence in
    depth so a misbehaving caller cannot inject arbitrary text.
    """
    if dimension not in ALLOWED_GROUP_BY:
        raise MetricValidationError(
            f"refusing to substitute group_by={dimension!r}: not in whitelist"
        )
    quoted = f'"{dimension}"'
    return sql_body.replace("{group_by}", quoted)
