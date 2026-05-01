"""sanitize_constitution — deterministic check of a DiffProposal against the constitution.

Per AGENTIC_ARCHITECTURE_INDEX.md §2.3 (Option D constitutional
critique-revise) the curation flow has TWO constitutional gates:

  1. This deterministic check — code-based, fast, side-effect-free.
  2. The curation-critic LLM (curation/prompts/critic.md) — Sonnet 4.6,
     re-checks the same six rules + handles ambiguous cases.

The LLM critic is a backstop; this code-based gate catches the
clear-cut violations cheaply.  Both are wired in M13b's synthesizer.

Annotated `readOnlyHint=True` — emits flags only, never writes.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from baselines.tools.sanitize_source_tier import HIGH_STAKES_METRIC_IDS
from curation.schemas import ConstitutionFlag, DiffProposal
from curation.server import curation_server

log = logging.getLogger(__name__)


# Domains that are constitutionally forbidden — social media + anonymous forums.
# Maintained alongside curation/prompts/refresher.md Rule 5.  Adding a domain
# here is a deliberate review step.
FORBIDDEN_DOMAINS: frozenset[str] = frozenset({
    "twitter.com",
    "x.com",
    "threads.net",
    "reddit.com",
    "www.reddit.com",
})

# Aggregator hosts — tier 3.  If a high-stakes metric's evidence is
# entirely from this set, that's a constitutional violation per Rule 6.
AGGREGATOR_DOMAINS: frozenset[str] = frozenset({
    "emarketer.com",
    "www.emarketer.com",
    "statista.com",
    "www.statista.com",
    "businessofapps.com",
    "www.businessofapps.com",
    "sensortower.com",
    "www.sensortower.com",
})

# Substrings in a registry_entry_id that mark it as competitor-shaped.
# Triggers the view-definition-asymmetry check (Rule 4).
_COMPETITOR_TOKENS: tuple[str, ...] = (
    "competitor_",
    "tiktok",
    "youtube_shorts",
    "shorts",
    "snap_spotlight",
)

_NUMERIC_FIELDS: frozenset[str] = frozenset({"trust_tier", "confidence"})

_MIN_RATIONALE_CHARS = 20


@curation_server.tool(annotations={"readOnlyHint": True})
async def sanitize_constitution(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Validate a DiffProposal against the six constitutional rules.

    Args:
        diff:  the proposal from the curation-refresher (or from
               diff_proposal directly).

    Returns:
        Zero or more ConstitutionFlag.  Higher severities (`high`,
        `critical`) trigger the critique-revise loop in M13b's
        synthesizer; lower severities are advisory only.
    """
    flags: list[ConstitutionFlag] = []

    flags.extend(_check_uncited_numeric(diff))
    flags.extend(_check_forbidden_domain(diff))
    flags.extend(_check_aggregator_only_high_stakes(diff))
    flags.extend(_check_competitor_missing_asymmetry(diff))
    flags.extend(_check_short_rationale(diff))

    return flags


# ---------------------------------------------------------------------------
# Rule helpers — one per constitutional rule
# ---------------------------------------------------------------------------

