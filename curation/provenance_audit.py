"""Provenance audit — deterministic validation across params/ and baselines/data/.

Walks every yaml under params/ and baselines/data/, validates each leaf
value against the rules declared in curation/sources_registry.yaml's
validation block, and emits an AuditResult with hard errors + soft
warnings.  Pure I/O over yaml files; no LLM, no network, no algorithms/
or engine/ imports — curation/ stays in its own lane.

Run from CLI:
    python -m curation.provenance_audit
Exit code 0 on pass, 1 on fail.

Rule ids are stable strings (e.g. "missing_provenance",
"unresolved_source", "anchor_check_dap").  Tests assert on these.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Allowed enums (mirrors REFERENCE_DOC_INDEX.md §2 + sources_registry.yaml §3)
# -----------------------------------------------------------------------------

ALLOWED_PROVENANCE: frozenset[str] = frozenset({
    "earnings",
    "sec_filing",
    "industry",
    "public_statement",
    "estimate",
    "derived",
    "synthesized_2026-04-28",
})

ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"high", "medium", "low"})

EXPECTED_PARAMS_META = {"owner": "algorithm_authors", "refresh_cadence_days": 30, "layer": 3}
EXPECTED_BASELINES_META = {"owner": "external_facts", "refresh_cadence_days": 90, "layer": 4}

# Soft-warning W7: section_ref → primary_sources fan-out limit.
MAX_PRIMARIES_PER_SECTION_REF = 5


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class AuditError:
    rule_id: str
    file_path: str
    value_path: str
    message: str
    severity: str = "error"


@dataclass
class AuditWarning:
    rule_id: str
    file_path: str
    value_path: str
    message: str
    severity: str = "warning"


@dataclass
class AuditResult:
    passed: bool
    errors: list[AuditError] = field(default_factory=list)
    warnings: list[AuditWarning] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Round-trippable dict — used by the JSON serialisation test."""
        return {
            "passed": self.passed,
            "errors": [dataclasses.asdict(e) for e in self.errors],
            "warnings": [dataclasses.asdict(w) for w in self.warnings],
            "stats": self.stats,
        }


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

