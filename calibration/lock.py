"""Calibration lock — gates access to agentic layers (M12+).

When all six §11 calibration tests pass, lock_calibration writes
calibration/CALIBRATION_LOCKED with:
  - timestamp
  - git HEAD commit (best-effort; "unknown" outside a git work tree)
  - all six CalibrationResult dicts
  - SHA256 of every params/*.yaml and baselines/data/*.yaml
  - SHA256 of config/calibration_config.json itself

verify_lock_against_current_state re-hashes the same files and reports
which (if any) drifted since lock-time.  The agentic-layer prelude in
M12 calls this before allowing any synthesizer write.

The lock file is committed to git — it's a build artefact that records
which yaml snapshot the calibration locked against.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)


LOCK_PATH = Path("calibration") / "CALIBRATION_LOCKED"

DEFAULT_PARAMS_DIR = Path("params")
DEFAULT_BASELINES_DIR = Path("baselines/data")
DEFAULT_CALIBRATION_CONFIG = Path("config/calibration_config.json")


# -----------------------------------------------------------------------------
# Hashing
# -----------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_dir(directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        str(p.relative_to(directory.parent)): _sha256(p)
        for p in sorted(directory.glob("*.yaml"))
    }


def _git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        return r.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def is_locked(lock_path: str | Path = LOCK_PATH) -> bool:
    return Path(lock_path).exists()


def lock_calibration(
    results: Iterable[Any],
    *,
    params_dir: str | Path = DEFAULT_PARAMS_DIR,
    baselines_dir: str | Path = DEFAULT_BASELINES_DIR,
    calibration_config: str | Path = DEFAULT_CALIBRATION_CONFIG,
    lock_path: str | Path = LOCK_PATH,
) -> None:
    """Write the lock file.  Idempotent — overwrites prior lock cleanly."""
    params_dir = Path(params_dir)
    baselines_dir = Path(baselines_dir)
    calibration_config = Path(calibration_config)
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    serialised_results: list[Any] = []
    for r in results:
        if hasattr(r, "to_dict"):
            serialised_results.append(r.to_dict())
        elif isinstance(r, dict):
            serialised_results.append(r)
        else:
            serialised_results.append(repr(r))

    payload: dict[str, Any] = {
        "locked_at": time.time(),
        "locked_at_human": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": _git_head(),
        "results": serialised_results,
        "params_hashes": _hash_dir(params_dir),
        "baselines_hashes": _hash_dir(baselines_dir),
        "calibration_config_hash": (
            _sha256(calibration_config) if calibration_config.exists() else None
        ),
    }
    lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    log.info("calibration locked: git_head=%s, files=%d/%d, lock=%s",
             payload["git_head"][:12],
             len(payload["params_hashes"]),
             len(payload["baselines_hashes"]),
             lock_path)


def verify_lock_against_current_state(
    *,
    params_dir: str | Path = DEFAULT_PARAMS_DIR,
    baselines_dir: str | Path = DEFAULT_BASELINES_DIR,
    calibration_config: str | Path = DEFAULT_CALIBRATION_CONFIG,
    lock_path: str | Path = LOCK_PATH,
) -> tuple[bool, list[str]]:
    """Return (still_valid, drifted_files).

    still_valid is False when ANY file's SHA differs from the locked SHA,
    when the lock file is missing, or when a previously-hashed file was
    deleted.  drifted_files lists the relative paths that changed.
    """
    lock_path = Path(lock_path)
    if not lock_path.exists():
        return False, ["calibration/CALIBRATION_LOCKED missing"]

    with lock_path.open() as fh:
        payload = json.load(fh)

    drifted: list[str] = []

    locked_params = payload.get("params_hashes") or {}
    current_params = _hash_dir(Path(params_dir))
    drifted.extend(_diff_hashes(locked_params, current_params))

    locked_baselines = payload.get("baselines_hashes") or {}
    current_baselines = _hash_dir(Path(baselines_dir))
    drifted.extend(_diff_hashes(locked_baselines, current_baselines))

    locked_cfg_hash = payload.get("calibration_config_hash")
    cfg_path = Path(calibration_config)
    current_cfg_hash = _sha256(cfg_path) if cfg_path.exists() else None
    if locked_cfg_hash != current_cfg_hash:
        drifted.append(str(cfg_path))

    return (len(drifted) == 0), drifted


def _diff_hashes(locked: dict[str, str], current: dict[str, str]) -> list[str]:
    drifted: list[str] = []
    for path, locked_hash in locked.items():
        if path not in current:
            drifted.append(f"{path} (deleted)")
        elif current[path] != locked_hash:
            drifted.append(path)
    for path in current:
        if path not in locked:
            drifted.append(f"{path} (new file added post-lock)")
    return drifted


# =============================================================================
# M12c-ii additions: agentic-layer guards
# =============================================================================

import functools


class CalibrationLockError(RuntimeError):
    """Raised when an agentic operation runs against a drifted lock."""


def requires_calibration_unlocked(f):
    """Decorator: aborts an agentic operation if the calibration lock has drifted.

    Per architecture_research.pdf §10.5 and AGENTIC_ARCHITECTURE_INDEX.md
    §8 rule 3.  Wrap every synthesize_* tool with this; the M12c-ii
    layer-isolation hook does NOT replace this check — they are belt
    and braces (the decorator runs in-process; the hook runs at the
    SDK boundary).
    """

    @functools.wraps(f)
    async def wrapper(*args, **kwargs):
        valid, drifted = verify_lock_against_current_state()
        if not valid:
            raise CalibrationLockError(
                f"Calibration drift detected in {drifted}. "
                "Re-run python -m calibration.runner and re-lock before proceeding."
            )
        return await f(*args, **kwargs)

    return wrapper


def would_change_locked_hash(
    target_file: str | Path,
    target_path: str,
    new_value,
    *,
    lock_path: str | Path = LOCK_PATH,
) -> bool:
    """Return True iff applying {target_path: new_value} to target_file
    would produce a SHA256 different from the locked hash.

    Used by synthesize_baseline before committing to disk.  The
    synthesizer escalates via ctx.elicit() when this returns True so
    the user can approve the calibration-invalidating change with
    full context.

    Implementation: load the current yaml, set the dotted target_path
    to a {value: new_value, ...} block (preserving any source/
    provenance/confidence fields already there), serialize back to
    bytes, sha256, compare to the locked hash.
    """
    import json
    import yaml as _yaml

    target = Path(target_file)
    lock = Path(lock_path)
    if not target.exists() or not lock.exists():
        # No file on disk OR no lock to compare against → conservative
        # answer is "yes, this would change something" so the
        # synthesizer surfaces it for human review.
        return True

    with lock.open() as fh:
        payload = json.load(fh)
    locked_hashes = {
        **(payload.get("params_hashes") or {}),
        **(payload.get("baselines_hashes") or {}),
    }
    # The hash keys are paths relative to the parent of the dir
    # (params/<file> and baselines/data/<file>).  Find the matching key.
    target_key = str(target)
    locked_hash = locked_hashes.get(target_key)
    if locked_hash is None:
        # Try matching by basename within either dir.
        for key in locked_hashes:
            if Path(key).name == target.name:
                locked_hash = locked_hashes[key]
                break
    if locked_hash is None:
        return True  # not in lock → unknown → surface for human review

    # Load current content, apply hypothetical change, recompute hash.
    with target.open() as fh:
        doc = _yaml.safe_load(fh) or {}
    parts = target_path.split(".")
    node = doc
    # Walk to the parent of the leaf field; create dicts as needed.
    for part in parts[:-1]:
        if not isinstance(node, dict):
            return True  # path doesn't fit current shape → would change
        if part not in node:
            return True
        node = node[part]
    if not isinstance(node, dict):
        return True
    leaf_key = parts[-1]
    # Preserve sibling fields (source / provenance / confidence /
    # note); only update the value.
    existing = node.get(leaf_key)
    if isinstance(existing, dict):
        new_block = {**existing, "value": new_value}
    else:
        new_block = {"value": new_value}
    node[leaf_key] = new_block

    candidate_text = _yaml.safe_dump(doc, sort_keys=False).encode("utf-8")
    candidate_hash = hashlib.sha256(candidate_text).hexdigest()

    return candidate_hash != locked_hash
