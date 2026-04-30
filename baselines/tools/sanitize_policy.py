"""sanitize_policy — enforce the Layer 4 constitution.

Per PDF §2.1 / §3.3 and AGENTIC_ARCHITECTURE_INDEX.md §2.4, every
baseline write must satisfy:

  * Every numeric claim has primary-source quoted text containing the
    actual number (no unsourced numbers).
  * Primary sources outrank aggregators ("via X" framing is a downgrade
    signal, not a substitute citation).
  * Competitor metrics MUST cite the view-definition asymmetry note —
    the M18 acceptance test specifically verifies that competitor
    comparisons inject the asymmetry, and a baseline shipped without
    that anchor breaks the canary.
  * Social-media posts (X / Threads / Reddit) are never canonical
    sources for baselines.

Deterministic; no LLM.  Empty flag list = clean.
"""
from __future__ import annotations

import re
from typing import Any

from baselines.schemas import ExtractedMetric, SanitizerFlag
from baselines.server import baselines_server


# Matches any ASCII or Unicode digit — used for the "quoted text contains
# the number" check.  We don't try to extract the specific value; the
# bound sanitizer handles range checks.
_DIGIT_RE = re.compile(r"\d")

# Substrings that hint the doc / url is an aggregator rather than a
# primary source.  Kept short and explicit to avoid false positives;
# adding patterns means a deliberate review.
_AGGREGATOR_DOMAIN_PATTERNS: tuple[str, ...] = (
    "aggregator",
    "compilation",
    "summary",
    "roundup",
)

# A "via " prefix in doc_title (e.g. "Reels Stats via eMarketer") is the
# canonical secondary-source framing in the existing baselines yaml.
_VIA_PATTERN = re.compile(r"\bvia\s+", re.IGNORECASE)

# Competitor metric id signals.  The flag fires when ANY of these
# substrings appears in the metric_id.
_COMPETITOR_METRIC_TOKENS: tuple[str, ...] = (
    "competitor_",
    "tiktok",
    "youtube_shorts",
    "shorts",
    "snap",
    "spotlight",
)

# What the quoted_text must mention to satisfy the asymmetry rule.
_ASYMMETRY_ANCHORS: tuple[str, ...] = (
    "view definition",
    "view-definition",
    "asymmetry",
    "view_definition_asymmetry",
)

# Domains we never accept as canonical baseline sources.
_FORBIDDEN_DOMAINS: tuple[str, ...] = (
    "twitter.com",
    "x.com",
    "threads.net",
    "reddit.com",
)


def _has_any(haystack: str, needles: tuple[str, ...]) -> bool:
    h = haystack.lower()
    return any(n in h for n in needles)


@baselines_server.tool(annotations={"readOnlyHint": True})
async def sanitize_policy(extracted: ExtractedMetric) -> list[SanitizerFlag]:
    """Enforce Layer 4 constitution.  Deterministic; no LLM."""
    flags: list[SanitizerFlag] = []

    src = extracted.source
    quoted = (src.quoted_text or "").strip()
    quoted_l = quoted.lower()
    url_l = (src.url or "").lower()
    doc_l = (src.doc_title or "").lower()
    metric_l = (extracted.metric_id or "").lower()

    # ---- a. Unsourced number ------------------------------------------------
    # The quoted_text must exist AND contain at least one digit; otherwise
    # the numeric value has no anchor.
    if not quoted:
        flags.append(SanitizerFlag(
            kind="policy",
            severity="high",
            evidence=["source.quoted_text is empty; numeric claim has no anchor"],
            suggested_fix="Include a verbatim quoted span from the primary source containing the value",
        ))
    elif not _DIGIT_RE.search(quoted):
        flags.append(SanitizerFlag(
            kind="policy",
            severity="high",
            evidence=[
                f"source.quoted_text={quoted[:60]!r} contains no digits; "
                "numeric claim is not anchored in the cited span"
            ],
            suggested_fix="Re-extract with quoted_text that includes the actual numeric value",
        ))

    # ---- b. Primary-source preference --------------------------------------
    # Aggregator-ish domain or "via X" framing in doc_title is a downgrade.
    if _has_any(url_l, _AGGREGATOR_DOMAIN_PATTERNS) or _has_any(doc_l, _AGGREGATOR_DOMAIN_PATTERNS):
        flags.append(SanitizerFlag(
            kind="policy",
            severity="medium",
            evidence=[f"source url/doc_title looks like an aggregator: url={url_l[:60]!r} doc={doc_l[:60]!r}"],
            suggested_fix="Prefer primary source (SEC filing, earnings transcript, company IR page) over aggregator",
        ))
    elif _VIA_PATTERN.search(src.doc_title or ""):
        flags.append(SanitizerFlag(
            kind="policy",
            severity="medium",
            evidence=[f"doc_title uses 'via' framing: {src.doc_title!r}"],
            suggested_fix="Prefer primary source (SEC filing, earnings transcript, company IR page) over aggregator",
        ))

    # ---- c. Competitor metric must cite view-definition asymmetry ----------
    is_competitor_metric = any(tok in metric_l for tok in _COMPETITOR_METRIC_TOKENS)
    if is_competitor_metric:
        cites_asymmetry = any(anchor in quoted_l for anchor in _ASYMMETRY_ANCHORS)
        # Also accept doc_title that explicitly references the asymmetry
        # file or the view-definition concept, since some extractors may
        # carry the anchor in the title rather than the quote.
        if not cites_asymmetry:
            cites_asymmetry = any(anchor in doc_l for anchor in _ASYMMETRY_ANCHORS)
        if not cites_asymmetry:
            flags.append(SanitizerFlag(
                kind="policy",
                severity="high",
                evidence=[
                    f"metric_id={extracted.metric_id!r} is a competitor metric but the source "
                    "quoted_text/doc_title does not reference the view-definition asymmetry"
                ],
                suggested_fix=(
                    "Competitor metrics must cite view_definition_asymmetry.yaml — see PDF §3.3 "
                    "and AGENTIC_ARCHITECTURE_INDEX.md §2.4. The M18 acceptance canary depends on this."
                ),
            ))

    # ---- d. Forbidden source domain ----------------------------------------
    if any(domain in url_l for domain in _FORBIDDEN_DOMAINS):
        matched = next(domain for domain in _FORBIDDEN_DOMAINS if domain in url_l)
        flags.append(SanitizerFlag(
            kind="policy",
            severity="critical",
            evidence=[f"source.url is on the forbidden-domain list: matched {matched!r} in {url_l[:80]!r}"],
            suggested_fix="Social media posts are not canonical sources for baselines; replace with primary disclosure",
        ))

    return flags
