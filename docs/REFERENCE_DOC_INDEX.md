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

---

## 3. Special-tag handling

The reference doc uses four conventional tags to mark values that need
non-default handling.  Each maps to a specific yaml-author obligation
below.

### `flag:not_disclosed`

The tag means Meta (or the relevant competitor) has not publicly
disclosed the value.  The reference doc records the absence and lists
the best available proxy.  Examples: §3.1 IG DAU/MAU ratio (derived
from a qualitative quote, not disclosed officially); §4.2 Reels-vs-Feed
eCPM efficiency ratio (Meta has stopped publishing the ratio); §4.6
Reels-specific violating prevalence; §4.8 creator Gini coefficient; §4.9
crash-free session rate and p95 cold-start; §5.3 Spotlight MAU.  When
a yaml file encounters such a value: if the simulator architecturally
requires it (an algorithm or calibration test reads it), fill the
`{value, source, provenance, confidence}` block with the synthesized
defaults per §2 of this index plus a `note` field of the form
`"Meta does not publicly disclose; modeled from <proxy>"`; if the value
is purely informational and no code reads it, omit it from the yaml
entirely.  Never raise confidence above "low" for a not_disclosed value.

### `flag:stale`

The tag means the value's source last refreshed it more than 12 months
ago and the upstream publisher has not re-validated since.  Example:
§4.1 "Daily Reels plays (IG + FB combined) ~200B+", last refreshed by
Meta in mid-2023.  When a yaml file encounters such a value: keep the
value and its original `provenance` and `confidence` (a stale public
statement is still a public statement), and add a `note` field of the
form `"value last refreshed [original date]; flagged for re-pull"`.
The Layer-5 curation agent (M13) prioritises stale values on the next
refresh pass.  Calibration tests should not anchor on stale values
without an additional sanity check.

### `⚠️ correction`

The tag means the v2 reference doc revises a value that was wrong in v1.
Examples: §1.1 family DAP corrected from 3.35B → 3.58B (per Meta Q4
2025 press release); §1.3 2026 capex guidance corrected from $114–118B
→ $115–135B.  When a yaml file encounters a corrected value: persist
ONLY the corrected value, with the original-document provenance.  Do
not preserve the prior value as an alternate field.  The §0 critical-
calibration-notes block in the reference doc is the audit trail; that
is sufficient — the yaml should not litter itself with retracted
numbers.  Authors reviewing a diff against an earlier yaml should
expect a value change here; the `_meta.last_updated: "2026-04-28"` is
the cue that a correction landed.

### `interview_pitfall`

The tag flags a class of mistake that interview candidates commonly
make and that the simulator's question generation and answer evaluation
must surface.  Examples: §2 denominator differences (Meta has 65% of
social ad spend OR 27% of digital ad spend depending on the universe);
§6.2 view-definition asymmetry between IG / FB / Shorts / TikTok; §8.3
the 25/30/25/20 rubric weights are a community estimate, not Meta-
published.  When a yaml file encounters such a value: persist the value
normally, but the file's owning layer must surface the pitfall in the
appropriate place — `interview/rubric/meta_at_rubric.yaml` (M17) for
rubric-weight pitfalls, `baselines/data/view_definition_asymmetry.yaml`
(M6) for view-definition pitfalls, and `tools/baseline_tools.py` (M18)
for denominator-asymmetry pitfalls.  The interview question gen and
evaluator (M17) read these markers and inject explicit candidate
warnings into question text and rubric scoring.

---

## 4. Verbatim-quote extraction list

Every Meta-side direct quote in the reference doc, with the file that
will persist it.  Quotes are reproduced verbatim — punctuation,
ellipses, and en-dashes preserved.  Competitor-side or non-Meta quotes
(EU EVP Henna Virkkunen §7.1; Aakash Gupta §8.1; IGotAnOffer §8.3;
TeamYouTube §6.1; TikTok Ads Help §6.1) are intentionally omitted —
they are not Mosseri / Susan Li / Zuckerberg / Meta spokesperson and
so fall outside the section's scope.