def _check_uncited_numeric(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Rule 2 — every numeric proposal must have cited_evidence."""
    is_numeric_field = diff.field in _NUMERIC_FIELDS
    is_numeric_value = isinstance(diff.proposed_value, (int, float)) and not isinstance(
        diff.proposed_value, bool
    )
    if not (is_numeric_field or is_numeric_value):
        return []
    if diff.cited_evidence:
        return []
    return [
        ConstitutionFlag(
            rule="uncited_numeric",
            severity="critical",
            evidence=[
                f"numeric proposal for {diff.registry_entry_id!r}.{diff.field!r} "
                f"has empty cited_evidence (proposed_value={diff.proposed_value!r})"
            ],
            suggested_fix=(
                "Cite at least one primary source from the allowed-domain set "
                "with quoted_text matching the proposed value."
            ),
        )
    ]


def _check_forbidden_domain(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Rule 5 — twitter / x / threads / reddit are forbidden."""
    offending: list[str] = []
    for cit in diff.cited_evidence:
        url = _cit_url(cit)
        host = _hostname(url)
        if host in FORBIDDEN_DOMAINS:
            offending.append(url)
    if not offending:
        return []
    return [
        ConstitutionFlag(
            rule="forbidden_domain",
            severity="critical",
            evidence=[f"cited_evidence contains forbidden URL: {u}" for u in offending],
            suggested_fix=(
                "Replace with a primary-source citation (SEC filing, "
                "earnings call transcript, or company IR page)."
            ),
        )
    ]


def _check_aggregator_only_high_stakes(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Rule 6 — high-stakes metrics need at least one tier-1/2 primary citation.

    The registry entry id may not literally match a metric_id; we
    compare it case-insensitively against the HIGH_STAKES_METRIC_IDS
    set with substring containment so e.g. `meta_q4_2025_dap_billion`
    is treated as high-stakes."""
    if not diff.cited_evidence:
        return []
    if not _is_high_stakes(diff.registry_entry_id):
        return []
    hosts = [_hostname(_cit_url(c)) for c in diff.cited_evidence]
    hosts = [h for h in hosts if h]
    if not hosts:
        return []
    if all(h in AGGREGATOR_DOMAINS for h in hosts):
        return [
            ConstitutionFlag(
                rule="secondary_aggregator_used",
                severity="high",
                evidence=[
                    f"high-stakes registry entry {diff.registry_entry_id!r} "
                    f"cites only aggregators: {sorted(set(hosts))}"
                ],
                suggested_fix=(
                    "Add at least one tier-1 or tier-2 primary citation "
                    "(SEC, earnings transcript, or company IR)."
                ),
            )
        ]
    return []


def _check_competitor_missing_asymmetry(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Rule 4 — competitor proposals must mention the view-definition asymmetry."""
    if not _is_competitor(diff.registry_entry_id):
        return []
    haystack_parts: list[str] = [diff.rationale or ""]
    for cit in diff.cited_evidence:
        haystack_parts.append(str(cit.get("quoted_text", "")) if isinstance(cit, dict) else "")
    haystack = " ".join(haystack_parts).lower()
    if "view definition" in haystack or "asymmetry" in haystack:
        return []
    return [
        ConstitutionFlag(
            rule="competitor_missing_asymmetry",
            severity="high",
            evidence=[
                f"competitor proposal {diff.registry_entry_id!r} lacks the "
                "view-definition asymmetry note (Rule 4)"
            ],
            suggested_fix=(
                "Quote or reference baselines/data/view_definition_asymmetry.yaml "
                "in cited_evidence or rationale."
            ),
        )
    ]


def _check_short_rationale(diff: DiffProposal) -> list[ConstitutionFlag]:
    """Proxy for Rule 1 — a too-short rationale usually means the
    refresher didn't think the change through."""
    if (diff.rationale or "").strip() and len(diff.rationale.strip()) >= _MIN_RATIONALE_CHARS:
        return []
    return [
        ConstitutionFlag(
            rule="primary_source_required",
            severity="medium",
            evidence=[
                f"rationale is missing or shorter than {_MIN_RATIONALE_CHARS} characters "
                f"(got {len(diff.rationale or '')!r}); this usually indicates the refresher "
                "skipped the constitutional review."
            ],
            suggested_fix=(
                "Write a plain-English rationale naming the primary source "
                "and why this diff is preferable to the current value."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _cit_url(cit: Any) -> str:
    if isinstance(cit, dict):
        return str(cit.get("url") or cit.get("source_url") or "")
    return ""


def _hostname(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    return host


def _is_high_stakes(registry_entry_id: str) -> bool:
    rid = registry_entry_id.lower()
    for metric in HIGH_STAKES_METRIC_IDS:
        if metric in rid:
            return True
    return False


def _is_competitor(registry_entry_id: str) -> bool:
    rid = registry_entry_id.lower()
    return any(tok in rid for tok in _COMPETITOR_TOKENS)