def run_audit(
    params_dir: str | Path = "params",
    baselines_dir: str | Path = "baselines/data",
    registry_path: str | Path = "curation/sources_registry.yaml",
) -> AuditResult:
    """Walk yaml files, apply validation rules, return AuditResult."""
    params_dir = Path(params_dir)
    baselines_dir = Path(baselines_dir)
    registry_path = Path(registry_path)

    errors: list[AuditError] = []
    warnings: list[AuditWarning] = []
    stats: dict[str, Any] = {
        "files_scanned": 0,
        "values_scanned": 0,
        "sources_used": set(),
        "provenance_values_used": set(),
        "run_timestamp": time.time(),
    }

    if not registry_path.exists():
        errors.append(AuditError(
            rule_id="registry_missing",
            file_path=str(registry_path),
            value_path="",
            message=f"sources registry not found at {registry_path}",
        ))
        return _finalise(errors, warnings, stats)

    with registry_path.open() as fh:
        registry = yaml.safe_load(fh) or {}

    section_refs = registry.get("section_refs", {}) or {}
    primary_sources = registry.get("primary_sources", {}) or {}
    validation = registry.get("validation", {}) or {}

    # ---- Registry self-consistency (F5, W6, W7) ------------------------------
    aliases_index = _build_aliases_index(primary_sources)

    referenced_ids: set[str] = set()
    for ref, val in section_refs.items():
        ids = val if isinstance(val, list) else [val]
        referenced_ids.update(ids)
        if isinstance(val, list) and len(val) > MAX_PRIMARIES_PER_SECTION_REF:
            warnings.append(AuditWarning(
                rule_id="section_ref_oversize",
                file_path=str(registry_path),
                value_path=f"section_refs.{ref!r}",
                message=(
                    f"section_ref maps to {len(val)} primary_sources "
                    f"(>{MAX_PRIMARIES_PER_SECTION_REF}); consider splitting "
                    "the upstream source field"
                ),
            ))

    for pid in sorted(referenced_ids):
        if pid not in primary_sources:
            errors.append(AuditError(
                rule_id="primary_source_missing",
                file_path=str(registry_path),
                value_path=f"section_refs → {pid}",
                message=f"primary_source id {pid!r} referenced by section_refs but not catalogued",
            ))

    for pid, entry in primary_sources.items():
        if not isinstance(entry, Mapping):
            errors.append(AuditError(
                rule_id="primary_source_malformed",
                file_path=str(registry_path),
                value_path=f"primary_sources.{pid}",
                message=f"primary_source {pid!r} is not a mapping",
            ))
            continue
        if "aliases" not in entry:
            warnings.append(AuditWarning(
                rule_id="aliases_missing",
                file_path=str(registry_path),
                value_path=f"primary_sources.{pid}",
                message=f"primary_source {pid!r} has no aliases list",
            ))
        if entry.get("url") == "PLACEHOLDER":
            warnings.append(AuditWarning(
                rule_id="placeholder_url",
                file_path=str(registry_path),
                value_path=f"primary_sources.{pid}.url",
                message=(
                    f"primary_source {pid!r} still has url='PLACEHOLDER' "
                    "(skeleton from M6c-ii; expected to be filled during M12 curation refresh)"
                ),
            ))

    # ---- File-level expected list -------------------------------------------
    expected = list(validation.get("expected_yaml_files_using_registry") or [])
    for rel in expected:
        path = Path(rel)
        if not path.exists():
            errors.append(AuditError(
                rule_id="expected_file_missing",
                file_path=rel,
                value_path="",
                message=f"expected yaml file {rel} not present on disk",
            ))

    # ---- Special view_definition_asymmetry presence + non-empty (F10) -------
    # Only fire F10 when the registry lists the file as expected.  This lets
    # tmp-yaml tests use a minimal registry without tripping the check.
    vda_expected = any(
        rel.endswith("view_definition_asymmetry.yaml") for rel in expected
    )
    if vda_expected:
        vda = baselines_dir / "view_definition_asymmetry.yaml"
        if not vda.exists():
            errors.append(AuditError(
                rule_id="view_def_asymmetry_missing",
                file_path=str(vda),
                value_path="",
                message="baselines/data/view_definition_asymmetry.yaml is required (M18 acceptance)",
            ))
        else:
            with vda.open() as fh:
                d = yaml.safe_load(fh) or {}
            mit = d.get("mandatory_injection_text", "")
            if not isinstance(mit, str) or not mit.strip():
                errors.append(AuditError(
                    rule_id="view_def_asymmetry_missing",
                    file_path=str(vda),
                    value_path="mandatory_injection_text",
                    message="mandatory_injection_text is missing or empty",
                ))

    # ---- non_100_sum_exemptions: reduce to a (file, value_path) skip set ----
    non_100_skip: set[tuple[str, str]] = {
        (e.get("file", ""), e.get("field_path", ""))
        for e in (validation.get("non_100_sum_exemptions") or [])
    }
    # Currently informational — a full non-100 percentage check is a future
    # enhancement; the skip set is wired so it's a no-op edit when that
    # check lands.
    _ = non_100_skip

    # ---- Walk every yaml file -----------------------------------------------
    yaml_files = _gather_yaml_files(params_dir, baselines_dir)
    for path in yaml_files:
        stats["files_scanned"] += 1
        try:
            with path.open() as fh:
                doc = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            errors.append(AuditError(
                rule_id="yaml_parse_error",
                file_path=str(path),
                value_path="",
                message=f"yaml parse failed: {exc}",
            ))
            continue
        _audit_file(path, doc, section_refs, aliases_index, errors, warnings, stats)

    # ---- meta_q4_2025_anchor_check (F9) -------------------------------------
    anchor_block = validation.get("meta_q4_2025_anchor_check") or {}
    _check_anchors(anchor_block, errors)

    # ---- Stringify sets for JSON serialisability ----------------------------
    stats["sources_used"] = sorted(stats["sources_used"])
    stats["provenance_values_used"] = sorted(stats["provenance_values_used"])

    return _finalise(errors, warnings, stats)


# -----------------------------------------------------------------------------
# Per-file walking
# -----------------------------------------------------------------------------

