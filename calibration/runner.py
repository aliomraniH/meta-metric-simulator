"""Calibration runner — executes the six §11 tests against the engine + metrics.

Loads config/calibration_config.json, builds a Scenario per test, runs it
through the M7 Simulator, computes metrics via the M9 MetricRuntime,
and compares each assertion against the expected band from §11 of the
reference doc.

Test_06 (competitor parity) is non-simulation — its assertions read
yaml files directly via the `type: yaml` and `type: yaml_present` paths.

The runner is async because metric_runtime.compute is async.  CLI entry
point exits 0 on all-pass, 1 on any-fail.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.scenario import Scenario
from engine.simulator import Simulator
from metrics.runtime import MetricRuntime

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class AssertionResult:
    metric: str | None
    group_by: str | None
    comparison: str
    expected: Any
    actual: Any
    tolerance_pct: float | None
    passed: bool
    gap: float | None = None
    detail: str = ""


@dataclass
class CalibrationResult:
    test_id: str
    passed: bool
    assertions_results: list[AssertionResult] = field(default_factory=list)
    runtime_sec: float = 0.0
    scenario_id: str | None = None
    failing_param_fix: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "passed": self.passed,
            "scenario_id": self.scenario_id,
            "runtime_sec": round(self.runtime_sec, 3),
            "failing_param_fix": self.failing_param_fix if not self.passed else None,
            "error": self.error,
            "assertions": [dataclasses.asdict(a) for a in self.assertions_results],
        }


# -----------------------------------------------------------------------------
# Config loading
# -----------------------------------------------------------------------------

def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open() as fh:
        return json.load(fh)


def build_scenario(test_cfg: dict[str, Any]) -> Scenario | None:
    """Construct a Scenario object from one test's `scenario` block.

    Returns None for non-simulation tests (e.g. test_06)."""
    scn = test_cfg.get("scenario")
    if scn is None:
        return None
    return Scenario(
        name=scn["name"],
        horizon_days=int(scn["horizon_days"]),
        seed=int(scn["seed"]),
        perturbations=list(scn.get("perturbations") or []),
    )


# -----------------------------------------------------------------------------
# Comparison helpers
# -----------------------------------------------------------------------------

def _within_pct(actual: float, expected: float, tolerance_pct: float) -> bool:
    if expected == 0:
        return abs(actual) <= tolerance_pct / 100.0
    return abs(actual - expected) / abs(expected) <= tolerance_pct / 100.0


def _gap_pct(actual: float, expected: float) -> float:
    if expected == 0:
        return float("inf") if actual != 0 else 0.0
    return 100.0 * (actual - expected) / abs(expected)


def evaluate_assertion(
    a: dict[str, Any],
    actual: Any,
    tolerance_default_pct: float,
) -> AssertionResult:
    """Compare a single actual value (or list/range) against the assertion's expected."""
    comparison = a["comparison"]
    expected = a["expected"]
    tol = a.get("tolerance_pct")
    if tol is None:
        tol = tolerance_default_pct

    passed = False
    gap: float | None = None
    detail = ""

    if comparison == "delta_pct":
        if not isinstance(actual, (int, float)):
            return AssertionResult(
                metric=a.get("metric"), group_by=a.get("group_by"),
                comparison=comparison, expected=expected, actual=actual,
                tolerance_pct=tol, passed=False,
                detail=f"actual={actual!r} is not a number",
            )
        if isinstance(expected, dict) and "low" in expected and "high" in expected:
            low = float(expected["low"])
            high = float(expected["high"])
            tol_frac = tol / 100.0
            band_low = low - abs(low) * tol_frac
            band_high = high + abs(high) * tol_frac
            passed = band_low <= float(actual) <= band_high
            gap = _gap_pct(float(actual), (low + high) / 2.0)
            detail = f"band [{band_low:.3f}, {band_high:.3f}] (±{tol}% of [{low}, {high}])"
        else:
            exp_v = float(expected)
            passed = _within_pct(float(actual), exp_v, tol)
            gap = _gap_pct(float(actual), exp_v)
            detail = f"target {exp_v} ±{tol}%"

    elif comparison == "absolute":
        if not isinstance(actual, (int, float)):
            return AssertionResult(
                metric=a.get("metric"), group_by=a.get("group_by"),
                comparison=comparison, expected=expected, actual=actual,
                tolerance_pct=tol, passed=False,
                detail=f"actual={actual!r} is not a number",
            )
        exp_v = float(expected)
        passed = _within_pct(float(actual), exp_v, tol)
        gap = _gap_pct(float(actual), exp_v)
        detail = f"target {exp_v} ±{tol}%"

    elif comparison == "in_range":
        low = float(expected["low"])
        high = float(expected["high"])
        if isinstance(actual, (int, float)):
            passed = low <= float(actual) <= high
            gap = 0.0 if passed else (
                float(actual) - high if actual > high else float(actual) - low
            )
            detail = f"in [{low}, {high}]"
        else:
            passed = False
            detail = f"actual={actual!r} is not a number"

    elif comparison == "ratio":
        # Ratio assertions: actual is itself a ratio (e.g. numerator/denominator
        # already computed by the caller).  Treated like absolute with tolerance.
        exp_v = float(expected)
        if isinstance(actual, (int, float)):
            passed = _within_pct(float(actual), exp_v, tol)
            gap = _gap_pct(float(actual), exp_v)
            detail = f"ratio target {exp_v} ±{tol}%"
        else:
            passed = False
            detail = f"actual={actual!r} not a number"

    elif comparison == "ge":
        exp_v = float(expected)
        if isinstance(actual, (int, float)):
            passed = float(actual) >= exp_v
            gap = float(actual) - exp_v
            detail = f"≥ {exp_v}"
        else:
            passed = False
            detail = f"actual={actual!r} not a number"

    elif comparison == "le":
        exp_v = float(expected)
        if isinstance(actual, (int, float)):
            passed = float(actual) <= exp_v
            gap = float(actual) - exp_v
            detail = f"≤ {exp_v}"
        else:
            passed = False
            detail = f"actual={actual!r} not a number"

    else:
        detail = f"unknown comparison: {comparison!r}"

    return AssertionResult(
        metric=a.get("metric"),
        group_by=a.get("group_by"),
        comparison=comparison,
        expected=expected,
        actual=actual,
        tolerance_pct=tol,
        passed=passed,
        gap=gap,
        detail=detail,
    )


