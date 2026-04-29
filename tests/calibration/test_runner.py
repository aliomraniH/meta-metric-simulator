"""Calibration runner mechanics tests.

Exercises the runner's config-loading, scenario-building, assertion
evaluation, and lock-file mechanics.  Does NOT run the actual six §11
calibration scenarios — those are M11b's job.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from calibration import lock as lock_mod
from calibration.runner import (
    AssertionResult,
    CalibrationResult,
    build_scenario,
    evaluate_assertion,
    load_config,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_CONFIG = REPO_ROOT / "config" / "calibration_config.json"


# -----------------------------------------------------------------------------
# Config loading + scenario building
# -----------------------------------------------------------------------------

def test_runner_loads_config():
    cfg = load_config(LIVE_CONFIG)
    assert "tolerance_default_pct" in cfg
    assert cfg["tolerance_default_pct"] == 20
    tests = cfg.get("tests") or {}
    assert set(tests.keys()) == {"test_01", "test_02", "test_03", "test_04", "test_05", "test_06"}
    for tid, tcfg in tests.items():
        assert tcfg["test_id"] == tid
        assert "description" in tcfg
        assert "anchor_section" in tcfg
        assert "assertions" in tcfg
        assert "failing_param_fix" in tcfg
        # scenario may be None for test_06.
        assert "scenario" in tcfg


def test_runner_builds_scenario_from_config():
    cfg = load_config(LIVE_CONFIG)
    test_01 = cfg["tests"]["test_01"]
    s = build_scenario(test_01)
    assert s is not None
    assert s.name == "test_01_time_share_growth"
    assert s.horizon_days == 14
    assert s.seed == 1101
    assert len(s.perturbations) == 1
    assert s.perturbations[0]["type"] == "ramp"


def test_runner_builds_scenario_returns_none_for_test_06():
    cfg = load_config(LIVE_CONFIG)
    s = build_scenario(cfg["tests"]["test_06"])
    assert s is None  # non-simulation


# -----------------------------------------------------------------------------
# evaluate_assertion mechanics
# -----------------------------------------------------------------------------

def test_assertion_delta_pct_in_band_passes():
    a = {
        "comparison": "delta_pct",
        "expected": {"low": 15, "high": 25},
        "metric": "ad_revenue_per_dau",
    }
    r = evaluate_assertion(a, actual=20.0, tolerance_default_pct=20)
    assert r.passed
    assert isinstance(r, AssertionResult)


def test_assertion_delta_pct_outside_band_fails():
    a = {
        "comparison": "delta_pct",
        "expected": {"low": 15, "high": 25},
        "metric": "ad_revenue_per_dau",
    }
    # 50% drift well outside the 20%-tolerance widened band [12, 30].
    r = evaluate_assertion(a, actual=50.0, tolerance_default_pct=20)
    assert not r.passed


def test_assertion_in_range_pass_and_fail():
    a = {"comparison": "in_range", "expected": {"low": 30, "high": 35}}
    r_in = evaluate_assertion(a, actual=32.0, tolerance_default_pct=20)
    assert r_in.passed
    r_out = evaluate_assertion(a, actual=40.0, tolerance_default_pct=20)
    assert not r_out.passed


def test_assertion_ge_and_le():
    r_ge = evaluate_assertion(
        {"comparison": "ge", "expected": 200},
        actual=200.0, tolerance_default_pct=20,
    )
    assert r_ge.passed
    r_ge_fail = evaluate_assertion(
        {"comparison": "ge", "expected": 200},
        actual=199.9, tolerance_default_pct=20,
    )
    assert not r_ge_fail.passed
    r_le = evaluate_assertion(
        {"comparison": "le", "expected": 100},
        actual=99.5, tolerance_default_pct=20,
    )
    assert r_le.passed


def test_assertion_unknown_comparison_fails():
    a = {"comparison": "wiggle", "expected": 0}
    r = evaluate_assertion(a, actual=0.0, tolerance_default_pct=20)
    assert not r.passed
    assert "unknown" in r.detail.lower()


# -----------------------------------------------------------------------------
# Lock file mechanics
# -----------------------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path):
    """Build a tiny params/baselines/config layout under tmp_path."""
    params = tmp_path / "params"
    baselines = tmp_path / "baselines" / "data"
    cfg_dir = tmp_path / "config"
    params.mkdir(parents=True)
    baselines.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    (params / "ranking_weights.yaml").write_text(yaml.safe_dump(
        {"_meta": {"layer": 3}, "alpha": {"value": 0.4}}
    ))
    (params / "creator_economics.yaml").write_text(yaml.safe_dump(
        {"_meta": {"layer": 3}, "elasticity": {"value": 0.5}}
    ))
    (baselines / "family_scale.yaml").write_text(yaml.safe_dump(
        {"_meta": {"layer": 4}, "dap": {"value": 3.58}}
    ))
    cfg = cfg_dir / "calibration_config.json"
    cfg.write_text(json.dumps({"tolerance_default_pct": 20, "tests": {}}))
    return {
        "params": params,
        "baselines": baselines,
        "config": cfg,
        "lock": tmp_path / "calibration" / "CALIBRATION_LOCKED",
    }


def test_lock_writes_and_verifies(sandbox):
    fake_results = [
        CalibrationResult(test_id=f"test_{i:02d}", passed=True)
        for i in range(1, 7)
    ]
    lock_mod.lock_calibration(
        fake_results,
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    assert sandbox["lock"].exists()

    valid, drifted = lock_mod.verify_lock_against_current_state(
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    assert valid is True
    assert drifted == []


def test_lock_detects_drift_when_yaml_changes(sandbox):
    fake_results = [CalibrationResult(test_id="test_01", passed=True)]
    lock_mod.lock_calibration(
        fake_results,
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    # Mutate one params yaml.
    (sandbox["params"] / "ranking_weights.yaml").write_text(yaml.safe_dump(
        {"_meta": {"layer": 3}, "alpha": {"value": 0.99}}  # changed
    ))
    valid, drifted = lock_mod.verify_lock_against_current_state(
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    assert valid is False
    assert any("ranking_weights.yaml" in d for d in drifted)


def test_lock_detects_drift_when_config_changes(sandbox):
    fake_results = [CalibrationResult(test_id="test_01", passed=True)]
    lock_mod.lock_calibration(
        fake_results,
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    sandbox["config"].write_text(json.dumps({"tolerance_default_pct": 50, "tests": {}}))
    valid, drifted = lock_mod.verify_lock_against_current_state(
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    assert valid is False
    assert any("calibration_config.json" in d for d in drifted)


def test_lock_is_idempotent_overwrites_cleanly(sandbox):
    r1 = [CalibrationResult(test_id="test_01", passed=True)]
    r2 = [CalibrationResult(test_id="test_01", passed=False, failing_param_fix="x.yaml")]
    lock_mod.lock_calibration(
        r1,
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    first_payload = json.loads(sandbox["lock"].read_text())
    lock_mod.lock_calibration(
        r2,
        params_dir=sandbox["params"],
        baselines_dir=sandbox["baselines"],
        calibration_config=sandbox["config"],
        lock_path=sandbox["lock"],
    )
    second_payload = json.loads(sandbox["lock"].read_text())
    assert first_payload["results"][0]["passed"] is True
    assert second_payload["results"][0]["passed"] is False


def test_is_locked_helper(sandbox):
    assert not lock_mod.is_locked(sandbox["lock"])
    sandbox["lock"].parent.mkdir(parents=True, exist_ok=True)
    sandbox["lock"].write_text("{}")
    assert lock_mod.is_locked(sandbox["lock"])


# -----------------------------------------------------------------------------
# CLI exit behaviour
# -----------------------------------------------------------------------------

def test_calibration_runner_main_exits_zero_on_trivial_pass(tmp_path):
    """Build a config with a single trivially-passing yaml-only test, run
    the runner via subprocess, assert exit 0.  Uses test_06-style assertions
    against a minimal baselines yaml so no simulator is required."""
    minimal_baselines = tmp_path / "baselines" / "data"
    minimal_baselines.mkdir(parents=True)
    yaml_path = minimal_baselines / "trivial.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "_meta": {"layer": 4},
        "x": {"value": 42, "source": "test", "provenance": "industry", "confidence": "medium"},
    }))

    cfg_path = tmp_path / "calibration_config.json"
    cfg_path.write_text(json.dumps({
        "tolerance_default_pct": 20,
        "tests": {
            "test_trivial": {
                "test_id": "test_trivial",
                "name": "trivial",
                "description": "trivial yaml-only test",
                "anchor_section": "n/a",
                "scenario": None,
                "assertions": [
                    {
                        "type": "yaml",
                        "file": str(yaml_path),
                        "field_path": "x.value",
                        "comparison": "in_range",
                        "expected": {"low": 40, "high": 50}
                    }
                ],
                "failing_param_fix": "n/a",
            }
        }
    }))

    result = subprocess.run(
        [sys.executable, "-m", "calibration.runner", "--config", str(cfg_path), "--json"],
        capture_output=True, text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"runner exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_calibration_runner_main_exits_one_on_trivial_fail(tmp_path):
    """Mirror of above but with an assertion that fails."""
    minimal_baselines = tmp_path / "baselines" / "data"
    minimal_baselines.mkdir(parents=True)
    yaml_path = minimal_baselines / "trivial.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "_meta": {"layer": 4},
        "x": {"value": 42, "source": "test", "provenance": "industry", "confidence": "medium"},
    }))

    cfg_path = tmp_path / "calibration_config.json"
    cfg_path.write_text(json.dumps({
        "tolerance_default_pct": 20,
        "tests": {
            "test_fail": {
                "test_id": "test_fail",
                "name": "fail",
                "description": "trivial yaml-only test that fails",
                "anchor_section": "n/a",
                "scenario": None,
                "assertions": [
                    {
                        "type": "yaml",
                        "file": str(yaml_path),
                        "field_path": "x.value",
                        "comparison": "in_range",
                        "expected": {"low": 1000, "high": 2000}
                    }
                ],
                "failing_param_fix": "n/a",
            }
        }
    }))

    result = subprocess.run(
        [sys.executable, "-m", "calibration.runner", "--config", str(cfg_path)],
        capture_output=True, text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