| Speaker                  | Quote (verbatim)                                                                                                                                                                                                                                                                                                                                                                                                                            | Target file                                                                                                                                              |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Susan Li (§1.7)          | "We expect first quarter 2026 total revenue to be in the range of $53.5–56.5 billion. Our guidance assumes foreign currency is an approximately 4% tailwind to year-over-year total revenue growth."                                                                                                                                                                                                                                          | `baselines/data/family_scale.yaml` (Q1 2026 guidance anchor) + `interview/rubric/meta_at_rubric.yaml` (M17 question gen)                                 |
| Mark Zuckerberg (§3.1)   | "just shy of 2 billion"                                                                                                                                                                                                                                                                                                                                                                                                                       | `baselines/data/instagram_platform.yaml` (IG DAU qualitative anchor; the 0.65 DAU/MAU ratio is derived from this quote)                                  |
| Mark Zuckerberg (§4.2)   | "over $50B annual run rate"                                                                                                                                                                                                                                                                                                                                                                                                                   | `baselines/data/reels_monetization.yaml` (Reels run-rate anchor — §0 item 3 pins this through 2026-04-28)                                                |
| Meta spokesperson (§4.7) | "We have fixed an error that caused some users to see content in their Instagram Reels feed that should not have been recommended. We apologize for the mistake."                                                                                                                                                                                                                                                                            | `baselines/data/reels_integrity.yaml` (Feb 26 2025 incident apology) + referenced by `calibration/tests/test_03_integrity_incident.py` (M11)             |
| Adam Mosseri (§6.1)      | "Sends, reach, and views are the most important metrics for anybody trying to understand how their content is doing on Instagram."                                                                                                                                                                                                                                                                                                            | `baselines/data/view_definition_asymmetry.yaml` (M6 — anchor for the metrics-priority guidance Reels creators receive)                                   |
| Meta product copy (§6.1) | "Views will measure the number of times a reel started to play or replay and the number of times a non-reel appeared on a person's screen."                                                                                                                                                                                                                                                                                                  | `baselines/data/view_definition_asymmetry.yaml` (M6 — IG unified Views definition, effective 2025-04-21)                                                 |
| Meta spokesperson (§7.3) | "Teens under 18 will be automatically placed into an updated 13+ setting, and they won't be able to opt out without a parent's permission. Just like you might see some suggestive content or hear some strong language in a movie rated for ages 13+, teens may occasionally see something like that on Instagram, but we're going to keep doing all we can to keep those instances as rare as possible."                                  | `interview/rubric/meta_at_rubric.yaml` (M17 — Teen Accounts April 2026 expansion context for regulatory-tradeoff questions)                              |
| Meta spokesperson (§7.3) | "We didn't work with the MPA when updating our content settings… they're not endorsing or approving our content settings."                                                                                                                                                                                                                                                                                                                    | `interview/rubric/meta_at_rubric.yaml` (M17 — MPA settlement disclaimer accompanying the "13+ content rating" framing)                                   |
| Adam Mosseri (§10)       | "The top three signals that matter most for ranking are watch time, likes and sends. So when looking at your insights, pay close attention to average watch time, likes per reach, and sends per reach. Likes are slightly more important for connected content, and sends are slightly more important for unconnected content."                                                                                                            | `params/ranking_weights.yaml` (anchor — the three Mosseri-grounded weights `watch_time`, `like_rate_connected`, `send_rate_unconnected` carry provenance "public_statement" / confidence "high") |

---

## 5. Calibration anchor list

The six calibration tests in §11 of the reference doc each anchor on a
small set of numeric values pulled from elsewhere in the doc.  When a
test fails in M11, the table below tells you which reference-doc values
may have drifted (or been misingested) and need re-pulling on the
next curation refresh.  Inequalities and tolerances are reproduced from
§11 verbatim and copied without loosening into `config/calibration_config.json`.