# -----------------------------------------------------------------------------
# Metric value extraction (at_tick reduction)
# -----------------------------------------------------------------------------

def _reduce_metric_rows(rows: list[dict[str, Any]], at_tick: Any) -> Any:
    """Pick a single value from a list of metric rows per the at_tick rule."""
    if not rows:
        return None

    if at_tick == "average":
        values = [float(r["value"]) for r in rows if r.get("value") is not None]
        return statistics.fmean(values) if values else None

    if at_tick == "final":
        # Final = last non-None value when ordered by dim ascending.
        non_null = [r for r in rows if r.get("value") is not None]
        if not non_null:
            return None
        try:
            non_null.sort(key=lambda r: float(r["dim"]))
        except (TypeError, ValueError):
            non_null.sort(key=lambda r: str(r["dim"]))
        return float(non_null[-1]["value"])

    if isinstance(at_tick, int):
        for r in rows:
            try:
                if int(r["dim"]) == at_tick and r.get("value") is not None:
                    return float(r["value"])
            except (TypeError, ValueError):
                continue
        return None

    return None


def _delta_pct_value(rows: list[dict[str, Any]], at_tick: Any) -> Any:
    """For delta_pct comparisons: target value vs baseline (mean of first 3 ticks)."""
    if not rows:
        return None

    by_dim: list[tuple[float, float]] = []
    for r in rows:
        if r.get("value") is None:
            continue
        try:
            by_dim.append((float(r["dim"]), float(r["value"])))
        except (TypeError, ValueError):
            continue
    by_dim.sort()
    if not by_dim:
        return None

    baseline_vals = [v for d, v in by_dim if d < 3]
    baseline = statistics.fmean(baseline_vals) if baseline_vals else None
    final_value = _reduce_metric_rows(rows, at_tick)
    if baseline is None or final_value is None:
        return None
    if baseline == 0:
        return float("inf") if final_value != 0 else 0.0
    return 100.0 * (float(final_value) - baseline) / abs(baseline)


# -----------------------------------------------------------------------------
# YAML-side assertions (test_06)
# -----------------------------------------------------------------------------

def _yaml_value(file_path: str | Path, dotted: str) -> Any:
    p = Path(file_path)
    if not p.exists():
        return None
    with p.open() as fh:
        doc = yaml.safe_load(fh) or {}
    node: Any = doc
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _evaluate_yaml_assertion(a: dict[str, Any], default_tol: float) -> AssertionResult:
    actual = _yaml_value(a["file"], a["field_path"])
    # `yaml_present` assertions check for substring presence and have no
    # `comparison` field — handle them on the `type` discriminator.
    if a.get("type") == "yaml_present":
        must_contain = a.get("must_contain", "")
        passed = isinstance(actual, str) and must_contain in actual
        return AssertionResult(
            metric=None, group_by=None,
            comparison="yaml_present",
            expected=must_contain,
            actual=actual[:80] + "…" if isinstance(actual, str) and len(actual) > 80 else actual,
            tolerance_pct=None,
            passed=passed,
            detail=f"{a['file']}::{a['field_path']} must contain {must_contain!r}",
        )
    return evaluate_assertion(a, actual, default_tol)


# -----------------------------------------------------------------------------
# Core run
# -----------------------------------------------------------------------------

