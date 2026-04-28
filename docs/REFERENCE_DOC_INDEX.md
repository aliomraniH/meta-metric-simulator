# Reference Doc Index

This file is the index over `docs/reels_metrics_comprehensive_v2.md` (the
canonical curation snapshot, dated 2026-04-28).  The simulator never
reads the reference doc at runtime — `M5` writes `params/*.yaml` and
`M6` writes `baselines/data/*.yaml` from the values cited here.  This
index tells contributors which section of the doc populates which file,
and how to translate confidence tiers, special tags, verbatim quotes,
and calibration anchors into the yaml/json the system actually consumes.

---

## 1. Section → file mapping

| Reference doc section                                  | Target file(s)                                                                                       | Notes                                                                                                                                                          |
|--------------------------------------------------------|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| §0 Critical calibration notes                          | (none)                                                                                               | Documentation only — used for cross-checks during M5–M11.  Each of the 10 items lists a class of mistake the simulator must not make.                          |
| §1.1 Family DAP                                        | `baselines/data/family_scale.yaml`                                                                   | Use the corrected 3.58B value (was 3.35B in v1) per §0 item 1.  +7% YoY tempo number.                                                                         |
| §1.2 Revenue (FY/Q4 2025, Q1 2026 guidance)            | `baselines/data/family_scale.yaml`                                                                   | Q1 2026 unreported as of snapshot — record `flag:not_disclosed` with Meta guidance range and consensus.                                                       |
| §1.3 Capex                                             | `baselines/data/family_scale.yaml`                                                                   | Use the corrected $115–135B 2026 guidance (was $114–118B in v1) per §0 item 2.                                                                                |
| §1.4 Reality Labs                                      | `baselines/data/family_scale.yaml`                                                                   | Q4 / FY 2025 operating losses; cumulative-since-2020 narrative used by interview question gen.                                                                 |
| §1.5 Q4 ad metrics (impressions / price per ad)        | `baselines/data/family_scale.yaml`                                                                   | Calibration anchor for `test_01_time_share_growth` (impressions +18% YoY, price +6% YoY).                                                                      |
| §1.6 Headcount                                         | `baselines/data/family_scale.yaml`                                                                   | 78,865 as of Dec 31, 2025; +6% YoY.                                                                                                                            |
| §1.7 Susan Li Q1 2026 guidance verbatim quote          | `baselines/data/family_scale.yaml` (anchor field) + `interview/rubric/meta_at_rubric.yaml` (M17)     | Verbatim quote — must be persisted as-is, not paraphrased.                                                                                                     |
| §2 Social / digital ad market share                    | `baselines/data/family_scale.yaml`                                                                   | Multiple denominators (social vs digital); surface the methodology asymmetry to the interview question gen.  `interview_pitfall` flagged in §2.                |
| §3.1 IG user base (3B MAU; ~2B DAU)                    | `baselines/data/instagram_platform.yaml`                                                             | Mosseri/Zuckerberg public statements; DAU/MAU 0.65 is derived, not disclosed.                                                                                  |
| §3.2 IG time spent                                     | `baselines/data/instagram_platform.yaml`                                                             | eMarketer; medium confidence.                                                                                                                                  |
| §3.3 IG ad revenue (eMarketer estimates)               | `baselines/data/instagram_platform.yaml`                                                             | Meta does not separately disclose IG; record both eMarketer and Quantumrun figures with provenance "industry".                                                 |
| §3.4 IG top regions (DataReportal)                     | `baselines/data/instagram_platform.yaml`                                                             | Use DataReportal as primary baseline; record Backlinko / Sprout Social variance as a note.                                                                     |
| §4.1 Reels time and engagement share                   | `baselines/data/reels_engagement.yaml` + `baselines/data/reels_product.yaml`                         | Sensor Tower 37%→46% drives `test_01`.  200B daily Reels plays is `flag:stale`.                                                                                |
| §4.2 Reels monetization                                | `baselines/data/reels_monetization.yaml` + `params/monetization_curves.yaml`                         | $50B run rate is the §0 item 3 anchor through 2026-04-28.  eCPM tiers feed `params/monetization_curves.yaml`.  Reels-vs-Feed ratio is `flag:not_disclosed`.    |
| §4.3 Creator economics                                 | `baselines/data/reels_creator.yaml` + `params/creator_economics.yaml`                                | 55% revshare is widely cited but not Meta-documented (§0 item 10 → confidence "low").  Creator payout numbers come from Meta March 18, 2026 announcement.      |
| §4.4 Engagement micro-metrics                          | `baselines/data/reels_engagement.yaml` + `params/segment_propensities.yaml` (seed values)            | Per §0 item 10, Meta does not publish per-Reels engagement.  All entries here carry confidence "low" or "medium" with industry sources.                        |
| §4.5 Skip Rate (Aug 2025)                              | `baselines/data/reels_engagement.yaml`                                                               | New creator-facing metric; no Meta benchmark for "good" rate.                                                                                                  |
| §4.6 Integrity (Transparency Center H1 2026)           | `baselines/data/reels_integrity.yaml` + `params/guardrail_thresholds.yaml`                           | FB violating prevalence 0.15–0.16% and IG adult nudity 0.09–0.11% are public_statement / high confidence.  Reels-specific is `flag:not_disclosed`.            |
| §4.7 Feb 26 2025 Reels integrity incident              | `calibration/tests/test_03_integrity_incident.py` (M11) + `baselines/data/reels_integrity.yaml`      | Calibration anchor — per §0 item 9, do NOT use a specific peak prevalence number.  Apology quote is verbatim.                                                  |
| §4.8 Creator concentration                             | `baselines/data/reels_creator.yaml` + `params/creator_economics.yaml`                                | HypeAuditor tier distribution → industry / medium.  Gini 0.85–0.95 is `flag:not_disclosed`; default 0.90 / synthesized.                                        |
| §4.9 Reels reliability                                 | `baselines/data/reels_product.yaml`                                                                  | Crash-free + p95 cold-start are `flag:not_disclosed`; record industry SLO benchmarks with provenance "synthesized_2026-04-28".                                 |
| §5.1 TikTok                                            | `baselines/data/competitors_tiktok.yaml`                                                             | §5.1.5 USDS deal close (§0 item 4) — retire any "pending divestiture" framing; ByteDance retains 19.9%.                                                       |
| §5.2 YouTube Shorts                                    | `baselines/data/competitors_youtube_shorts.yaml`                                                     | 200B daily views carries view-definition methodology caveat; flag in `view_definition_asymmetry.yaml`.                                                         |
| §5.3 Snap Spotlight                                    | `baselines/data/competitors_snap_spotlight.yaml`                                                     | Q4 2025 8-K numbers are SEC-filing high confidence.  Spotlight MAU and Spotlight share-of-time are `flag:not_disclosed`.                                       |
| §6 View definition asymmetry                           | `baselines/data/view_definition_asymmetry.yaml` (M6 — mandatory injection)                           | The §6.2 "Mandatory injection text" block must be persisted verbatim.  Every competitor comparison response in M18 must inject this.                           |
| §7.1 EU DSA (incl. §7.1.1 Amsterdam ruling)            | (none — informational)                                                                               | Documentation only — used by interview question gen for regulatory-context questions.  No yaml.                                                                |
| §7.2 UK Online Safety Act                              | (none — informational)                                                                               | Documentation only.                                                                                                                                            |
| §7.3 Meta Teen Accounts (April 2026 expansion)         | `interview/rubric/meta_at_rubric.yaml` (M17) + (informational anchor for question gen)               | Verbatim Meta quote and MPA settlement disclaimer persisted in the rubric file.  Operational guardrail multipliers feed §9.5 → `params/segment_propensities.yaml`. |
| §7.4 US litigation and state laws                      | (none — informational)                                                                               | Documentation only — surfaced in interview question prompts that touch regulatory tradeoffs.                                                                   |
| §7.5 Reels feature launches since Feb 1 2026           | (none — informational)                                                                               | Documentation only — used by interview question gen for "what changed" prompts.                                                                                |
| §8 Meta PM interview process                           | `interview/rubric/meta_at_rubric.yaml` (M17)                                                         | Per §0 item 7, the 25/30/25/20 weight split is community-estimate; rubric file MUST embed the §8.3 disclaimer text verbatim.                                   |
| §9 Viewer segment benchmarks                           | `params/segment_propensities.yaml`                                                                   | §9.1–9.3 seed values are synthesized (provenance "synthesized_2026-04-28").  §9.4 teen-anchored values use Pew → industry / medium.                            |
| §9.5 Teen integrity guardrail multipliers              | `params/segment_propensities.yaml` (teen segment) + `params/guardrail_thresholds.yaml`               | Multipliers are public_statement / high (Meta Family Center, April 2026).                                                                                      |
| §10 Ranking signal weights                             | `params/ranking_weights.yaml`                                                                        | §10.1 seed weights derive from the Mosseri Jan 22 2025 verbatim quote (top three signals).  §10.2 35/65 pool mix is synthesized; alternative 30/70 noted.      |
| §10.3 Originality and repetition (Dec 2025 / Jan 2026) | `params/ranking_weights.yaml` (`quality_bonus`, `repetition_penalty`)                                | CreatorFlow / Funnl industry / medium.                                                                                                                          |
| §10.4 Mosseri Dec 31 2025 year-end memo themes         | (none — informational)                                                                               | Documentation only — informs interview question gen.                                                                                                           |
| §11 Calibration acceptance criteria                    | `docs/CALIBRATION_SPEC.md` (M11) + `config/calibration_config.json` (M11) + `calibration/tests/*.py` | Source of truth for the six tests.  Inequalities and tolerances copied verbatim — never loosened.                                                              |
| §12 Document refresh log                               | (none)                                                                                               | Documentation only — used to date the next-pull window for `curation/` agent (Layer 5 refresh).                                                                |
| §13 Citation discipline summary                        | `curation/sources_registry.yaml` (M6) — policy reference                                             | Documentation only here; the registry's `trust_tier` and `confidence` fields are seeded from this section's guidance.                                          |