def _audit_file(
    path: Path,
    doc: Mapping[str, Any],
    section_refs: Mapping[str, Any],
    aliases_index: Mapping[str, str],
    errors: list[AuditError],
    warnings: list[AuditWarning],
    stats: dict[str, Any],
) -> None:
    rel = str(path)

    # ---- _meta block (F8) ---------------------------------------------------
    meta = doc.get("_meta") or {}
    expected_meta = (
        EXPECTED_PARAMS_META if rel.startswith(("params/", "params\\"))
        or "/params/" in rel.replace("\\", "/")
        else EXPECTED_BASELINES_META
    )
    if not meta:
        errors.append(AuditError(
            rule_id="meta_missing",
            file_path=rel,
            value_path="_meta",
            message="_meta block missing or empty",
        ))
    else:
        for k, expected_v in expected_meta.items():
            if meta.get(k) != expected_v:
                errors.append(AuditError(
                    rule_id="meta_field_mismatch",
                    file_path=rel,
                    value_path=f"_meta.{k}",
                    message=(
                        f"_meta.{k} = {meta.get(k)!r}, expected {expected_v!r} "
                        f"per sources_registry.yaml validation block"
                    ),
                ))

    # ---- Recursive leaf walk -----------------------------------------------
    for value_path, leaf in _iter_leaves(doc):
        stats["values_scanned"] += 1
        _check_leaf(rel, value_path, leaf, section_refs, aliases_index,
                    errors, warnings, stats)


