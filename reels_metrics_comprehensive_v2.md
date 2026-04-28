# reels_metrics_comprehensive_v2.md

**Snapshot date:** 2026-04-28
**Prior snapshot:** 2026-02-01
**Refresh cadence:** quarterly (next: 2026-07-15, after Q2 2026 earnings)
**Owner:** external_facts curation
**Purpose:** canonical source for `params/`, `baselines/data/`, and calibration acceptance criteria. Every numeric value carries a `source` and a `confidence` tier. The simulator never reads this file at runtime — `M5` writes `params/*.yaml` and `M6` writes `baselines/data/*.yaml` from this document.

---

## Document conventions

Every numeric value below is followed by a citation in this format:

> `value` — *source name* (`source_url`), accessed YYYY-MM-DD, type=`{earnings|sec_filing|industry|public_statement|estimate|derived|synthesized}`, confidence=`{high|medium|low}`

**Confidence tiers:**

- **high** — primary source (SEC filing, Meta IR, Mosseri verified quote, Meta Transparency Center, EU Commission press release).
- **medium** — reputable industry analyst (eMarketer, Sensor Tower, DataReportal, Tinuiti, Pew, Sprout Social, Backlinko) reporting their own methodology.
- **low** — secondary aggregator citing primary or analyst, or anecdotal creator/practitioner data without methodology.

**Special tags:**
- `⚠️ correction` — value differs from prior snapshot and has been corrected here.
- `flag:not_disclosed` — Meta/competitor has not publicly disclosed; document records the absence and the best available proxy.
- `flag:stale` — value last refreshed >12 months ago by source; flagged for re-pull.
- `interview_pitfall` — common candidate trap; surface in question-gen and evaluator prompts.

---

## 0. Critical calibration notes for the simulator

These are the items most likely to trip up the calibration suite (`M11`) or surface as interview gotchas:

1. **Family DAP corrected to 3.58B** (was 3.35B in v1). The 7% YoY growth is the headline tempo number Meta publishes. *Source: Meta Q4 2025 press release.*
2. **2026 capex guidance corrected to $115–135B** (was $114–118B in v1). Midpoint ~$125B, roughly 2× FY 2025 actual of $72.2B. *Source: Meta Q4 2025 press release.*
3. **Reels run rate is still "over $50B annual run rate"** from Zuck's Q3 2025 (Oct 29, 2025) call. Q4 2025 did NOT refresh this number. Treat as the most recent anchor through April 28, 2026; Q1 2026 earnings on April 29 may refresh.
4. **TikTok US divestiture is CLOSED** (Jan 22, 2026 — TikTok USDS Joint Venture LLC). Retire any "pending divestiture" framing. ByteDance retains 19.9%; Oracle + Silver Lake + MGX hold ~45%.
5. **Reels share of IG ads moved 35% → 53%** between Q4 2024 and Q4 2025. The deltas the simulator will calibrate against use the 53% endpoint.
6. **Reels share of US IG time moved 37% → 46%** over the same window (Sensor Tower).
7. **The interview rubric weights 25/30/25/20 are a community estimate, NOT Meta-published.** Surface this caveat in the rubric file. The four pillars (Goals, Metrics, Debugging, Trade-off) are confirmed across all sources.
8. **View definitions across IG / YouTube Shorts / TikTok harmonized in March–April 2025** to "play started, no minimum watch time." Cross-period comparisons spanning that boundary are methodologically broken — `view_definition_asymmetry.yaml` is mandatory injection on every competitor comparison.
9. **The Feb 26, 2025 Reels integrity incident is a calibration anchor** for `test_03_integrity_incident`. Use prevalence spike framing, not a specific peak number — Meta never published one.
10. **Meta does not publish per-Reels engagement metrics** (skip rate, completion rate, send rate, save rate at the platform level). All such values are industry estimates and must be marked `confidence=low` or `medium`.

---

## 1. Meta family-level financials

### 1.1 Daily Active People (DAP)

- **3.58 billion** (Dec 2025 average), +7% YoY — *Meta Q4 2025 press release* ([investor.atmeta.com](https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Fourth-Quarter-and-Full-Year-2025-Results/default.aspx)), accessed 2026-04-28, type=earnings, confidence=high. ⚠️ correction (was 3.35B in v1).

### 1.2 Revenue

