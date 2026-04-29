"""MetricRuntime — auto-discovery + execution facade for metrics.

Walks `metrics/definitions/` recursively at construction, parses every
.sql file, compiles each, caches by name.  compute() looks up the
cached CompiledMetric and runs it via the supplied AsyncSession factory.

Auto-discovery is fail-fast: a parse or compile error surfaces at boot
rather than at the first compute() call, so calibration authoring (M11)
gets the error immediately.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from metrics.compiler import (
    CompiledMetric,
    MetricDefinition,
    MetricNotFound,
    compile_metric,
    parse_metric_file,
)

log = logging.getLogger(__name__)


class MetricRuntime:
    """Cached registry of compiled metrics."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        definitions_dir: str | Path = "metrics/definitions",
    ) -> None:
        self._session_factory = db_session_factory
        self._definitions_dir = Path(definitions_dir)
        self._compiled: dict[str, CompiledMetric] = {}
        self.stats: dict[str, Any] = {"by_layer": Counter(), "files_loaded": 0}
        self._discover()

    # -- discovery ---------------------------------------------------------

    def _discover(self) -> None:
        if not self._definitions_dir.exists():
            log.warning("metrics definitions dir not found: %s", self._definitions_dir)
            return
        for sql_path in sorted(self._definitions_dir.rglob("*.sql")):
            definition = parse_metric_file(sql_path)
            compiled = compile_metric(definition)
            if definition.name in self._compiled:
                raise ValueError(
                    f"duplicate metric name {definition.name!r}: "
                    f"already loaded from "
                    f"{self._compiled[definition.name].definition.source_path}, "
                    f"now found in {sql_path}"
                )
            self._compiled[definition.name] = compiled
            self.stats["by_layer"][definition.layer] += 1
            self.stats["files_loaded"] += 1
        log.info(
            "metrics discovered: %d files, layers=%s",
            self.stats["files_loaded"],
            dict(self.stats["by_layer"]),
        )

    # -- introspection -----------------------------------------------------

    def list_metrics(self, layer: str | None = None) -> list[MetricDefinition]:
        defs = [c.definition for c in self._compiled.values()]
        if layer is not None:
            defs = [d for d in defs if d.layer == layer]
        return sorted(defs, key=lambda d: (d.layer, d.name))

    def get_metric_definition(self, name: str) -> MetricDefinition:
        compiled = self._compiled.get(name)
        if compiled is None:
            raise MetricNotFound(self._not_found_message(name))
        return compiled.definition

    # -- execution ---------------------------------------------------------

    async def compute(
        self,
        metric_name: str,
        scenario_id: str,
        group_by: str | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Run the named metric and return rows as plain dicts."""
        compiled = self._compiled.get(metric_name)
        if compiled is None:
            raise MetricNotFound(self._not_found_message(metric_name))

        async with self._session_factory() as session:
            return await compiled.run(
                session,
                scenario_id=scenario_id,
                group_by=group_by,
                **params,
            )

    # -- helpers -----------------------------------------------------------

    def _not_found_message(self, name: str) -> str:
        avail = ", ".join(sorted(self._compiled.keys())) or "(none discovered)"
        return (
            f"metric {name!r} not found. Available metrics: [{avail}]. "
            f"Define a new one by dropping a .sql file under "
            f"{self._definitions_dir}/<layer>/<name>.sql."
        )