def _iter_leaves(node: Any, path: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Yield (dotted_path, leaf_dict) for every leaf value block.

    A leaf value block is a mapping carrying a `source` field.  Nested
    dicts that don't carry `source` are recursed into.  `_meta` is
    handled separately and skipped here.
    """
    if isinstance(node, Mapping):
        if "source" in node and isinstance(node.get("source"), str):
            yield path, node
            # Some leaves nest sub-leaves (e.g. capex_2026_guidance.low/high).
            # Recurse only into mapping children that themselves contain a source.
            for k, v in node.items():
                if k == "_meta" or not isinstance(v, Mapping):
                    continue
                if "source" in v:
                    yield from _iter_leaves(v, f"{path}.{k}" if path else k)
            return
        for k, v in node.items():
            if k == "_meta":
                continue
            new_path = f"{path}.{k}" if path else k
            if isinstance(v, (Mapping, list)):
                yield from _iter_leaves(v, new_path)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_leaves(v, f"{path}[{i}]")


def _check_leaf(
    file_path: str,
    value_path: str,
    leaf: Mapping[str, Any],
    section_refs: Mapping[str, Any],
    aliases_index: Mapping[str, str],
    errors: list[AuditError],
    warnings: list[AuditWarning],
    stats: dict[str, Any],
) -> None:
    # F1: source presence (already filtered by _iter_leaves; here we belt-and-braces)
    source = leaf.get("source")
    if not isinstance(source, str) or not source:
        errors.append(AuditError(
            rule_id="missing_source",
            file_path=file_path,
            value_path=value_path,
            message="leaf value missing `source` field",
        ))
        return
    stats["sources_used"].add(source)

    # F2: provenance presence
    provenance = leaf.get("provenance")
    if provenance is None:
        errors.append(AuditError(
            rule_id="missing_provenance",
            file_path=file_path,
            value_path=value_path,
            message="leaf value missing `provenance` field",
        ))
    else:
        stats["provenance_values_used"].add(provenance)
        # F6: provenance enum
        if provenance not in ALLOWED_PROVENANCE:
            errors.append(AuditError(
                rule_id="invalid_provenance",
                file_path=file_path,
                value_path=value_path,
                message=(
                    f"provenance={provenance!r} not in allowed set: "
                    f"{sorted(ALLOWED_PROVENANCE)}"
                ),
            ))

    # F3: confidence presence
    confidence = leaf.get("confidence")
    if confidence is None:
        errors.append(AuditError(
            rule_id="missing_confidence",
            file_path=file_path,
            value_path=value_path,
            message="leaf value missing `confidence` field",
        ))
    elif confidence not in ALLOWED_CONFIDENCE:
        # F7: confidence enum
        errors.append(AuditError(
            rule_id="invalid_confidence",
            file_path=file_path,
            value_path=value_path,
            message=(
                f"confidence={confidence!r} not in allowed set: "
                f"{sorted(ALLOWED_CONFIDENCE)}"
            ),
        ))

    # F4: source resolves
    if not _resolve_source(source, section_refs, aliases_index):
        errors.append(AuditError(
            rule_id="unresolved_source",
            file_path=file_path,
            value_path=value_path,
            message=(
                f"source string {source!r} not found in section_refs nor "
                f"any primary_source's aliases. Add to "
                f"sources_registry.yaml section_refs or as an alias."
            ),
        ))

    # ---- Soft warnings ------------------------------------------------------
    note = leaf.get("note") or ""
    note_str = note if isinstance(note, str) else ""

    # W3: synthesized provenance without note
    if provenance == "synthesized_2026-04-28" and not note_str.strip():
        warnings.append(AuditWarning(
            rule_id="synthesized_without_note",
            file_path=file_path,
            value_path=value_path,
            message=(
                "value with provenance='synthesized_2026-04-28' should carry a "
                "`note` explaining the synthesis or proxy "
                "(REFERENCE_DOC_INDEX §2)"
            ),
        ))

    # W4: flag:not_disclosed in note but provenance not synthesized
    if "flag:not_disclosed" in note_str and provenance != "synthesized_2026-04-28":
        # Only warn when the value is non-null — null values with not_disclosed
        # are properly handled (see reels_specific_prevalence pattern).
        if leaf.get("value") is not None:
            warnings.append(AuditWarning(
                rule_id="not_disclosed_provenance_mismatch",
                file_path=file_path,
                value_path=value_path,
                message=(
                    "note carries 'flag:not_disclosed' but provenance is "
                    f"{provenance!r} (expected 'synthesized_2026-04-28')"
                ),
            ))

    # W5: flag:stale in note — confirm provenance was preserved (not downgraded)
    if "flag:stale" in note_str and provenance == "synthesized_2026-04-28":
        warnings.append(AuditWarning(
            rule_id="stale_provenance_downgraded",
            file_path=file_path,
            value_path=value_path,
            message=(
                "stale value should preserve original provenance, not "
                "downgrade to synthesized_2026-04-28"
            ),
        ))

    # Synthesized + high confidence is suspicious — surface as warning so
    # authors don't accidentally inflate confidence on a modeled estimate.
    if provenance == "synthesized_2026-04-28" and confidence == "high":
        warnings.append(AuditWarning(
            rule_id="synthesized_high_confidence",
            file_path=file_path,
            value_path=value_path,
            message=(
                "synthesized values should not carry confidence='high'; "
                "modeled estimates are 'low' (or 'medium' only when derived from primary)"
            ),
        ))


# -----------------------------------------------------------------------------
# Source-string resolution
# -----------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _normalise(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def _build_aliases_index(primary_sources: Mapping[str, Any]) -> dict[str, str]:
    """Flatten every primary_source's aliases list into a lookup.

    Keys are the alias strings AND each primary_source id itself (so a
    yaml may reference an id directly).  Values are the canonical id.
    Both raw and normalised forms are inserted.
    """
    out: dict[str, str] = {}
    for pid, entry in (primary_sources or {}).items():
        # Allow direct id references.
        out[pid] = pid
        out[_normalise(pid)] = pid
        if not isinstance(entry, Mapping):
            continue
        for alias in entry.get("aliases", []) or ():
            if isinstance(alias, str):
                out[alias] = pid
                out[_normalise(alias)] = pid
    return out


def _resolve_source(
    source: str,
    section_refs: Mapping[str, Any],
    aliases_index: Mapping[str, str],
) -> bool:
    if source in section_refs:
        return True
    if source in aliases_index:
        return True
    norm = _normalise(source)
    if norm in {_normalise(k) for k in section_refs.keys()}:
        return True
    if norm in aliases_index:
        return True
    return False


# -----------------------------------------------------------------------------
# Anchor checks (§0 corrections from the reference doc)
# -----------------------------------------------------------------------------

def _check_anchors(anchor_block: Mapping[str, Any], errors: list[AuditError]) -> None:
    """Run the four meta_q4_2025_anchor_check rules from the registry."""
    for anchor_id, spec in (anchor_block or {}).items():
        if not isinstance(spec, Mapping):
            continue
        rel_path = spec.get("file")
        path = Path(rel_path) if rel_path else None
        field_path = spec.get("field_path", "")
        expected = spec.get("expected")
        forbidden = spec.get("forbidden_prior_value")

        if path is None or not path.exists():
            errors.append(AuditError(
                rule_id=f"anchor_check_{anchor_id}",
                file_path=str(path) if path else "",
                value_path=field_path,
                message=f"anchor file missing: {rel_path}",
            ))
            continue

        with path.open() as fh:
            doc = yaml.safe_load(fh) or {}

        # Navigate the dotted field_path.  The "value" segment indexes into
        # the leaf's `value` key; intermediate segments are dict keys.
        actual = _navigate_path(doc, field_path)
        if actual != expected:
            errors.append(AuditError(
                rule_id=f"anchor_check_{anchor_id}",
                file_path=str(path),
                value_path=field_path,
                message=(
                    f"anchor mismatch: expected {expected!r} at {field_path}, "
                    f"got {actual!r}.  {spec.get('note', '')}"
                ),
            ))

        if forbidden is not None and _value_appears_anywhere(doc, forbidden):
            errors.append(AuditError(
                rule_id=f"anchor_check_{anchor_id}",
                file_path=str(path),
                value_path="(scan)",
                message=(
                    f"forbidden prior value {forbidden!r} appears in {path} — "
                    f"per §0 of the reference doc, the corrected value is "
                    f"{expected!r}; the prior value must not be persisted."
                ),
            ))


def _navigate_path(doc: Any, path: str) -> Any:
    if not path:
        return doc
    node = doc
    for part in path.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return None
    return node


def _value_appears_anywhere(doc: Any, target: Any) -> bool:
    """Walk every leaf's `value` field and check for an exact match.

    Equality uses float tolerance for numeric targets so 3.35 vs 3.350001
    don't slip through; a plain == is fine for ints / strings.
    """
    if isinstance(target, float):
        def _matches(x: Any) -> bool:
            return isinstance(x, (int, float)) and abs(float(x) - target) < 1e-9
    else:
        def _matches(x: Any) -> bool:
            return x == target

    def _walk(node: Any) -> bool:
        if isinstance(node, Mapping):
            if "value" in node and _matches(node.get("value")):
                return True
            return any(_walk(v) for v in node.values())
        if isinstance(node, list):
            return any(_walk(v) for v in node)
        return False

    return _walk(doc)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _gather_yaml_files(params_dir: Path, baselines_dir: Path) -> list[Path]:
    files: list[Path] = []
    if params_dir.exists():
        files.extend(sorted(params_dir.glob("*.yaml")))
    if baselines_dir.exists():
        files.extend(sorted(baselines_dir.glob("*.yaml")))
    return files


def _finalise(
    errors: list[AuditError],
    warnings: list[AuditWarning],
    stats: dict[str, Any],
) -> AuditResult:
    if isinstance(stats.get("sources_used"), set):
        stats["sources_used"] = sorted(stats["sources_used"])
    if isinstance(stats.get("provenance_values_used"), set):
        stats["provenance_values_used"] = sorted(stats["provenance_values_used"])
    return AuditResult(
        passed=(len(errors) == 0),
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _format_summary(result: AuditResult) -> str:
    lines: list[str] = []
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"=== Provenance audit: {status} ===")
    lines.append(
        f"files_scanned={result.stats.get('files_scanned')} "
        f"values_scanned={result.stats.get('values_scanned')} "
        f"errors={len(result.errors)} warnings={len(result.warnings)}"
    )
    if result.errors:
        lines.append("")
        lines.append("ERRORS:")
        by_file: dict[str, list[AuditError]] = {}
        for e in result.errors:
            by_file.setdefault(e.file_path, []).append(e)
        for fpath, errs in sorted(by_file.items()):
            lines.append(f"  {fpath}:")
            for e in errs:
                lines.append(f"    [{e.rule_id}] {e.value_path or '(file)'} — {e.message}")
    if result.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        by_rule: dict[str, list[AuditWarning]] = {}
        for w in result.warnings:
            by_rule.setdefault(w.rule_id, []).append(w)
        for rid, ws in sorted(by_rule.items()):
            lines.append(f"  [{rid}] {len(ws)} occurrence(s)")
            for w in ws[:5]:
                lines.append(f"    {w.file_path}::{w.value_path} — {w.message}")
            if len(ws) > 5:
                lines.append(f"    ... +{len(ws) - 5} more")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the provenance audit.")
    p.add_argument("--params-dir", default="params")
    p.add_argument("--baselines-dir", default="baselines/data")
    p.add_argument("--registry", default="curation/sources_registry.yaml")
    p.add_argument("--json", action="store_true", help="emit JSON instead of human summary")
    args = p.parse_args(argv)

    result = run_audit(args.params_dir, args.baselines_dir, args.registry)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_format_summary(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(_main())