---

## 2. Confidence-tier policy

The reference doc tags every numeric value with a `type` and a
`confidence` tier.  When an M5 / M6 author copies a value into a yaml
file, those two fields collapse into the yaml's per-value
`{provenance, confidence}` pair using the table below.  The mapping is
load-bearing: the curation provenance audit (M8) walks every numeric
value in `params/` and `baselines/data/` and fails if `provenance` is
missing or unrecognised.

| Reference doc tier                                                                  | yaml `provenance`                | yaml `confidence` | When to use                                                                                                                                                                              |
|-------------------------------------------------------------------------------------|----------------------------------|-------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `confidence=high` AND `type=earnings`                                               | `"earnings"`                     | `"high"`          | Meta IR earnings calls, Meta press releases on quarterly results, Alphabet earnings calls.  Direct first-party financial disclosure.                                                     |
| `confidence=high` AND `type=sec_filing`                                             | `"sec_filing"`                   | `"high"`          | Meta 8-K, 10-K, 10-Q; Alphabet 8-K / 10-K; Snap 8-K.  The most authoritative tier — auditor-attested financials.                                                                         |
| `confidence=high` AND `type=public_statement`                                       | `"public_statement"`             | `"high"`          | Meta Newsroom posts, Mosseri verbatim statements with citation, Meta Transparency Center reports, Meta Family Center policy text, EU Commission press releases, Pew teen anchor numbers. |
| `confidence=medium` AND `type=industry`                                             | `"industry"`                     | `"medium"`        | eMarketer, Sensor Tower, DataReportal, Tinuiti, Pew, Sprout Social, HypeAuditor, Backlinko reporting their own methodology.  Reputable analyst with a documented method.                 |
| `confidence=low` OR `type ∈ {estimate, derived, synthesized}`                       | `"synthesized_2026-04-28"`       | `"low"`           | Anything below the analyst tier — secondary aggregators, anecdotal creator/practitioner data, derived ratios, simulator seed defaults.  Add a `note` field carrying the original cited source.  |
| `flag:not_disclosed` AND the simulator architecturally requires a value             | `"synthesized_2026-04-28"`       | `"low"`           | Add `note: "Meta does not publicly disclose; modeled estimate"`.  See the paragraph below.                                                                                              |
| `flag:not_disclosed` AND the value is purely informational (no algorithm reads it)  | (omit from yaml entirely)        | (omit)            | Document the absence in the reference doc, but do not invent a yaml value.  M6 may still record the absence as a note in the relevant baseline file.                                     |
| `flag:stale` (value last refreshed >12 months ago)                                  | (preserve the original tier)     | (preserve)        | Keep the value, but add `note: "value last refreshed [date]; flagged for re-pull"`.  The `curation/` Layer 5 agent (M13) prioritises stale values on the next refresh.                  |

### What to do when Meta has not disclosed a value the simulator needs

Most calibration-anchor values that Meta hasn't published — Reels-specific
prevalence, Reels-vs-Feed eCPM ratio, Reels Gini coefficient,
crash-free session rate, p95 cold-start, the connected/unconnected
ranking pool mix — are still architecturally required: an algorithm
or a calibration test reads them.  In every such case the rule is the
same.  Use `provenance: "synthesized_2026-04-28"` and `confidence: "low"`,
attach a `note` field that names the closest available proxy and the
reasoning ("Meta does not publicly disclose; modeled from <proxy>;
calibration test_<n> tunes this"), and pick a defensible default within
the industry-implied range from the reference doc.  Never invent a
provenance string outside the registered set, and never raise confidence
above "low" to make a missing value look better than it is — the
audit will see through it and the calibration suite is the thing that
actually validates the choice.