async def run_test(
    test_id: str,
    test_cfg: dict[str, Any],
    simulator: Simulator | None,
    metric_runtime: MetricRuntime | None,
    tolerance_default_pct: float,
) -> CalibrationResult:
    t0 = time.perf_counter()
    result = CalibrationResult(test_id=test_id, passed=False)

    # Special handling for test_06 (no scenario).
    if test_cfg.get("scenario") is None:
        for a in test_cfg.get("assertions", []):
            if a.get("type") == "yaml" or a.get("type") == "yaml_present":
                result.assertions_results.append(
                    _evaluate_yaml_assertion(a, tolerance_default_pct)
                )
            else:
                result.assertions_results.append(AssertionResult(
                    metric=None, group_by=None, comparison=a.get("comparison", "?"),
                    expected=a.get("expected"), actual=None, tolerance_pct=None,
                    passed=False,
                    detail=f"non-simulation test only supports yaml assertions; got type={a.get('type')!r}",
                ))
        result.passed = all(ar.passed for ar in result.assertions_results)
        if not result.passed:
            result.failing_param_fix = test_cfg.get("failing_param_fix")
        result.runtime_sec = time.perf_counter() - t0
        return result

    if simulator is None or metric_runtime is None:
        result.error = "simulator and metric_runtime are required for simulation tests"
        result.runtime_sec = time.perf_counter() - t0
        return result

    try:
        scenario = build_scenario(test_cfg)
        assert scenario is not None
        await simulator.run(scenario)
        result.scenario_id = scenario.scenario_id

        for a in test_cfg.get("assertions", []):
            atype = a.get("type", "metric")
            if atype == "yaml" or atype == "yaml_present":
                result.assertions_results.append(
                    _evaluate_yaml_assertion(a, tolerance_default_pct)
                )
                continue

            metric = a["metric"]
            group_by = a.get("group_by")
            params = a.get("params") or {}
            at_tick = a.get("at_tick", "final")

            rows = await metric_runtime.compute(
                metric, scenario_id=scenario.scenario_id, group_by=group_by, **params
            )
            if not rows:
                result.assertions_results.append(AssertionResult(
                    metric=metric, group_by=group_by, comparison=a["comparison"],
                    expected=a["expected"], actual=None, tolerance_pct=None,
                    passed=False, detail="metric returned no rows",
                ))
                continue

            if a["comparison"] == "delta_pct":
                actual = _delta_pct_value(rows, at_tick)
            else:
                actual = _reduce_metric_rows(rows, at_tick)

            result.assertions_results.append(
                evaluate_assertion(a, actual, tolerance_default_pct)
            )

        result.passed = all(ar.passed for ar in result.assertions_results)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("calibration test %s raised", test_id)

    if not result.passed:
        result.failing_param_fix = test_cfg.get("failing_param_fix")
    result.runtime_sec = time.perf_counter() - t0
    return result


async def run_all(
    config: dict[str, Any],
    simulator: Simulator | None,
    metric_runtime: MetricRuntime | None,
) -> list[CalibrationResult]:
    tol = float(config.get("tolerance_default_pct", 20))
    results: list[CalibrationResult] = []
    for tid, cfg in (config.get("tests") or {}).items():
        results.append(await run_test(tid, cfg, simulator, metric_runtime, tol))
    return results


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _format_summary(results: list[CalibrationResult]) -> str:
    lines: list[str] = []
    n_pass = sum(1 for r in results if r.passed)
    n_total = len(results)
    overall = "PASS" if n_pass == n_total else "FAIL"
    lines.append(f"=== Calibration: {overall} ({n_pass}/{n_total} tests) ===")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        n_pass_a = sum(1 for a in r.assertions_results if a.passed)
        n_total_a = len(r.assertions_results)
        lines.append(
            f"{r.test_id}: {status} ({r.runtime_sec:.2f}s) — "
            f"{n_pass_a}/{n_total_a} assertions"
        )
        if not r.passed:
            if r.error:
                lines.append(f"    ERROR: {r.error}")
            for a in r.assertions_results:
                if not a.passed:
                    lines.append(
                        f"    - [{a.comparison}] metric={a.metric} actual={a.actual} "
                        f"expected={a.expected} {a.detail}"
                    )
            if r.failing_param_fix:
                lines.append(f"    → tune: {r.failing_param_fix}")
    return "\n".join(lines)


async def _amain(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    simulator: Simulator | None = None
    metric_runtime: MetricRuntime | None = None
    if any((cfg or {}).get("scenario") is not None
           for cfg in (config.get("tests") or {}).values()):
        # Only build simulator + runtime when needed.
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from infra.db import metadata
        eng = create_async_engine(args.database_url, future=True)
        async with eng.begin() as conn:
            await conn.run_sync(metadata.create_all)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        simulator = Simulator(
            factory,
            params_dir=args.params_dir,
            viewers_n=args.viewers_n,
            creators_n=args.creators_n,
            reels_n=args.reels_n,
        )
        metric_runtime = MetricRuntime(factory, definitions_dir=args.definitions_dir)

    results = await run_all(config, simulator, metric_runtime)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print(_format_summary(results))
    return 0 if all(r.passed for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the calibration suite.")
    p.add_argument("--config", default="config/calibration_config.json")
    p.add_argument("--params-dir", default="params")
    p.add_argument("--definitions-dir", default="metrics/definitions")
    p.add_argument("--database-url", default="sqlite+aiosqlite:///:memory:")
    p.add_argument("--viewers-n", type=int, default=20)
    p.add_argument("--creators-n", type=int, default=10)
    p.add_argument("--reels-n", type=int, default=50)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
