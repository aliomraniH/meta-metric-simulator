"""Provenance audit tests.

Eight tests per the M8 spec.  test_clean_audit_passes runs against the
actual repo state; the other seven exercise the rule engine via tmp
yaml files.
"""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from curation.provenance_audit import (
    AuditResult,
    run_audit,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _minimal_registry(tmp_path: Path, source_string: str = "test_source_v1.md §1.1") -> Path:
    """Write a minimal sources_registry.yaml with one section_ref + one primary."""
    text = dedent(f"""\
        _meta:
          owner: "curation"
          refresh_cadence_days: 90
          last_updated: "2026-04-28"
          layer: 5
        section_refs:
          "{source_string}": demo_primary
        primary_sources:
          demo_primary:
            url: "https://example.com/x"
            accessed_date: "2026-04-28"
            type: "industry"
            confidence: "medium"
            trust_tier: 3
            aliases: []
        validation:
          expected_yaml_files_using_registry: []
          audit_command: "python -m curation.provenance_audit"
          audit_will_fail_when: []
          audit_warns_when: []
          non_100_sum_exemptions: []
          meta_q4_2025_anchor_check: {{}}
        """)
    path = tmp_path / "registry.yaml"
    path.write_text(text)
    return path


def _baselines_yaml(
    tmp_path: Path,
    leaves: dict,
    *,
    name: str = "test_file.yaml",
    layer: int = 4,
    owner: str = "external_facts",
    cadence: int = 90,
) -> Path:
    """Write a baselines/data/-style yaml under a tmp baselines/data/ dir."""
    bdir = tmp_path / "baselines" / "data"
    bdir.mkdir(parents=True, exist_ok=True)
    doc = {
        "_meta": {
            "owner": owner,
            "refresh_cadence_days": cadence,
            "last_updated": "2026-04-28",
            "layer": layer,
        },
    }
    doc.update(leaves)
    path = bdir / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _empty_params_dir(tmp_path: Path) -> Path:
    pdir = tmp_path / "params"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def _rule_ids(errs: list) -> list[str]:
    return [e.rule_id for e in errs]


# -----------------------------------------------------------------------------
# 1. Clean audit against the actual repo state
# -----------------------------------------------------------------------------

def test_clean_audit_passes_against_actual_repo():
    """Run audit against params/ and baselines/data/ as they exist on disk.

    Per the M8 spec: this may fail on first run with unresolved_source
    errors, in which case the failures form the alias-patch worklist.
    On a properly populated registry it passes with 0 errors (warnings
    are expected — placeholder URLs etc. are M12-deferred).
    """
    result = run_audit(
        params_dir=REPO_ROOT / "params",
        baselines_dir=REPO_ROOT / "baselines" / "data",
        registry_path=REPO_ROOT / "curation" / "sources_registry.yaml",
    )
    if not result.passed:
        # Surface the failures so an iteration cycle can patch the registry.
        unresolved = [e for e in result.errors if e.rule_id == "unresolved_source"]
        if unresolved:
            msg = "\n".join(
                f"  - {e.file_path}::{e.value_path} → {e.message}" for e in unresolved
            )
            pytest.fail(
                f"{len(unresolved)} unresolved_source error(s); add aliases to "
                f"curation/sources_registry.yaml:\n{msg}"
            )
        else:
            msg = "\n".join(f"  [{e.rule_id}] {e.file_path}::{e.value_path}" for e in result.errors)
            pytest.fail(f"audit failed with non-source errors:\n{msg}")
    assert result.passed
    assert result.stats["files_scanned"] >= 18
    assert result.stats["values_scanned"] > 100


# -----------------------------------------------------------------------------
# 2. Missing provenance triggers hard fail
# -----------------------------------------------------------------------------

def test_missing_provenance_fails(tmp_path):
    registry = _minimal_registry(tmp_path)
    _baselines_yaml(tmp_path, {
        "some_metric": {
            "value": 1.0,
            "source": "test_source_v1.md §1.1",
            # provenance intentionally missing
            "confidence": "high",
        }
    })
    result = run_audit(
        params_dir=_empty_params_dir(tmp_path),
        baselines_dir=tmp_path / "baselines" / "data",
        registry_path=registry,
    )
    assert not result.passed
    assert "missing_provenance" in _rule_ids(result.errors)


# -----------------------------------------------------------------------------
# 3. Invalid confidence triggers hard fail
# -----------------------------------------------------------------------------

def test_invalid_confidence_fails(tmp_path):
    registry = _minimal_registry(tmp_path)
    _baselines_yaml(tmp_path, {
        "some_metric": {
            "value": 1.0,
            "source": "test_source_v1.md §1.1",
            "provenance": "industry",
            "confidence": "foobar",
        }
    })
    result = run_audit(
        params_dir=_empty_params_dir(tmp_path),
        baselines_dir=tmp_path / "baselines" / "data",
        registry_path=registry,
    )
    assert not result.passed
    assert "invalid_confidence" in _rule_ids(result.errors)


# -----------------------------------------------------------------------------
# 4. Unknown source triggers hard fail with the unmatched string in the message
# -----------------------------------------------------------------------------

def test_unknown_source_fails_with_unmatched_string_in_message(tmp_path):
    registry = _minimal_registry(tmp_path)
    bogus = "completely_made_up_source_2099"
    _baselines_yaml(tmp_path, {
        "some_metric": {
            "value": 1.0,
            "source": bogus,
            "provenance": "industry",
            "confidence": "medium",
        }
    })
    result = run_audit(
        params_dir=_empty_params_dir(tmp_path),
        baselines_dir=tmp_path / "baselines" / "data",
        registry_path=registry,
    )
    assert not result.passed
    matching = [e for e in result.errors if e.rule_id == "unresolved_source"]
    assert matching, "expected at least one unresolved_source error"
    assert any(bogus in e.message for e in matching), (
        "unresolved_source message must include the unmatched source string "
        "so it can be added as an alias"
    )


# -----------------------------------------------------------------------------
# 5. Anchor check fails when forbidden prior value is present
# -----------------------------------------------------------------------------

def test_anchor_check_fails_when_dap_value_is_prior_3_35(tmp_path):
    """Build a registry with the dap anchor pointing at a tmp file whose
    dap_billion.value = 3.35 (the forbidden prior value)."""
    bdir = tmp_path / "baselines" / "data"
    bdir.mkdir(parents=True)
    fs_path = bdir / "family_scale.yaml"
    fs_path.write_text(yaml.safe_dump({
        "_meta": {
            "owner": "external_facts", "refresh_cadence_days": 90,
            "last_updated": "2026-04-28", "layer": 4,
        },
        "dap_billion": {
            "value": 3.35,  # forbidden prior value
            "source": "test_source_v1.md §1.1",
            "provenance": "earnings",
            "confidence": "high",
        },
    }, sort_keys=False))

    registry_text = dedent(f"""\
        _meta:
          owner: "curation"
          refresh_cadence_days: 90
          last_updated: "2026-04-28"
          layer: 5
        section_refs:
          "test_source_v1.md §1.1": demo_primary
        primary_sources:
          demo_primary:
            url: "https://example.com"
            accessed_date: "2026-04-28"
            type: "earnings"
            confidence: "high"
            trust_tier: 2
            aliases: []
        validation:
          expected_yaml_files_using_registry: []
          audit_command: "x"
          audit_will_fail_when: []
          audit_warns_when: []
          non_100_sum_exemptions: []
          meta_q4_2025_anchor_check:
            dap:
              file: "{fs_path}"
              field_path: "dap_billion.value"
              expected: 3.58
              forbidden_prior_value: 3.35
              source_id: "demo_primary"
              note: "test"
        """)
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(registry_text)

    result = run_audit(
        params_dir=_empty_params_dir(tmp_path),
        baselines_dir=bdir,
        registry_path=registry_path,
    )
    assert not result.passed
    assert "anchor_check_dap" in _rule_ids(result.errors)


# -----------------------------------------------------------------------------
# 6. Synthesized + high confidence emits warning, audit still passes
# -----------------------------------------------------------------------------

def test_warning_for_synthesized_high_confidence(tmp_path):
    registry = _minimal_registry(tmp_path)
    _baselines_yaml(tmp_path, {
        "some_metric": {
            "value": 1.0,
            "source": "test_source_v1.md §1.1",
            "provenance": "synthesized_2026-04-28",
            "confidence": "high",
            "note": "modeled from proxy X",
        }
    })
    result = run_audit(
        params_dir=_empty_params_dir(tmp_path),
        baselines_dir=tmp_path / "baselines" / "data",
        registry_path=registry,
    )
    assert result.passed, "synthesized+high should warn, not error"
    rule_ids = {w.rule_id for w in result.warnings}
    assert "synthesized_high_confidence" in rule_ids


# -----------------------------------------------------------------------------
# 7. Non-100 exemption honoured (real reels_creator.yaml is HypeAuditor sums)
# -----------------------------------------------------------------------------

def test_non_100_exemption_honoured_for_reels_creator():
    """The HypeAuditor tier_distribution_pct in baselines/data/reels_creator.yaml
    sums to ~110% by design.  The audit must not flag this path; the
    sources_registry.yaml non_100_sum_exemptions block lists it explicitly.
    """
    result = run_audit(
        params_dir=REPO_ROOT / "params",
        baselines_dir=REPO_ROOT / "baselines" / "data",
        registry_path=REPO_ROOT / "curation" / "sources_registry.yaml",
    )
    # No error mentioning tier_distribution_pct should be present.
    matching = [
        e for e in result.errors
        if "tier_distribution_pct" in e.value_path
        and "reels_creator.yaml" in e.file_path
    ]
    assert matching == [], (
        f"reels_creator.yaml tier_distribution_pct should be exempted from "
        f"non-100 sum check, got errors: {matching}"
    )


# -----------------------------------------------------------------------------
# 8. AuditResult round-trips through JSON
# -----------------------------------------------------------------------------

def test_audit_result_serialisable():
    result = run_audit(
        params_dir=REPO_ROOT / "params",
        baselines_dir=REPO_ROOT / "baselines" / "data",
        registry_path=REPO_ROOT / "curation" / "sources_registry.yaml",
    )
    blob = json.dumps(result.to_dict())
    parsed = json.loads(blob)
    assert parsed["passed"] == result.passed
    assert len(parsed["errors"]) == len(result.errors)
    assert len(parsed["warnings"]) == len(result.warnings)
    assert "files_scanned" in parsed["stats"]
    assert "run_timestamp" in parsed["stats"]