- **FY 2025 revenue:** $200.97B, +22% YoY — first time over $200B — *Meta 8-K* ([SEC filing](https://www.sec.gov/Archives/edgar/data/0001326801/000162828026003832/meta-12312025xexhibit991.htm)), accessed 2026-04-28, type=sec_filing, confidence=high.
- **Q4 2025 revenue:** $59.89B, +24% YoY — *same source*, type=earnings, confidence=high.
- **Q1 2026 revenue:** NOT YET REPORTED at snapshot date. Release April 29, 2026. Meta guidance: $53.5–56.5B; analyst consensus ~$55.6B. flag:not_disclosed (will refresh post-earnings).
- **Q4 2025 Family of Apps revenue:** $58.94B, +24.6% YoY (98.4% of total) — *Meta 8-K*, confidence=high.
- **Q4 2025 FoA operating income:** $30.77B, +51% YoY — *Meta 8-K*, confidence=high.
- **Operating margin:** 41% Q4 2025; 41% FY 2025 (Q4 compressed ~700 bps YoY due to R&D ramp) — *Meta 8-K*, confidence=high.

### 1.3 Capex

- **FY 2025 actual:** $72.22B (Q4 alone $22.1B) — *Meta Q4 2025 press release*, confidence=high.
- **2026 guidance:** $115–135B, midpoint ~$125B (~2× 2025 actual) — *Meta Q4 2025 press release*, confidence=high. ⚠️ correction (was $114–118B in v1).

### 1.4 Reality Labs

- **Q4 2025 operating loss:** $6.02B; **FY 2025 operating loss:** $19.19B; cumulative since 2020 ~$80B — *Meta 8-K*, confidence=high.

### 1.5 Q4 ad metrics

- **Ad impressions:** +18% YoY — *Meta Q4 2025 press release*, confidence=high.
- **Price per ad:** +6% YoY — *same source*, confidence=high.

### 1.6 Headcount

- **78,865** as of Dec 31, 2025, +6% YoY — *Meta 8-K*, confidence=high.

### 1.7 Verbatim guidance quote (anchor for question-gen)

> "We expect first quarter 2026 total revenue to be in the range of $53.5–56.5 billion. Our guidance assumes foreign currency is an approximately 4% tailwind to year-over-year total revenue growth."
> — Susan Li, Meta Q4 2025 earnings call, January 28, 2026 ([transcript](https://s21.q4cdn.com/399680738/files/doc_financials/2025/q4/META-Q4-2025-Earnings-Call-Transcript.pdf))

---

## 2. Social and digital advertising market share

Multiple sources report different denominators. The simulator should surface methodology when comparing.

### 2.1 Q3 2025 social ad spend (Sculpt compilation from platform earnings)

- Meta: **65.8%** ($46.56B; +21% YoY) — *Sculpt* ([wearesculpt.com](https://wearesculpt.com/blog/share-of-social-media-ad-spend-q2-2025/)), accessed 2026-04-28, type=industry, confidence=medium.
- YouTube: **13.9%**
- TikTok: **~11.7%** (estimate, ByteDance private)
- LinkedIn: **6.5%**

### 2.2 2025 social ad revenue (Omdia, June 2025)

- Meta (FB+IG combined): **~70%** of social media ad revenue — *Hollywood Reporter / Omdia* ([source](https://www.hollywoodreporter.com/business/business-news/meta-social-media-ad-revenue-70-percent-facebook-instagram-1236563625/)), accessed 2026-04-28, type=industry, confidence=medium.
- Top 4 (FB, IG, YouTube, TikTok): >90% of social ad revenue.

### 2.3 2026 global digital ad spend forecast (eMarketer, Nov 2025)

- Meta: **26.8%** ($243.46B) — surpasses Google for first time — *Marketing Dive / eMarketer* ([source](https://www.marketingdive.com/news/meta-to-surpass-google-in-digital-ad-revenue-for-first-time-emarketer/817384/)), accessed 2026-04-28, type=industry, confidence=medium.
- Google: 26.4%
- ByteDance (TikTok+): 7.9%
- Microsoft (LinkedIn ~half of 2.1%): ~1.05%

### 2.4 US video ads 2025 (eMarketer)

- Meta: 30.1% ($25.34B); YouTube: 8.3%; TikTok: 6.5% — *eMarketer*, type=industry, confidence=medium.

**interview_pitfall:** denominator differences. "Meta has 65% market share" and "Meta has 27% market share" can both be correct depending on whether the universe is global social ad spend or global digital ad spend.

---

## 3. Instagram platform metrics

### 3.1 User base

- **MAU: 3.0 billion** (announced Mosseri / Zuckerberg) — *TechCrunch* ([source](https://techcrunch.com/2025/09/24/instagram-now-has-3-billion-monthly-active-users-will-test-features-to-help-users-control-their-feeds/)), accessed 2026-04-28, type=public_statement, confidence=high. Date of announcement: 2025-09-24.
- **DAU qualitative:** "just shy of 2 billion" — Zuck, Q4 2025 call, 2026-01-28, type=earnings, confidence=high. derived DAU/MAU ≈ 0.65.
- **DAU/MAU ratio:** flag:not_disclosed officially. The 0.65 figure is derived from the qualitative quote.

### 3.2 Time spent

- **Average daily time spent:** ~33.1 minutes (US adults) — *Cropink / eMarketer* ([source](https://cropink.com/time-spent-on-instagram-statistics)), accessed 2026-04-28, type=industry, confidence=medium.
- **By generation (US):** Gen Z 53 min, Millennials ~35 min, Gen X 28 min, Boomers 20 min — *eMarketer*, type=industry, confidence=medium.

### 3.3 Ad revenue (eMarketer estimates; Meta does not separately disclose IG)

- **2025 IG ad revenue:** ~$71B — *eMarketer / BusinessTats* ([source](https://businesstats.com/instagram-statistics/)), type=estimate, confidence=medium.
- **Alternative compilation:** $83.6B global / $37.1B US — *Quantumrun citing eMarketer*, type=estimate, confidence=low (variance with above).
- **Share of Meta ad revenue:** ~37% global; ~50% US — *Vidico / BusinessTats*, type=estimate, confidence=medium.

### 3.4 Top regions (DataReportal April 2026, sourced from Meta ad-tools API)

- **India: 392M** users — *DataReportal* ([source](https://datareportal.com/essential-instagram-stats)), accessed 2026-04-28, type=industry, confidence=medium.
- **US: 172M**
- **Brazil: 141M**
- **Indonesia: 100–109M**

Note: alternative aggregators (Backlinko, Sprout Social) cite India 472–480M; methodology differs. Use DataReportal as primary baseline.

---

## 4. Reels product metrics

### 4.1 Time and engagement share (calibration anchors)

- **Reels share of US IG time:** 46% in 2025 (vs 37% in 2024) — *Sensor Tower via CNBC* ([source](https://www.cnbc.com/2026/01/20/most-of-instagrams-ads-ran-on-reels-in-2025-data-shows.html)), accessed 2026-04-28, type=industry, confidence=medium.
- **Reels watch time growth (US):** +30%+ YoY in Q4 2025 — *Susan Li, Q4 2025 earnings call*, type=earnings, confidence=high.
- **Daily Reels plays (IG + FB combined):** ~200B+ — *Meta, mid-2023*, last refreshed July 2023, flag:stale.
- **Daily reshares:** ~4.5B — *DemandSage / Vidico* ([source](https://www.demandsage.com/instagram-reel-statistics/)), type=estimate, confidence=low.
- **FB same-day Reels surfacing (Q4 2025):** +25% QoQ — *Q4 transcript*, confidence=high.
- **Original-content share of IG recommendations (US, Q4 2025):** 75%, +10pp in Q4 — *Q4 transcript*, confidence=high.
- **Edits-app share of viewed Reels:** ~10% of daily Reels views, ~3× QoQ — *Q4 transcript*, confidence=high.

### 4.2 Monetization (calibration anchors)

- **Reels run rate:** "over $50B annual run rate" — *Zuck, Q3 2025 earnings call, 2025-10-29* ([Tubefilter](https://www.tubefilter.com/2025/10/30/meta-reels-ad-revenue-q3-2025-earnings-report/)), type=earnings, confidence=high. NOT refreshed in Q4 2025 call.
- **Share of IG ads on Reels (Q4 2025):** **53%** (was 35% in Q4 2024) — *Sensor Tower via CNBC Jan 2026*, type=industry, confidence=medium.
- **Q2 2025 US IG impression mix (Tinuiti):** Stories 44% / Feed 31% / Reels 21% — *Tinuiti via eMarketer* ([source](https://www.emarketer.com/content/instagram-powered-by-reels-ascends-paid-social-media-throne)), type=industry, confidence=medium.
- **Reels share of Q2 2025 US IG ad impressions:** 21% (vs 13% Q2 2024) — *same source*.
- **Monetization efficiency (Reels eCPM ÷ Feed eCPM):** ~85–95% (estimate; gap is narrowing) — *eMarketer Jan 2026; Tinuiti commentary*, type=estimate, confidence=low. flag:not_disclosed (Meta has stopped publishing the ratio).
- **Meta blended advertiser CPM:** $6.59 (Oct 2025) — *Gupta Media* ([source](https://www.guptamedia.com/social-media-ads-cost)), type=industry, confidence=medium.
- **Reels CPM Tier-1 (US/UK/AU):** $10–$23; finance/SaaS $20+ — *Adamigo 2026* ([source](https://www.adamigo.ai/blog/meta-ads-cpm-cpc-benchmarks-by-country-2026)), type=estimate, confidence=low.
- **Reels CPM Tier-2 (LatAm/India/SEA):** $1–$5 — *same*, confidence=low.

### 4.3 Creator economics

- **Creator revshare on legacy in-stream ads:** 55% creator / 45% Meta — *widely cited industry; not formally documented by Meta as a fixed %*, type=estimate, confidence=low.
- **IG Reels creator RPM (Bonuses tier):** $0.01–$0.05 per 1K views — *Buffer, Flowgent, Zeely*, type=estimate, confidence=low.
- **FB Reels creator RPM (CMP, post-Aug 2025):** $0.02–$0.50 baseline; finance niche up to $0.90 — *Rowfix* ([source](https://rowfix.com/benchmarks)), type=industry, confidence=low.
- **2025 FB creator payouts:** ~$3B (+35% YoY); ~60% to Reels content — *Meta announcement via CNBC, March 18, 2026* ([source](https://www.cnbc.com/2026/03/18/meta-creator-pay-instagram-tiktok-youtube-facebook.html)), type=public_statement, confidence=high.
- **Creators earning >$10K/yr on FB (2025):** +30% YoY — *same source*.

### 4.4 Engagement micro-metrics (industry estimates; Meta does not publish)

All values below are confidence=low unless noted. The simulator should treat these as default `params/segment_propensities.yaml` seed values, not as anchors.

- **Average engagement rate (likes+comments / reach):** 1.23% overall (declined ~24% YoY) — *Vidico 2025*.
- **Engagement by account size:** <5K: 3.79%; 10–50K: 3–4%; 250K+: 1.5–2% — *Sprout / Social Insider*.
- **First-3-second drop-off:** ~50% of viewers churn; 70% if hook is poor — *TrueFuture / Hootsuite*.
- **Captioned vs uncaptioned completion:** ~65% vs ~37% — *industry*.
- **Engagement by length:** 15–30s: 4.8%; 45+s: 2.9%; <15s ~72% completion vs ~46% longer — *Zebracat 2025*.
- **Send-to-like weighting:** sends weighted ~3–5× likes for unconnected reach — *derived from Mosseri statements*, type=derived, confidence=medium.
- **Daily Reels DM sends:** ~1B/day (~694K/min) — *Metricool 2025*.

### 4.5 Skip Rate (NEW Aug 2025)

Instagram introduced a creator-facing "Skip Rate" metric: % of viewers who skip within first 3 seconds. Replaces "View Rate." Plus retention chart. **No public benchmark for "good" rate** — Meta has not published targets. Source: *BabbleBoxx*, type=industry, confidence=medium.

### 4.6 Integrity (Meta Transparency Center, H1 2026)

Source: [transparency.meta.com](https://transparency.meta.com/reports/integrity-reports-h1-2026/), accessed 2026-04-28, type=public_statement, confidence=high.

- **Violent & graphic prevalence (Facebook):** 0.15–0.16% (down from 0.19–0.20%).
- **Adult nudity prevalence (Instagram):** 0.09–0.11% (up from 0.06–0.07%, methodology change).
- **Bullying & harassment proactive detection:** declined ~20% over last 3 quarters.
- **Enforcement precision:** <0.1% of content removed incorrectly.
- **Reels-specific prevalence:** flag:not_disclosed. Use IG platform-wide as proxy.

### 4.7 Feb 26, 2025 Reels integrity incident (calibration anchor for `test_03`)

**Date:** Feb 25–26, 2025 evening (US); Meta apology Feb 27, 2025.
**What happened:** Algorithmic failure surfaced graphic violent and (in some cases) sexual content in Reels feeds, including for users with Sensitive Content Control on highest setting.
**Meta verbatim apology (Feb 27, 2025):**

> "We have fixed an error that caused some users to see content in their Instagram Reels feed that should not have been recommended. We apologize for the mistake."

**Context:** Occurred ~1 month after Meta's Jan 2025 announcement to (a) end third-party fact-checking in favor of Community Notes, (b) scale back automated moderation to extreme violations only.
**Aftermath:** [CBS News April 2025 follow-up](https://www.cbsnews.com/news/instagram-violence/) found 600+ accounts pushing graphic violence still active with $1K+/day economics. No public root-cause technical explanation from Meta.
**Cataloged as:** OECD AI Incident #957.
**Calibration use:** Spike `violating_prevalence` for 48 hours → expect circuit-breaker fires, brief watch-time spike (freeze-in-horror), recovery within 14 days. Do NOT use a specific peak prevalence number — Meta never published one.

### 4.8 Creator concentration

- **Tier distribution % (HypeAuditor State of Influencer Marketing 2025):**
  - Nano (1K–10K): 65–76% (2023: 65.4%; 2024–25: 75.9%)
  - Micro (10K–50K, sometimes –100K): 27.7% → ~33%
  - Mid-tier (50K–500K): 6.4%
  - Mega/Celebrity (1M+): **0.23%**
  Source: [HypeAuditor](https://hypeauditor.com/state-of-influencer-marketing-2025/), accessed 2026-04-28, type=industry, confidence=medium.
- **Gini coefficient:** flag:not_disclosed. Industry-implied ~0.85–0.95 (extremely concentrated). Use 0.90 as default seed for `params/creator_economics.yaml`, type=estimate, confidence=low.

### 4.9 Reliability (no Meta disclosure)

- **Crash-free session rate:** flag:not_disclosed. Industry SLO benchmark: ≥99.7% — type=estimate, confidence=low.
- **p95 cold-start load time:** flag:not_disclosed. Industry SLO benchmark: ≤2.5s — type=estimate, confidence=low.

---

## 5. Competitor metrics

### 5.1 TikTok

#### 5.1.1 Financials

- **Global ad revenue 2025:** $32–33B (eMarketer ~$33B; WARC ~$32.4B, +24.5% YoY); 2024 baseline ~$23.6–26.4B — *Marketing Dive, Digiday/eMarketer*, accessed 2026-04-28, type=estimate, confidence=medium (ByteDance is private).
- **US ARPU 2024 (eMarketer):** $96.71/user; 2026 projected $130.31 — *ROI Revolution* ([source](https://roirevolution.com/blog/state-of-tiktok-advertising-in-2024/)), type=estimate, confidence=medium.
- **Global ARPU:** 2023 ~$11; 2024 ~$16 — *Miracuves* ([source](https://miracuves.com/blog/tiktok-revenue-model/)), type=estimate, confidence=low.

#### 5.1.2 Engagement

- **US daily time spent:** 52–53.8 min/day (eMarketer 2025; declining 6.9% YoY); projected 50 min in 2026 — *Digiday/eMarketer*, type=industry, confidence=medium.
- **Global daily time:** ~58 min (Electro IQ); ~95 min (DataReportal — methodology variance) — type=industry, confidence=low.
- **Daily uploads (global):** ~34M (~272/sec) — *Cool Nerds Marketing* ([source](https://coolnerdsmarketing.com/tiktok-statistics-2025/)), type=estimate, confidence=low.
- **Sessions/day:** 15–19; ~10.85 min/session — *AWISEE*, confidence=low.
- **Engagement rate:** 3.85–4.9% advertising; some sources 5–6% — *SQ Magazine*, confidence=low.

#### 5.1.3 TikTok Shop (Momentum Works data)

- **GMV 2025:** $64.3B global, +94% YoY — *DealStreetAsia* ([source](https://www.dealstreetasia.com/stories/tiktok-shop-gmv-2025-472662)), type=industry, confidence=medium.
- **US:** $15.1B, +68% YoY.
- **Indonesia:** $13.1B; SEA aggregate $45.6B, +100% YoY.

#### 5.1.4 Creator economics

- **Creator Rewards RPM (US):** $0.50–$1.00 per 1K **qualified views** (≥5s, FYP, unique viewer) — *Rowfix, Shortimize*, type=industry, confidence=low.
- **Pulse revshare:** 50% to creators on top 4% of videos in 12 categories (100K+ followers); active April 2026 — *Influencer Marketing Hub*, type=public_statement, confidence=medium.

#### 5.1.5 US divestiture status — IMPORTANT FOR INTERVIEW CONTEXT

**Status as of 2026-04-28: DEAL CLOSED. TikTok operating normally in US.**

- **Sept 25, 2025:** Trump signs fifth EO declaring framework a "qualified divestiture" under PAFACAA; deadline pushed to Jan 22–23, 2026.
- **Dec 18, 2025:** ByteDance signs binding agreement.
- **Jan 22, 2026:** **TikTok USDS Joint Venture LLC** established. Ownership: Oracle ~15%, Silver Lake ~15%, MGX (Abu Dhabi) ~15% (45% combined); existing ByteDance investors ~30%; ByteDance retains **19.9%** (capped below 20% statutory threshold). Algorithm licensed from ByteDance, retrained on US user data, hosted on Oracle Cloud. CEO of JV: Adam Presser; CSO: Will Farrell; Shou Chew on board.
- **March 17, 2026:** Sen. Mark Warner formally questions reported $10B Treasury fee.
- **March 3–4, 2026:** Major Oracle data-center outage in Ashburn VA, ~20 hours; second outage post-deal-close (first in late Jan 2026).

Sources: *Axios, CBS News, Variety, NBC, Almcorp*, accessed 2026-04-28, type=public_statement, confidence=high.

### 5.2 YouTube Shorts

- **Daily views:** **200B daily** (CEO Mohan, Cannes Lions June 2025; +186% YoY from 70B March 2024 — but **NOT apples-to-apples** due to view-def change) — *TheWrap, IMDb*, accessed 2026-04-28, type=public_statement, confidence=high (with methodology caveat).
- **Daily uploads:** ~12M; 910M+ total library — *AWISEE*, type=estimate, confidence=low.
- **Avg session length:** 14–18 min — *Loopex/Zebracat*, type=estimate, confidence=low.
- **YouTube total ad revenue 2024:** $36.15B — *Alphabet 10-K*, type=sec_filing, confidence=high.
- **YouTube ad revenue 2025:** $40.5B+ (Q4 2025: $11.38B, +8.7% YoY; Q3 2025: $10.26B, +15% YoY) — *Alphabet 8-K Q4 2025* ([SEC filing](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000012/googexhibit991q42025.htm)), type=sec_filing, confidence=high.
- **YouTube TOTAL revenue 2025 (ads + subs, first-time disclosure):** **>$60B with 325M+ paid subscriptions** (Pichai, Feb 4, 2026) — *Variety* ([source](https://variety.com/2026/digital/news/youtube-2025-total-revenue-ads-subscriptions-alphabet-earnings-1236652260/)), type=earnings, confidence=high.
- **Shorts RPM:** $0.01–$0.30 (most $0.04–$0.06); US avg ~$0.30 — *Influencer Marketing Hub, Mediacube*, type=estimate, confidence=low.
- **Shorts ad revshare:** **45% to creator / 55% YouTube** (covers music licensing); active since Feb 1, 2023 — *YouTube Help*, type=public_statement, confidence=high.
- **US TV viewing share (Nielsen Gauge):** **12.5% Jan 2026** (#1 for 11th consecutive month, up from 10.8% Jan 2025); 12.7% Feb 2026 but #2 due to Super Bowl + Winter Olympics — *MediaPost, Deadline*, type=industry, confidence=high.
- **Engagement rate:** ~5.91% (Loopex); watch rate 2.52%; comment rate 0.05%; retention 73% — type=estimate, confidence=low.

### 5.3 Snap Spotlight (Q4 2025, released Feb 4, 2026)

Source: [Snap 8-K Q4 2025](https://www.sec.gov/Archives/edgar/data/0001564408/000156440826000011/snapincq42025investorlet.htm), type=sec_filing, confidence=high.

- **Global DAU:** 474M (down 3M QoQ).
- **Global MAU:** 946M (+51M / +6% YoY; +3M QoQ).
- **DAU regional:** NA 94M (–1%); EU 98M (–1%); RoW 282M (+11% YoY).
- **Spotlight MAU:** flag:not_disclosed.
- **Spotlight share of content time:** flag:not_disclosed (qualitative: US Snapchatters posting to Spotlight +47% YoY; reposts/shares +69% YoY).
- **Time / impression growth:** Global impressions +14% YoY Q4; eCPMs –8% YoY.
- **Q1 2026 guidance:** Revenue $1.50–1.53B; Adj. EBITDA $170–190M.
- **Spotlight Rewards:** ENDED Jan 31, 2025; replaced by **Snap Monetization Program** (Feb 1, 2025). May 7, 2026 update: minimum 100 hr Spotlight View Time for max rewards. Source: *Snap newsroom, Snap help*, type=public_statement, confidence=high.

---

## 6. View definition asymmetry (mandatory injection)

**This section is the canonical source for `baselines/data/view_definition_asymmetry.yaml`. Every competitor comparison response MUST include the warning.**

### 6.1 Per-platform definitions

**Instagram (effective 2025-04-21 — Meta unification):**
> "Views will measure the number of times a reel started to play or replay and the number of times a non-reel appeared on a person's screen."

For Reels, triggers if the reel plays for **0.1 seconds** on screen. Self-views excluded. Replaces Impressions + Plays + Video Views with a single Views metric.

Mosseri verbatim:
> "Sends, reach, and views are the most important metrics for anybody trying to understand how their content is doing on Instagram."

Sources: *Metricool, SocialPilot, Emplifi docs, Zoomph*, accessed 2026-04-28, type=public_statement, confidence=high.

**Facebook:** Aligned with Instagram on the same April 2025 unified Views metric (*Kolsquare*); pre-change FB used 3-second video view standard with 50%+ visibility on mobile.

**YouTube Shorts (effective 2025-03-31):**
> "Views = each time a Short starts to play or replay, with no minimum watch time required."

Old metric **renamed "Engaged Views"** = "viewers who chose to continue watching past initial impression" (specific threshold undisclosed; historically a few seconds). **Critically: monetization and YPP eligibility (10M engaged Shorts views/90d) still tied to engaged views, NOT the new total.**

TeamYouTube verbatim:
> "We recognize that you want a deeper understanding of how your short-form videos are performing holistically, including when you're posting across multiple platforms."

Sources: *TechCrunch, PPC Land, Sprout Social*, accessed 2026-04-28, type=public_statement, confidence=high.

**YouTube long-form (unchanged):** view = ≥30 sec watched (or ≥11 sec if video <30s); user-initiated; manual review at 301 views.

**TikTok:**
> "A view on TikTok is counted as soon as the video starts playing in someone's feed."

Each loop counts as a new view (excluding self-views). Ads use 1-sec 50%-visible impressions; "Focused View" = ≥6 sec or engagement. Creator Rewards "qualified views" are stricter: ≥5 sec, FYP only, unique viewer.

Source: *TikTok Ads Help*, accessed 2026-04-28, type=public_statement, confidence=high.

### 6.2 Why the asymmetry matters (interview_pitfall)

As of April 2026, all three short-video platforms now define a "view" as essentially "play started" with no minimum watch time — but the denominators differ massively, and the unification is recent enough that any cross-period comparison crossing March/April 2025 is methodologically broken. The 70B → 200B Shorts daily-views jump (March 2024 → June 2025) is partly methodological inflation.

**Other asymmetries:**

1. Autoplay vs click-to-play (vertical feed swipes generate involuntary plays often <100ms).
2. Loop counting now harmonized post-2025 but not historically.
3. IG carousel images can each register as a view, inflating IG totals against video-only platforms.
4. Only YouTube retains an "Engaged Views" parallel metric that approximates the quality-controlled view.
5. Monetization denominators differ — YouTube pays on engaged views, TikTok on qualified views, Instagram doesn't pay on views directly.

**Summing platform totals (e.g., 200B IG+FB + ~90B TikTok + 200B Shorts daily) overstates actual human attention by an estimated 30–50%+ in aggregate.** A more honest cross-platform metric is **watch-time minutes** or **engaged views**, not raw view counts.

**Mandatory injection text** (the simulator should append this verbatim to every competitor comparison response):

> ⚠️ View-definition asymmetry: Instagram, Facebook, YouTube Shorts, and TikTok all redefined "view" as "play started, no minimum watch time" between March and April 2025. Cross-period comparisons spanning that boundary are methodologically broken. Aggregate platform totals (e.g., summing daily views across IG/FB/Shorts/TikTok) overstate human attention by an estimated 30–50% due to loop counting, autoplay, and carousel inflation. When comparing platforms, prefer watch-time minutes or engaged views.

---

## 7. Regulatory and macro context (snapshot 2026-04-28)

### 7.1 EU Digital Services Act (DSA)

- **Open Meta proceedings:** (1) April 30, 2024 — deceptive ads, political content, CrowdTangle deprecation; (2) May 16, 2024 — protection of minors (rabbit-hole effects, behavioral addiction, age assurance).
- **Oct 24, 2025 preliminary findings** against Meta on Article 40 (researcher data access), Article 16 (Notice & Action), appeal mechanism, and "dark patterns."
- **EVP Henna Virkkunen verbatim:** "Our democracies depend on trust… The DSA makes this a duty, not a choice." [EC press release](https://digital-strategy.ec.europa.eu/en/news/commission-preliminarily-finds-tiktok-and-meta-breach-their-transparency-obligations-under-digital).
- **No final fine against Meta as of 2026-04-28.** X received €120M fine in Dec 2025; max DSA penalty is 6% of global turnover (~€9B for Meta).
- **2026-03-26:** EC opens formal DSA investigation into Snapchat (illicit sales, grooming).
- **April 2026:** DSA "second wave" VLOP designations expanded.

#### 7.1.1 Reels-specific Amsterdam ruling (Oct 2, 2025)

Rechtbank Amsterdam held "Reels does not comply because there is no non-profiling option for the Reels feed" and ordered persistent non-profiling feed across home/comments/Reels by Jan 1, 2026. Meta has appealed; oral hearing held Jan 26, 2026 at Gerechtshof Amsterdam; **decision pending as of April 2026.**
Sources: *EU Law Live, Cade Project*, type=public_statement, confidence=high.

### 7.2 UK Online Safety Act

- **March 12, 2026 — Ofcom + ICO joint letter** to Facebook, Instagram, TikTok, Snapchat, YouTube, Roblox demanding evidence on (1) age assurance, (2) stop strangers contacting children, (3) safer feeds, (4) rigorous testing. Deadline April 30, 2026; report May 2026. Source: [Ofcom March 2026 bulletin](https://www.ofcom.org.uk/online-safety/illegal-and-harmful-content/online-safety-industry-bulletins/online-safety-industry-bulletin-march-2026).
- **Timeline:** Categorisation register July 2026; final policy statements mid-2027; CSEA reporting duty in force April 7, 2026; risk-assessment requests May 1–July 31, 2026.
- **Max penalty:** £18M or 10% global turnover; senior-manager criminal liability up to 5 years prison.
- **No 2026 enforcement action against Instagram specifically yet.** X under investigation (Jan 2026); Telegram under investigation (early 2026).
- **UK "Growing Up in the Online World" consultation** open until May 26, 2026.
- **April 15, 2026:** UK Commons passes amendments enabling SoS regulations on social media features for children; bill returned to Lords April 27, 2026.

### 7.3 Meta Teen Accounts (April 2026 expansion)

- **2026-04-08/09:** Meta announced **international expansion** of revamped Teen Accounts with 13+ rating, rolling out India first then globally over coming months.
- **New "Limited Content" tier:** disables ability to see/leave/receive comments, applies stronger filters, further limits search.
- **More Content tier:** requires parental permission.
- **MPA settlement (effective 2026-04-15):** After cease-and-desist over PG-13 trademark, Meta agreed to "substantially reduce" PG-13 references and add disclaimer:
  > "We didn't work with the MPA when updating our content settings… they're not endorsing or approving our content settings."
  Now framed as "13+ content rating." Source: *Deadline*, type=public_statement, confidence=high.
- **Meta verbatim (2026-04-08):**
  > "Teens under 18 will be automatically placed into an updated 13+ setting, and they won't be able to opt out without a parent's permission. Just like you might see some suggestive content or hear some strong language in a movie rated for ages 13+, teens may occasionally see something like that on Instagram, but we're going to keep doing all we can to keep those instances as rare as possible."
- **Reels-specific teen guardrails:** stricter ranking/filtering across Reels; teens cannot follow/be followed by accounts that "regularly share age-inappropriate content"; search blocks for "gore", "alcohol"; AI experiences gated to 13+ tone; Sleep Mode 10pm–7am; 60-min daily reminder; under-16 can't go Live without parental permission.

### 7.4 US litigation and state laws

- **2026-04-10:** Massachusetts SJC: Section 230 does NOT shield Meta from state claims that Instagram was designed to addict children; motion-to-dismiss denied; case proceeds.
- **Virginia SB 854:** effective Jan 1, 2026 (1-hour daily limit for under-16).
- **Florida HB 3:** in force (11th Cir. stayed injunction Nov 2025).
- **Tennessee Public Chapter 899:** in force.
- **Utah HB 464:** enjoined.
- **California SB 976:** partially in force.
- **SCOTUS *Free Speech Coalition v. Paxton* (June 27, 2025):** signals more deferential review of age-verification laws.

### 7.5 Reels feature launches since Feb 1, 2026

| Feature | Date | Notes |
|---|---|---|
| **Reels native affiliate links** | Late March 2026 | Mosseri: "use affiliate links to tag products"; rollout US/BR/IN/ID/TH; Meta 0% commission on affiliate sales; checkout still off-platform |
| **"Shop the Look" AI auto-tagging (controversy)** | Feb 2026 | Tested AI product tagging without creator consent; backlash; pulled/revised |
| **Build Your 2026 Algorithm (English markets)** | Jan/Feb 2026 | Topic preference controls in Reels tab live globally for English users |
| **Trial Reels scheduling** | Q1 2026 | Schedule trial Reels in advance |
| **Instagram TV app (Google TV, Fire TV)** | Q1 2026 | Big-screen Reels; topic-based "channels" |
| **Short Drama (test)** | Q1 2026 | Mini-drama feature in testing (~$1.3B US TikTok-driven category) |
| **Edits app updates** | Ongoing 2026 | AI Style fonts, related-topic tags, account links, weekly idea suggestions, complex templates; ~10% of Reels viewed daily now created in Edits |
| **Instagram Plus subscription test** | Q1 2026 | Story tools paywall test in JP/MX/PH |
| **2× Reels playback speed** | Q1 2026 | Double-speed playback added |
| **5-hashtag cap** | Q1 2026 | Posts/Reels capped at 5 hashtags |
| **Creator Fast Track** | March 2026 | Cross-platform pay program: $1K–$3K/mo for 3 months for invited IG/TT/YT creators (100K+/1M+ followers) who post on Facebook |
| **DM E2EE sunset** | Announced for May 8, 2026 | E2EE in DMs being removed |

Sources: *Meta newsroom announcements; Mosseri Threads posts; PPC Land; Net Influencer; CNBC*, accessed 2026-04-28, type=public_statement, confidence=high.

---

## 8. Meta PM interview process (verified April 2026)

### 8.1 Timebox

- **Total interview length:** 45 minutes — *StellarPeers, Aakash Gupta, IGotAnOffer*, accessed 2026-04-28, type=industry, confidence=high.
- **Working minutes:** 35 — same sources.
- **Trade-off budget:** ~5 minutes at the end — *Aakash Gupta heuristic*: "It's an area that comes at the end… people often lose time for it. It's important not to. You don't want to get out a 0/5 on this rubric area due to time." type=public_statement, confidence=medium.

### 8.2 Four pillars (confirmed)

- **Goals**
- **Metrics**
- **Debugging**
- **Trade-off**

Confirmed across all sources. Specific weights are NOT publicly published by Meta.

### 8.3 Weights — community estimate (NOT Meta-published)

> ⚠️ **interview_pitfall:** the 25/30/25/20 weight split is a community estimate, not Meta-official. IGotAnOffer: "FAANG companies, including Meta, are very secretive about the exact details of their interview grading rubrics." Treat as best-available proxy and surface this caveat in `interview/rubric/meta_at_rubric.yaml`.

- Goals: 25%
- Metrics: 30%
- Debugging: 25%
- Trade-off: 20%

### 8.4 Frameworks (community-standard, not Meta-official)

- **GAME** (Goals): IGotAnOffer proprietary framework.
- **Define → Explore → Conclude** (Debugging): IGotAnOffer's three-step method.
- **RICE + Re-evaluate** (Trade-off): industry-standard from Intercom (2017).

All three are mainstream prep frameworks; none is Meta-published.

### 8.5 IC5/IC6 differentiation

- **IC5 (E5):** Senior PM, terminal level for ~85% of Meta PMs, owns problem space E2E.
- **IC6 (M1):** Staff/Lead PM, equivalent to Google L6 / Amazon L7. Reaching IC6 requires ~1+ year at IC5 plus broader scope (30+ engineers, full XFN).
- Sources: *Apt blog, Engineering Bolt, Blind*, type=industry, confidence=high.

### 8.6 NEW: Central Products PM track (2025/2026)

Meta has introduced a new loop variant for Central Products with:

1. **Product Sense with AI** ("vibe coding" — translate thinking into prompts to build a working prototype using AI tools)
2. **Analytical Thinking & Logical Reasoning**
3. **Product Architecture** (open-ended system design)
4. **Leadership & Drive**

Standard track unchanged. Source: *IGotAnOffer*, accessed 2026-04-28, type=industry, confidence=medium.

### 8.7 Difficulty levels (for `interview/rubric/meta_at_rubric.yaml`)

- **L4 (E4):** Junior/Mid PM. Typical scenarios: single-team scope, well-defined success metric, clear baseline.
- **L5 (E5):** Senior PM. Typical scenarios: multi-team coordination, ambiguous success metric, requires segmentation strategy.
- **L6 (M1):** Staff PM. Typical scenarios: cross-org strategy, ecosystem-level trade-offs (e.g., creator revenue vs platform integrity), regulatory dimensions.

---

## 9. Viewer segment benchmarks (modeled, not measured)

**Honest assessment:** the 5 specific behavioral segments (snackers, deep_watchers, social_sharers, shoppers, teens) do NOT have publicly available population-share data with these labels. No Pew, eMarketer, Sensor Tower, or GWI report segments Reels viewers along these axes. The values below are seed defaults for `params/segment_propensities.yaml` based on closest available proxies. Treat all as type=synthesized, confidence=low — except teen-specific values where Pew provides direct anchors.

### 9.1 Population shares (synthesized seed values)

Total population sums to 100%. Seed values for `params/segment_propensities.yaml`:

| Segment | Population share | Justification |
|---|---|---|
| snackers | 35% | Modeled from "average user 33 min/day" cohort with low session length |
| deep_watchers | 15% | Modeled from "Gen Z 53 min/day" power-user cohort |
| social_sharers | 20% | Modeled from "1B daily DM sends / 2B DAU" implied share-active cohort |
| shoppers | 10% | Modeled from Instagram Shop adoption + affiliate-link rollout markets |
| teens | 20% | Pew anchor: 55% of US teens use IG daily; 12% "almost constantly"; modeled as ~20% of daily-active base globally |

### 9.2 Skip thresholds (seed values)

| Segment | Skip threshold (sec) | Justification |
|---|---|---|
| snackers | 1.5 | Highest-impatience cohort; below industry 50% drop-off mark |
| deep_watchers | 8 | Watches longer-form Reels and finishes them |
| social_sharers | 3 | Skips faster than deep_watchers; shares more than they watch |
| shoppers | 4 | Pauses for product evaluation |
| teens | 2 | High volume / low patience; closer to snackers than deep_watchers |

### 9.3 Engagement propensities (seed values, multipliers vs platform avg)

| Segment | likes | sends | saves | comments | replays |
|---|---|---|---|---|---|
| snackers | 0.5× | 0.3× | 0.2× | 0.3× | 0.5× |
| deep_watchers | 1.5× | 1.0× | 1.5× | 1.5× | 2.0× |
| social_sharers | 1.0× | 3.0× | 0.5× | 1.5× | 0.8× |
| shoppers | 0.8× | 0.6× | 3.0× | 0.5× | 1.0× |
| teens | 1.2× | 2.5× | 0.7× | 1.8× | 1.3× |

### 9.4 Teen-specific anchors (Pew, public data, confidence=medium-to-high)

- **55% of US teens use IG daily; 12% "almost constantly"** (up from 8% in 2023) — *Pew Dec 9, 2025* ([source](https://www.pewresearch.org/internet/2025/12/09/teens-social-media-and-ai-chatbots-2025/)).
- **~20% of teens post daily** on IG/TikTok; ~30% on Snapchat — *Pew April 15, 2026* ([source](https://www.pewresearch.org/internet/2026/04/15/teens-experiences-on-tiktok-instagram-and-snapchat/)) — first comparative report across platforms.
- **51% of US teens 4+ hr/day on social media; 13–19 avg 4.8 hr/day; girls 5.3h vs boys 4.4h** — *Brighterly aggregation 2025*.
- **Teen "almost constantly" use:** TikTok 22%; YouTube 21%; IG 12%; Black teens 35% on YouTube — *Pew 2024*.

### 9.5 Teen integrity guardrail multiplier

- **Operational multipliers** (vs platform default): content filtering 2.0×; ad load reduction 0.8×; recommendation rabbit-hole damping 1.5×; Sensitive Content Control most-restrictive auto-on; Hidden Words strictest; Live restricted for under-16; DM restrictions to known-followers only.
- Source: *Meta Family Center, Meta newsroom April 2026*, type=public_statement, confidence=high.

---

## 10. Ranking signal weights (`params/ranking_weights.yaml`)

**Most recent verified Mosseri statement (Jan 22, 2025, IG video):**

> "The top three signals that matter most for ranking are watch time, likes and sends. So when looking at your insights, pay close attention to average watch time, likes per reach, and sends per reach. Likes are slightly more important for connected content, and sends are slightly more important for unconnected content."

Source: *Social Media Today, Hootsuite, Dataslayer*, type=public_statement, confidence=high.

### 10.1 Seed weights for `params/ranking_weights.yaml` (synthesized from Mosseri framework)

| Weight | Default | Rationale |
|---|---|---|
| `watch_time` | 0.40 | Mosseri's #1 signal |
| `like_rate_connected` | 0.20 | "slightly more important for connected" |
| `send_rate_unconnected` | 0.30 | "slightly more important for unconnected" |
| `skip_penalty_3s` | -0.25 | Aug 2025 Skip Rate metric exposure suggests real input |
| `quality_bonus` | 0.10 | Originality Score (Dec 2025) rewards original content |
| `repetition_penalty` | -0.30 | 60–80% reach drop on aggregator accounts (Dec 2025) |

### 10.2 Connected vs unconnected pool mix

- **Seed default:** 35% connected / 65% unconnected — *prior simulator estimate; Mosseri does not quote a number*, type=synthesized, confidence=low.
- **Alternative scenario:** 30% / 70% — *industry sites cite "60%+ of Reels views from non-followers"; Mosseri Sept 2025 framing implies unconnected share has likely increased*, type=synthesized, confidence=low.

The simulator should run calibration against both 35/65 and 30/70 to bracket sensitivity.

### 10.3 Originality and repetition (NEW Dec 2025 / Jan 2026)

- **Originality Score:** Instagram detects recycled clips. Reposting TikTok with watermarks tanks reach. Aggregator accounts saw 60–80% reach drops in Dec 2025 update; original creators saw 40–60% increases.
- **Repost cap:** Accounts posting 10+ reposts in 30 days excluded from Explore/Reels recommendations entirely.
- Source: *CreatorFlow 2026, Funnl*, type=industry, confidence=medium.

### 10.4 Mosseri Dec 31, 2025 year-end memo themes

Authenticity / "trust graph" shift; originality rewarded; profile-level signals over individual posts; AI content needs labels. Analysis: *Om Malik, Jan 1, 2026*.

---

## 11. Calibration acceptance criteria (`docs/CALIBRATION_SPEC.md` source)

The six tests that gate Phase-2 unlock. Pass criteria are explicit inequalities with default 20% tolerance.

### 11.1 test_01_time_share_growth

- **Setup:** ramp Reels share-of-IG-time from 37% → 46% over 12 months.
- **Expected:**
  - IG ad revenue delta: +15% to +25% YoY
  - Revenue per impression delta: -15% to -8%
- **Anchor sources:** Sensor Tower (37%→46%); Q4 2025 ad impressions +18% YoY / price per ad +6% YoY.
- **Likely failing-param fix:** `params/monetization_curves.yaml` Reels-vs-Feed efficiency ratio.

### 11.2 test_02_ad_load_ramp

- **Setup:** ramp Reels share of IG ad placements from 35% → 53% over 12 months.
- **Expected:**
  - Skip rate delta: +12% to +20%
  - Session length delta: -15% to -8%
- **Anchor sources:** Sensor Tower (35%→53%); Tinuiti impression-mix data.
- **Likely failing-param fix:** `params/monetization_curves.yaml` ad_load_to_skip_elasticity.

### 11.3 test_03_integrity_incident

- **Setup:** spike `violating_prevalence` from 0.03% baseline to 5× baseline for 48 hours, then restore.
- **Expected:**
  - Circuit-breaker fires within 24 hours
  - Watch time briefly spikes (freeze-in-horror) then crashes
  - Trust metric recovery within 14 days
- **Anchor sources:** Feb 26, 2025 Reels incident (no specific peak number — Meta never published).
- **Likely failing-param fix:** `params/guardrail_thresholds.yaml` integrity ceiling, or `params/integrity_dynamics.yaml` decay rate.

### 11.4 test_04_gem_lattice

- **Setup:** apply +3% conversion lift step (GEM rollout proxy).
- **Expected:**
  - Conversion rate delta: +2.5% to +3.5%
  - ROAS delta: +2% to +5%
- **Anchor sources:** Susan Li Q4 2025: "GEM-on-Facebook-Reels rollout lifted Reels conversions ~3%."
- **Likely failing-param fix:** `params/monetization_curves.yaml` conversion lift propagation.

### 11.5 test_05_creator_bonus_pause

- **Setup:** drop mid-tier (10K–100K) creator earnings to 0 for 90 days.
- **Expected:**
  - Micro-creator posting frequency delta: -30% to -15%
- **Anchor sources:** Industry observation post-Reels Play Bonus pause; Snap Spotlight Rewards end Jan 31, 2025; YouTube Shorts Fund end (similar pattern).
- **Likely failing-param fix:** `params/creator_economics.yaml` earnings-to-posting elasticity.

### 11.6 test_06_competitor_parity

Non-simulation test — reads `baselines/data/competitors_*.yaml` and asserts:

- TikTok global ad revenue 2025 within $30–35B
- TikTok US daily time spent within 50–55 min
- TikTok Shop GMV 2025 within $60–70B (global)
- YouTube Shorts daily views ≥ 200B
- Snap MAU ≥ 940M
- `view_definition_asymmetry.yaml` present, non-empty, and includes warning text

---

## 12. Document refresh log

| Date | Action | By |
|---|---|---|
| 2026-02-01 | v1 snapshot | curation |
| 2026-04-28 | v2 refresh — Q4 2025 actuals; TikTok USDS deal close; Teen Accounts global expansion; Ofcom+ICO joint letter; Reels affiliate links; Creator Fast Track; corrections to DAP and capex; new ranking signals (Originality, Skip Rate); Pew first comparative teen report | curation |
| 2026-07-15 (planned) | v2.1 refresh — Q1 + Q2 2026 earnings; Amsterdam Reels appeal decision; Ofcom April 30 deadline outcome; UK age-assurance enforcement | scheduled |

---

## 13. Citation discipline summary

**Reported actuals (high confidence):** Meta DAP, FY/Q4 revenue, FoA, capex, opex; Snap DAU/MAU; YouTube ad revenue; Pew teen percentages; TikTok USDS deal terms; view-definition changes; Meta Transparency Center prevalence rates; Mosseri verbatim quotes.

**Industry-analyst estimates (medium confidence; Meta does not disclose):** Reels eCPM, monetization efficiency ratio, Reels run rate beyond Q3 2025 ($50B), Reels-vs-Feed gap, IG ad revenue, social-media ad market share %, time-spent figures, daily Reels plays/reshares since 2023, TikTok ad revenue and ARPU.

**Anecdotal / creator-reported (low confidence):** All RPM ranges (IG, FB, YouTube Shorts, TikTok), engagement micro-rates, ad load %, content lifespan, swipe-through rates, comments per video.

**Pure estimates / no public data:** Reels Gini coefficient (~0.85–0.95), crash-free session rate, p95 load time, FB-vs-IG split of 200B daily Reels plays, connected/unconnected ranking pool mix (35/65 or 30/70 alternative), 5 viewer behavioral segments (snackers/deep_watchers/etc.) and their population shares.

**Unverified industry repetition (treat as stale):** "55% creator share on Reels" — widely cited but no Meta primary documentation found; the 200B+ daily Reels plays figure has not been refreshed by Meta since Q2 2023.

**Re-pull priority for April 30, 2026+:** Q1 2026 earnings (Meta Apr 29; Alphabet Apr 29; Snap May 6) — likely to refresh Reels run rate, capex, ad-impression growth, ranking-quality commentary, and possibly the Reels-vs-Feed efficiency narrative.
