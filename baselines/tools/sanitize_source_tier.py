"""sanitize_source_tier — rank source against curation/sources_registry.yaml.

Per PDF §2.1 / §3.3 and AGENTIC_ARCHITECTURE_INDEX.md §2.4, every
baseline write should prefer the highest available trust tier:

  1 — SEC filings
  2 — Earnings calls + Meta IR press releases
  3 — Vetted industry analysts
  4 — Blog / aggregator

This sanitizer resolves the proposed source against the registry built
in M6c, then flags depending on the matched tier and whether the
metric_id is "high-stakes" (DAP, capex, run rate, FY revenue, share-
of-time / share-of-ads — the calibration anchors).

Deterministic; no LLM.  The registry is loaded ONCE at module import
time so per-call overhead is just dict lookups.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import yaml

from baselines.schemas import ExtractedMetric, SanitizerFlag
from baselines.server import baselines_server

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration: which metric_ids are high-stakes calibration anchors
# ---------------------------------------------------------------------------

# Kept explicit; do NOT heuristically infer.  Adding a metric here is a
# deliberate review of "what would a tier-3 source for this value mean
# for calibration?"
HIGH_STAKES_METRIC_IDS: frozenset[str] = frozenset({
    "dap_billion",
    "fy_2025_revenue_usd_billion",
    "q4_2025_revenue_usd_billion",
    "fy_2025_capex_usd_billion",
    "capex_2026_guidance_usd_billion",
    "run_rate_usd_billion",
    "share_of_us_ig_time_pct",
    "share_of_ig_ads_pct",
})


# ---------------------------------------------------------------------------
# Registry load — module-level singleton
# ---------------------------------------------------------------------------

# Default registry path.  Tests may override via _set_registry_path_for_tests.
_DEFAULT_REGISTRY_PATH = Path("curation/sources_registry.yaml")

# Counters + caches for the singleton loader.  _load_count is exposed so
# the test_registry_loaded_once test can assert exactly-once load.
_load_count: int = 0
_url_to_tier: dict[str, int] = {}
_alias_to_tier: dict[str, int] = {}
_domain_to_tier: dict[str, int] = {}


def _build_indexes(primary_sources: dict) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    url_to_tier: dict[str, int] = {}
    alias_to_tier: dict[str, int] = {}
    domain_to_tier: dict[str, int] = {}
    for pid, entry in (primary_sources or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            tier = int(entry.get("trust_tier", 4))
        except (TypeError, ValueError):
            tier = 4
        url = (entry.get("url") or "").strip()
        if url and url != "PLACEHOLDER":
            url_to_tier[url] = tier
            try:
                host = urlparse(url).hostname
            except Exception:  # noqa: BLE001
                host = None
            if host:
                # Normalise www-prefixed hosts to their bare form so a
                # quote matching www.sec.gov also matches sec.gov.
                bare = host.lower()
                if bare.startswith("www."):
                    bare = bare[4:]
                # Earlier (lower-tier) entries should not overwrite later
                # higher-tier entries.  Tier 1 < tier 4 numerically.
                if domain_to_tier.get(bare, 99) > tier:
                    domain_to_tier[bare] = tier
        for alias in entry.get("aliases", []) or ():
            if isinstance(alias, str) and alias.strip():
                alias_to_tier[alias.strip().lower()] = tier
        # The primary_source id itself is also a valid alias.
        alias_to_tier[pid.lower()] = tier
    return url_to_tier, alias_to_tier, domain_to_tier


def _load_registry(path: Path = _DEFAULT_REGISTRY_PATH) -> None:
    """Populate the module-level lookup tables from disk.  Idempotent —
    repeat calls reload."""
    global _load_count, _url_to_tier, _alias_to_tier, _domain_to_tier
    _load_count += 1
    if not path.exists():
        log.warning("sources registry not found at %s; tier checks degrade to 'unknown'", path)
        _url_to_tier = {}
        _alias_to_tier = {}
        _domain_to_tier = {}
        return
    with path.open() as fh:
        doc = yaml.safe_load(fh) or {}
    primary = doc.get("primary_sources", {}) or {}
    _url_to_tier, _alias_to_tier, _domain_to_tier = _build_indexes(primary)


# Load eagerly on first import.  Tests can call _set_registry_path_for_tests
# to reload from a different fixture path.
_load_registry()


def _set_registry_path_for_tests(path: Path) -> None:
    """Test-only hook: reload the registry from a different file."""
    _load_registry(path)


def _resolve_tier(extracted: ExtractedMetric) -> int | None:
    """Return the matched trust_tier (1..4) or None if unresolved."""
    src = extracted.source
    url = (src.url or "").strip()
    doc_title = (src.doc_title or "").strip()

    # 1. Direct URL match.
    if url and url in _url_to_tier:
        return _url_to_tier[url]

    # 2. Domain match.
    if url:
        try:
            host = urlparse(url).hostname or ""
        except Exception:  # noqa: BLE001
            host = ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host in _domain_to_tier:
            return _domain_to_tier[host]

    # 3. Alias match against doc_title (case-insensitive).
    if doc_title:
        title_l = doc_title.lower()
        if title_l in _alias_to_tier:
            return _alias_to_tier[title_l]
        # Also try contains-match against aliases when the title is
        # longer than a bare alias (e.g. "Meta Q4 2025 press release —
        # full text" contains the registered alias).
        for alias, tier in _alias_to_tier.items():
            if alias and alias in title_l:
                return tier

    return None


@baselines_server.tool(annotations={"readOnlyHint": True})
async def sanitize_source_tier(extracted: ExtractedMetric) -> list[SanitizerFlag]:
    """Rank source against sources_registry.yaml trust tiers.  Deterministic; no LLM."""
    flags: list[SanitizerFlag] = []

    tier = _resolve_tier(extracted)
    if tier is None:
        flags.append(SanitizerFlag(
            kind="source_tier",
            severity="medium",
            evidence=[
                f"source url={extracted.source.url!r} doc_title={extracted.source.doc_title!r} "
                "did not resolve to any primary_source in curation/sources_registry.yaml"
            ],
            suggested_fix="Add this source to curation/sources_registry.yaml as a new primary_source or as an alias on an existing entry",
        ))
        return flags

    is_high_stakes = extracted.metric_id in HIGH_STAKES_METRIC_IDS

    if tier == 4 and is_high_stakes:
        flags.append(SanitizerFlag(
            kind="source_tier",
            severity="high",
            evidence=[
                f"metric_id={extracted.metric_id!r} is a high-stakes calibration anchor but matched tier {tier} (blog/aggregator)"
            ],
            suggested_fix="High-stakes metrics require tier 1-2 sources (SEC, earnings_call). This source is tier 4.",
        ))
    elif tier == 3 and is_high_stakes:
        flags.append(SanitizerFlag(
            kind="source_tier",
            severity="medium",
            evidence=[
                f"metric_id={extracted.metric_id!r} is high-stakes but matched tier {tier} (analyst/industry)"
            ],
            suggested_fix="Prefer tier 1-2 source (SEC, earnings_call) if available.",
        ))
    # tier 1, 2, or low-stakes tier 3/4 — no flag.

    return flags
