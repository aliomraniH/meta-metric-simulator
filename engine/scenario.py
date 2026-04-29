"""Scenario dataclass — the unit of simulation input.

A Scenario specifies the perturbation manifest, horizon, and seed for one
deterministic run.  The dataclass validates its perturbation defs at
construction time using the same shape that algorithms/perturbations.py
(M4c) accepts; the engine never re-implements ramp/step/spike logic.

Persistence
-----------
save() inserts the scenario into the `scenarios` table defined in
infra/db.py (M0) — the same row the M2 events table FKs against.
load(scenario_id) reads back via the symmetric SELECT.

Both methods are async because the underlying SQLAlchemy session is async
(matches infra/db.py's AsyncSession).  The Simulator wraps these calls.

Validation
----------
- horizon_days ∈ [1, 365]
- seed must be int
- perturbations may be empty
- each perturbation_def must match one of the three supported shapes
- target dotted_paths are accepted as-is (algorithms/perturbations.apply
  raises KeyError at run time if the path doesn't resolve)

start_day extension
-------------------
The reference doc and algorithms/perturbations.py operate on a `tick_day`
that is RELATIVE to the perturbation's start.  The Scenario dataclass
accepts an optional `start_day` field on every perturbation_def
(default 0) so a scenario can stage perturbations across the horizon.
The engine offsets tick_day - start_day before calling
algorithms/perturbations.apply.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import ulid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db import scenarios as scenarios_table


# Supported perturbation shapes — kept here as a single source of truth
# for both validation and the engine wrapper in engine/perturbations.py.
PERTURBATION_TYPES: frozenset[str] = frozenset({"ramp", "step", "spike"})

# Required fields per perturbation type (excluding `type` and `target`,
# which are required for all).
_REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "ramp":  ("from", "to", "days"),
    "step":  ("value",),
    "spike": ("to", "duration_days"),
}

MIN_HORIZON_DAYS = 1
MAX_HORIZON_DAYS = 365


class ScenarioValidationError(ValueError):
    """Raised when a Scenario or one of its perturbation defs is malformed."""


@dataclass
class Scenario:
    """One simulation run's specification."""

    name: str
    horizon_days: int
    seed: int
    perturbations: list[dict[str, Any]] = field(default_factory=list)
    description: str | None = None
    scenario_id: str = field(default_factory=lambda: str(ulid.new()))
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.horizon_days, int):
            raise ScenarioValidationError(
                f"horizon_days must be int, got {type(self.horizon_days).__name__}"
            )
        if not (MIN_HORIZON_DAYS <= self.horizon_days <= MAX_HORIZON_DAYS):
            raise ScenarioValidationError(
                f"horizon_days={self.horizon_days} outside "
                f"[{MIN_HORIZON_DAYS}, {MAX_HORIZON_DAYS}]"
            )
        if not isinstance(self.seed, int):
            raise ScenarioValidationError(
                f"seed must be int, got {type(self.seed).__name__}"
            )
        if not isinstance(self.perturbations, list):
            raise ScenarioValidationError(
                f"perturbations must be a list, got {type(self.perturbations).__name__}"
            )
        for i, p in enumerate(self.perturbations):
            self._validate_perturbation(i, p)

    @staticmethod
    def _validate_perturbation(idx: int, p: Mapping[str, Any]) -> None:
        if not isinstance(p, Mapping):
            raise ScenarioValidationError(
                f"perturbation[{idx}] must be a mapping, got {type(p).__name__}"
            )
        ptype = p.get("type")
        if ptype not in PERTURBATION_TYPES:
            raise ScenarioValidationError(
                f"perturbation[{idx}].type={ptype!r} not in {sorted(PERTURBATION_TYPES)}"
            )
        target = p.get("target")
        if not isinstance(target, str) or not target:
            raise ScenarioValidationError(
                f"perturbation[{idx}].target must be a non-empty dotted_path string"
            )
        for fld in _REQUIRED_BY_TYPE[ptype]:
            if fld not in p:
                raise ScenarioValidationError(
                    f"perturbation[{idx}] type={ptype!r} missing required field {fld!r}"
                )
        # start_day is optional; if present, must be a non-negative int.
        sd = p.get("start_day", 0)
        if not isinstance(sd, int) or sd < 0:
            raise ScenarioValidationError(
                f"perturbation[{idx}].start_day must be a non-negative int, got {sd!r}"
            )

    # -- persistence -------------------------------------------------------

    async def save(self, session: AsyncSession) -> str:
        """Insert this scenario into the `scenarios` table.  Returns scenario_id."""
        await session.execute(
            scenarios_table.insert(),
            [{
                "scenario_id":   self.scenario_id,
                "name":          self.name,
                "description":   self.description,
                "perturbations": list(self.perturbations),
                "horizon_days":  self.horizon_days,
                "seed":          self.seed,
                "disputed":      0,
                "confidence":    None,
                "created_at":    self.created_at,
            }],
        )
        await session.commit()
        return self.scenario_id

    @classmethod
    async def load(cls, session: AsyncSession, scenario_id: str) -> "Scenario":
        """Read a scenario back by id."""
        result = await session.execute(
            select(scenarios_table).where(scenarios_table.c.scenario_id == scenario_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise KeyError(f"scenario {scenario_id!r} not found")
        # JSONType deserialises to a Python list/dict on Postgres; on SQLite
        # it may come back as a JSON-serialised string.  Normalise.
        perts = row["perturbations"]
        if isinstance(perts, str):
            import json as _json
            perts = _json.loads(perts)
        return cls(
            name=row["name"],
            horizon_days=int(row["horizon_days"]),
            seed=int(row["seed"]),
            perturbations=list(perts or []),
            description=row["description"],
            scenario_id=row["scenario_id"],
            created_at=float(row["created_at"]),
        )