| Calibration test                | Anchor values from reference doc                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Reference doc section                                  |
|---------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| `test_01_time_share_growth`     | • Reels share of US IG time **37% → 46%** (Sensor Tower) — drives the ramp setup. <br/> • Q4 2025 **ad impressions +18% YoY** — expected-band anchor. <br/> • Q4 2025 **price per ad +6% YoY** — expected-band anchor. <br/> • Susan Li Q4 2025: Reels US watch time **+30%+ YoY** — corroborating signal. <br/> Expected bands: IG ad revenue Δ ∈ [+15%, +25%] YoY; revenue per impression Δ ∈ [−15%, −8%].  Likely failing-param fix: `params/monetization_curves.yaml` Reels-vs-Feed efficiency ratio.                                                                                                  | §4.1 (37→46), §1.5 (+18%, +6%), §11.1                  |
| `test_02_ad_load_ramp`          | • Reels share of IG ad placements **35% → 53%** (Sensor Tower) — drives the ramp setup; aligns with §0 item 5. <br/> • Tinuiti Q2 2025 US IG impression mix **Stories 44% / Feed 31% / Reels 21%** — corroborating mix. <br/> • Reels share of Q2 2025 US IG ad impressions **21% (vs 13% Q2 2024)** — corroborating slope. <br/> Expected bands: skip rate Δ ∈ [+12%, +20%]; session length Δ ∈ [−15%, −8%].  Likely failing-param fix: `params/monetization_curves.yaml` `ad_load_to_skip_elasticity`.                                                                                                  | §4.2 (35→53; impression mix; 21% vs 13%), §11.2        |
| `test_03_integrity_incident`    | • Feb 26 2025 Reels incident — qualitative spike + apology framing only.  Per §0 item 9 the test MUST NOT use a specific peak prevalence number — Meta never published one. <br/> • FB violent & graphic prevalence **0.15–0.16%** (down from 0.19–0.20%) — baseline reference. <br/> • IG adult nudity prevalence **0.09–0.11%** — baseline reference. <br/> • Enforcement precision **<0.1%** removed-incorrectly. <br/> • Bullying & harassment proactive detection **declined ~20%** over last 3 quarters. <br/> Setup: spike violating_prevalence from 0.03% baseline to **5× baseline for 48 hours**, then restore.  Expected: circuit-breaker fires within 24 hours; brief freeze-in-horror watch-time spike; trust recovery within 14 days.  Likely failing-param fix: `params/guardrail_thresholds.yaml` integrity ceiling or `params/integrity_dynamics.yaml` `organic_decay_per_day`. | §4.6 (prevalence baselines), §4.7 (incident), §11.3, §0 item 9 |
| `test_04_gem_lattice`           | • Susan Li Q4 2025 earnings call: GEM-on-Facebook-Reels rollout **lifted Reels conversions ~3%** — drives the +3% conversion lift step. <br/> • Q4 2025 **ad impressions +18% YoY** and **price per ad +6% YoY** — corroborating ad-economics context. <br/> Expected bands: conversion rate Δ ∈ [+2.5%, +3.5%]; ROAS Δ ∈ [+2%, +5%].  Likely failing-param fix: `params/monetization_curves.yaml` `conversion_lift_propagation`.                                                                                                                                                                          | §11.4 (lift), §1.5 (ad economics)                      |
| `test_05_creator_bonus_pause`   | • Reels Play Bonus pause — industry observation; no Meta-disclosed magnitude. <br/> • Snap Spotlight Rewards **ended Jan 31, 2025** (replaced by Snap Monetization Program Feb 1, 2025) — directionally analogous precedent. <br/> • YouTube Shorts Fund end — similar pattern. <br/> • HypeAuditor tier distribution **micro 27.7% → ~33%; mid-tier 6.4%** — defines the "10K–100K" cohort the test pauses earnings for. <br/> • 2025 FB creator payouts **~$3B (+35% YoY)**, ~60% to Reels — sets the magnitude scale. <br/> Setup: drop mid-tier (10K–100K) creator earnings to 0 for 90 days.  Expected band: micro-creator posting frequency Δ ∈ [−30%, −15%].  Likely failing-param fix: `params/creator_economics.yaml` `earnings_to_posting_elasticity`. | §4.3 (FB payouts), §4.8 (tier distribution), §5.3 (Snap Spotlight Rewards end), §11.5 |
| `test_06_competitor_parity`     | Non-simulation test — reads `baselines/data/competitors_*.yaml` and asserts: <br/> • TikTok global ad revenue 2025 **within $30–35B** — §5.1.1. <br/> • TikTok US daily time spent **within 50–55 min** — §5.1.2. <br/> • TikTok Shop GMV 2025 **within $60–70B global** — §5.1.3. <br/> • YouTube Shorts daily views **≥ 200B** — §5.2. <br/> • Snap MAU **≥ 940M** (Q4 2025: 946M, +6% YoY) — §5.3. <br/> • `baselines/data/view_definition_asymmetry.yaml` present, non-empty, and includes the §6.2 mandatory injection warning text — §6.2.                                                            | §5.1.1, §5.1.2, §5.1.3, §5.2, §5.3, §6.2, §11.6        |
