"""Simulator — composes scenario + params + tick into a full run.

Loads the six params yaml files ONCE in __init__ so a per-tick cost is
zero and so a run is parameter-immutable (a determinism prerequisite).
Run() persists the scenario, bootstraps WorldState, iterates run_tick
across the horizon, and flushes the writer at the end.

Async because the substrate writer + scenarios persistence go through
infra/db's AsyncSession.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker

from engine.scenario import Scenario
from engine.state import WorldState
from engine.tick import run_tick
from substrate.writer import EventWriter

log = logging.getLogger(__name__)


PARAM_FILE_NAMES: tuple[str, ...] = (
    "ranking_weights.yaml",
    "segment_propensities.yaml",
    "monetization_curves.yaml",
    "guardrail_thresholds.yaml",
    "integrity_dynamics.yaml",
    "creator_economics.yaml",
)


class Simulator:
    """One Simulator per process; reused across many scenario runs."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        params_dir: str | Path = "params",
        *,
        viewers_n: int | None = None,
        creators_n: int | None = None,
        reels_n: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.params: dict[str, Mapping[str, Any]] = self._load_params(Path(params_dir))
        self._viewers_n = viewers_n
        self._creators_n = creators_n
        self._reels_n = reels_n

    @staticmethod
    def _load_params(params_dir: Path) -> dict[str, Mapping[str, Any]]:
        loaded: dict[str, Mapping[str, Any]] = {}
        for fname in PARAM_FILE_NAMES:
            path = params_dir / fname
            if not path.exists():
                raise FileNotFoundError(f"required params file missing: {path}")
            with path.open() as fh:
                loaded[fname.removesuffix(".yaml")] = yaml.safe_load(fh) or {}
        return loaded

    async def run(self, scenario: Scenario) -> str:
        """Persist + simulate.  Returns scenario.scenario_id when done."""
        kwargs: dict[str, int] = {}
        if self._viewers_n is not None:
            kwargs["viewers_n"] = self._viewers_n
        if self._creators_n is not None:
            kwargs["creators_n"] = self._creators_n
        if self._reels_n is not None:
            kwargs["reels_n"] = self._reels_n

        state = WorldState.bootstrap(seed=scenario.seed, params=self.params, **kwargs)

        async with self._session_factory() as session:
            await scenario.save(session)
            writer = EventWriter(session)
            # Stamp scenario_id on the writer so tick.py can read it without
            # threading the scenario through every helper.
            writer.scenario_id = scenario.scenario_id  # type: ignore[attr-defined]

            for _ in range(scenario.horizon_days):
                state = await run_tick(state, scenario.perturbations, self.params, writer)
            await session.commit()

        log.info(
            "scenario %s complete: horizon=%d days, seed=%d, perturbations=%d",
            scenario.scenario_id,
            scenario.horizon_days,
            scenario.seed,
            len(scenario.perturbations),
        )
        return scenario.scenario_id
