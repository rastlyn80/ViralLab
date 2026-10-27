# ViralLab — Product Vision

**Status: permanent specification.** This document defines what ViralLab is
for. It outranks any individual phase plan: when `DEVELOPMENT_PLAN.md` and
this document disagree about *what we are building*, this one is correct and
the plan gets corrected. When they disagree about *how or when*, the plan is
correct.

**Revision 2** — 2026-10-27. Adds §6.1, the surface principle: the science
stays rigorous and the surface stays readable by someone who has never heard
of a correlation. Revision 1 (2026-10-24) superseded the scattered statements
of intent in `DEVELOPMENT_PLAN.md` §1 and §7 Phase 11.

---

## 1. The product in one paragraph

ViralLab is a **continuously learning, evidence-driven AI creative
strategist**. It discovers what makes social-media content succeed,
understands how those success patterns change over time, and generates
personalised, actionable recommendations for what to make next. It is
local-first and private by default, built initially around one professional
concert pianist's accounts, and architected throughout to stay
niche-agnostic so it can serve other creators later.

The emphasis is on **evidence-driven**. The product's value is not that it
produces advice — anything can produce advice — but that every piece of
advice is traceable to an observation, carries an honest confidence class,
and has survived a validation protocol designed to catch the ways this kind
of analysis normally fools itself.

## 2. The question the product exists to answer

> *Given everything that can be observed about content that succeeded and
> content that did not, what should this creator make next, and why?*

Three sub-questions, in increasing difficulty:

1. **What happened?** Reliable, provenance-tracked observation of
   publications and their performance — own accounts and others'.
2. **What distinguishes success?** Patterns that separate over-performing
   content from the creator's own baseline, at comparable ages, robust to
   the confounds in §7.
3. **What should change?** Recommendations that are specific enough to act
   on, and whose effect can be measured afterwards.

Most of the product's engineering exists to stop (2) and (3) from producing
confident nonsense.

---

## 3. Core capability areas

These are **requirements, not optional enhancements.** Each names the phase
that is expected to deliver it; see §8 for the mapping.

### A. Automatic competitive discovery

The system must discover and analyse content it was never told about.

**Discovery entry points:**

| Entry point | Example |
|---|---|
| Keyword / free text | "emotional piano performance" |
| Hashtag | `#pianotok`, `#classicalmusic` |
| Niche | classical piano, piano composition |
| Musical genre | classical, neoclassical, jazz piano |
| Composer / repertoire | Chopin, Rachmaninoff, Ballade No. 1 |
| Contextual / thematic | virtuoso playing, practice-room content, piano covers of pop |
| Creator cohort | established creators; small accounts with unusually successful videos; emerging creators; historically successful creators now in decline |
| Trend | an emerging audio, format or framing |

**Requirements:**

- Discovery runs on **TikTok and Instagram independently**, and supports
  **comparative** analysis across them.
- Discovery is **repeatable**: re-running a search updates the shared
  intelligence database incrementally rather than creating a parallel
  dataset or duplicating rows.
- Every discovered record carries **provenance**: which source produced it,
  under which permissions, when, at what cost, and what the known coverage
  limits of that source are.
- Discovery must find **unusually successful videos from small accounts**,
  not only big accounts. This is a distinct requirement because
  follower-weighted ranking systematically hides exactly the signal we most
  want: a format that works independently of existing reach.

**Hard constraints inherited from Phase 0A:**

- `[PRIMARY]` Official APIs **do not** expose competitor data. TikTok's
  Display API covers the authorised account only; Instagram's Hashtag Search
  and Business Discovery are narrow and rate-limited. **Never design as if
  official APIs will supply a competitor corpus.** Third-party collection is
  the only route, under the operating policy in `DEVELOPMENT_PLAN.md` §2.3.
- `[PRIMARY, measured 0A]` **Hashtag discovery returns only recent content**
  (0 of 45 records aged ≥35 days), so it cannot supply a mature fixed-age
  corpus. Profile scraping returns the full back-catalogue (1,452-day span).
  The pipeline shape is therefore fixed:

  > **discover creators via hashtag/keyword → fetch each creator's
  > back-catalogue by profile → build the fixed-age corpus from the
  > back-catalogues.**

  Hashtag feeds also skew toward currently-promoted content, so they are a
  *creator-discovery* instrument, never a performance-measurement one.
- `[PRIMARY, measured 0A]` One validated provider rounds view counts to ~3
  significant figures. That is tolerable for a `log(views / baseline)`
  target but **blurs outlier boundaries and makes small within-creator
  differences unresolvable** — it must be recorded as a corpus-level
  limitation wherever it is used.
- `[PRIMARY, measured 0A]` Third-party media **is** directly downloadable
  and honours range requests, so multimodal analysis of discovered content
  is feasible.

### B. Targeted creator analysis

The user must be able to type a handle — e.g. `@tonyannmusic` — and ask for
a full analysis.

**The system should:**

1. **Identify and reconcile the creator's accounts across platforms**, and
   do so **only when supported by evidence**. A TikTok handle and an
   Instagram handle are the same person when something says so: a matching
   link in a bio, an identical display name plus overlapping content, or
   cross-posted videos detected by the existing fingerprint pipeline. A
   coincidental handle match is **not** evidence, and an unreconciled
   account stays unreconciled rather than being guessed.
2. **Retrieve available historical publications and performance signals**,
   recording what was and was not obtainable.
3. **Analyse individual videos** — hooks, music, filming, editing,
   on-screen text — through the existing tiered pipeline.
4. **Analyse the account's overall content strategy** — cadence, format mix,
   repertoire, consistency, how it has changed.
5. **Compare platforms where reliable correspondence exists**, and say so
   explicitly when it does not.
6. **Store observations and findings in the common database.**
7. **Refresh analysed accounts over time**, so a creator analysed once
   becomes a tracked creator.

**Non-negotiable:** manual creator analysis and automatic discovery feed
**the same intelligence engine and the same tables.** There is no "manual
analysis" dataset. A creator discovered by hashtag and a creator typed in by
hand differ only in a provenance field.

### C. Breakthrough-video and creator-trajectory analysis

**This is the single most distinctive feature of the product**, and the one
most likely to produce genuinely actionable findings.

For a successful creator, identify the publication — or the sequence of
publications — that marked a real change in their performance trajectory,
and characterise what changed.

**The analysis must cover:**

- **Before:** posting frequency, format mix, and typical performance
  preceding the breakthrough.
- **The earliest unusually successful videos** measured against *that
  creator's own established baseline at a comparable age* — not against
  absolute view counts, and not against the niche.
- **What those videos were:** content, music, hook, filming, editing,
  on-screen text, length, repertoire.
- **After:** what the creator changed or repeated.
- **Durability:** whether subsequent performance rose **sustainably**, or
  the breakthrough was isolated.
- **Shape:** whether growth came from one breakthrough, several successive
  breakthroughs, or gradual accumulation with no discontinuity.

**Output:** a **creator timeline visualisation** showing publication
history, outliers against the rolling baseline, detected breakthrough
points, and the regime changes between them — so the shape of a career is
legible at a glance and a human can judge whether the detected breakthrough
is real.

**Statistical honesty requirements, which this feature makes unusually easy
to violate:**

- A "breakthrough" is a **change-point in a creator's own performance
  distribution**, not a single high-view video. One lucky video is noise.
- Detection must use **fixed-age** performance. A video from three years ago
  has had three years to accumulate views; comparing its lifetime total to a
  recent video's measures elapsed time, not quality (CLAUDE.md §3.8a).
- **Survivorship bias is inherent to this feature.** Studying creators who
  succeeded cannot tell us what makes success, because we never see the
  creators who did the same things and failed. Every trajectory finding
  carries this caveat, and a control cohort of non-breakthrough creators in
  the same niche is required before any pattern is promoted above
  `hypothesis`.
- A detected breakthrough is **correlational**. "This creator changed X and
  then grew" is never reported as "changing X causes growth".
- Platform-wide effects must be ruled out: if every creator in the niche
  jumped the same month, that is an algorithm change, not a breakthrough.
- **Never claim a video caused follower growth without historical
  evidence.** Where historical follower counts or age-normalised view data
  are unavailable - the normal case for third-party creators - the finding
  is labelled an **`inferred_breakthrough`**, never an established causal
  event. These are different database states, not different wordings: an
  inferred breakthrough records exactly which evidence was missing.

### D. Comprehensive multimodal video analysis

Every analysed video must yield **structured, searchable characteristics**.
The taxonomy below is the product requirement; `docs/FEATURE_SCHEMA.md` is
its implementation contract.

**Musical**
piece | composer | genre | recognisable passage | interpretation | tempo |
dynamics | virtuosity | emotional character | musical climax and its
position | originality | identifiable musical structure

**Visual**
camera angle | framing | shot distance | performer visibility | hands vs
face | setting | background | lighting | colour palette | performer
presentation | visual atmosphere

**Editing**
total duration | opening seconds | cut count | transitions | pacing | camera
changes | overlays | templates | effects | subtitles | one-take vs edited |
identifiable visual hooks

**Language and publishing**
spoken introduction | on-screen text | opening hook | caption | hashtags |
calls to action | posting date and time | platform-specific differences

**Every stored characteristic carries, without exception:**

| field | why |
|---|---|
| documented schema | a feature nobody can define cannot be analysed |
| source | which modality and which input produced it |
| method | the extractor: VLM read, ASR, classical CV, heuristic |
| model + version | a feature extracted by two model versions is two features |
| confidence / uncertainty | a guess and a certainty must not aggregate alike |
| extracted-at | so re-extraction is detectable |

**An AI classification is not ground truth.** A standing architectural
requirement, not a caveat:

- Features are stored with their confidence, and low-confidence features are
  excluded from modelling rather than silently averaged in.
- A **human correction and review mechanism** is required. Any extracted
  feature must be correctable by the user; the correction is stored
  **alongside** the machine value rather than overwriting it; and
  corrections become labelled evaluation data for the extractor.
- Re-extraction with a new model version **adds a row**, never mutates one.
  Model drift must be visible in the data rather than hidden by it.
- Whisper hallucinates on music-only audio (measured, Phase 0B), so ASR is
  gated behind speech detection and stores NULL when no speech is present.
  That is the template for every extractor: **absence is recorded as
  absence.**

### E. Scientific comparison of successful and unsuccessful content

**Do not study viral videos alone.** A corpus of hits cannot identify what
distinguishes a hit. The engine compares **viral, moderately successful and
ordinary** publications, and the sampler must preserve all three strata
(CLAUDE.md 3.12).

Every comparison accounts for, where the data allows:

| factor | handling |
|---|---|
| creator baseline and account size | targets relative to the creator own rolling baseline, never absolute views |
| platform and niche | stratified, never pooled blindly |
| publication age | fixed-age anchors only (3.8a) |
| observation date and which historical metrics exist | recorded per snapshot; absent anchors stay absent |
| seasonal effects | time-aware baselines |
| sampling bias | inverse-probability weights carried from `analysis_samples` |
| multiple correlated characteristics | multivariate treatment; correlated features are not reported as independent findings |
| correlation vs prediction vs causation | three distinct claim types, never conflated |

**Never fabricate** historical metrics, missing retention values, or
unavailable competitor statistics. If TikTok does not report saves, the
value is unknown, not zero. If no snapshot brackets day 7, `views@7d` does
not exist and is not interpolated.

### F. Continuous learning and temporal intelligence

**Essential architectural requirement, not a late feature.** Successful
strategies change. A system that cannot tell 2023 winning formula from 2026
will confidently recommend obsolete tactics, precisely because 2023 videos
have had three more years to accumulate views.

**The governing rule:**

> A strategy that worked in 2023 must not be recommended in 2026 merely
> because its historical videos accumulated many views. Conversely, older
> strategies must remain **discoverable and eligible for reconsideration**
> if they become effective again.

So: **preserve all historical observations permanently, while letting
recommendations prioritise current evidence.** Retention and relevance are
different concerns, implemented separately, never by deleting data.

Required capabilities:

- **Recency-weighted analysis** - evidence weight decays with age, with the
  decay rate a recorded, tunable parameter rather than a hidden constant.
- **Rolling observation windows** - findings are computed over explicit
  windows and carry those windows with them.
- **Emerging and declining pattern detection** - a feature association with
  performance is tracked *as a time series*, not as one coefficient.
- **Change-point and trend analysis** - at niche, platform and creator level.
- **Historical vs current strategy comparison** - "what worked then vs now"
  as a first-class query.
- **Platform-specific and niche-specific evolution** - tracked separately;
  TikTok and Instagram do not move together.
- **Model degradation detection and retraining triggers** - walk-forward
  drift (12.3 V5) running continuously, with an explicit recalibration
  trigger rather than a human happening to notice.
- **Historical exploration / time-travel view** - reconstruct what the
  system believed at a past date, which requires versioned findings.
- **Alerts for meaningful emerging opportunities** - with a false-positive
  budget, because an alerting system nobody trusts is worse than none.

**Attribution discipline.** Do not attribute every observed change to
platform algorithms. Distinguish, where the data permits:

| cause | signature |
|---|---|
| algorithm change | niche-wide, abrupt, synchronous across unrelated creators |
| audience preference shift | gradual, correlated with content characteristics |
| sampling change | our collection changed, not the world |
| competition | more creators entering a format |
| creator behaviour | they changed what they post |

A niche-wide jump in one month is evidence about the platform, not about any
individual creator.

### G. The AI creative strategist - the flagship feature

An AI strategist that generates **personalised video concepts on demand**
from the intelligence database.

A complete recommendation can include:

- suggested repertoire, musical passage, or original composition
- the central creative concept
- opening hook and first-three-seconds strategy
- suggested words to speak on camera, where appropriate
- camera placement, angle, framing, lighting
- suggested duration, pacing, cuts, transitions, editing style
- on-screen text, caption, hashtags, cover concept
- **platform-specific TikTok and Instagram adaptations**
- suggested posting windows, based on available evidence
- supporting comparable videos and creator examples
- **evidence strength, uncertainty, and the reasons for the recommendation**
- alternative concepts suitable for testing

Requests must be **conversational and refinable** - against the creator
artistic identity, available equipment, preferences and existing repertoire.
Those constraints are stored profile data, not re-typed each time.

**Architecture: five layers, deliberately separated.**

```
 1. multimodal extraction     what is in the video            (Phase 6)
 2. statistical modelling     what correlates with outcome    (Phase 7)
 3. evidence retrieval        comparable videos and findings  (Phase 8)
 4. LLM creative strategist   prose, concepts, adaptation     (Phase 8)
 5. feedback learning         did the advice actually work    (Phase 9+)
```

**Do not train a large language model from scratch.** The LLM is layer 4
only: a *writer and planner* over retrieved evidence, never the source of
evidence. It must not invent a statistic. Fine-tuning a specialised model is
considered **only** when enough validated data exists to justify it, and
never before layer 2 has beaten its baselines.

This separation is what keeps the product honest. If layer 2 has no
established finding, layer 4 can still produce a concept - but it is
labelled `hypothesis` or `not-yet-validated`, and the interface shows that.

### H. Closed feedback loop

The system must eventually connect its own advice to real outcomes and learn
which advice works. The full lineage is preserved end to end:

```
research evidence -> recommendation -> creative brief -> produced variant
      -> platform publication -> observed performance -> updated model
```

Each arrow is a stored relationship, not a convention. The existing
performance / variant / publication model (Phase 2) is already the second
half of this chain; the first half needs `recommendations` and
`creative_briefs` with explicit lineage.

This is what makes the live experiment possible: comparing **predicted**
against **observed** performance for content the system actually advised.
Without the lineage, the MVP central question - do recommendations improve
outcomes? - is unanswerable.

### I. One unified graphical interface

Every capability must be reachable from **one coherent browser
application.** Explicitly: **avoid a collection of disconnected
command-line tools and temporary interfaces.** The CLI tools built during
Phase 2 are scaffolding; each is to be absorbed or retired.

Progressive capability order:

| # | Surface | Phase |
|---|---|---|
| 1 | Dashboard and system health | **3 (delivered)** |
| 2 | Connected accounts | **3 (delivered)** |
| 3 | Personal content library | **3 (delivered)** |
| 4 | Video detail and cross-platform comparison | **3 (delivered)** |
| 5 | Human matching review | **3 (delivered)**, absorbing `scripts/review_ui.py` |
| 6 | Competitor discovery and search | 4 |
| 7 | Manual creator analysis | 4 |
| 8 | Breakthrough-video timelines | 5 |
| 9 | Multimodal video intelligence | 6 |
| 10 | Historical vs current trend analysis | 7 |
| 11 | AI content recommendations | 8 |
| 12 | Content preparation, scheduling, publishing | post-MVP |

### J. Own-account intelligence *(substantially delivered, Phase 2)*

The creator own accounts are the highest-quality data in the system:
official APIs, exact figures, true daily snapshots, and local master files
for content-level ground truth. This is the **only** place where
cross-platform correspondence can be established from content rather than
inferred from metadata, which makes it the anchor for everything else.

Delivered: 234 TikTok + 289 Instagram publications; 258 preserved Instagram
videos; a measured spectral audio fingerprint; a performance / variant /
publication relationship model; 129 machine-proposed master links and 201
cross-platform proposals, none yet human-verified.

---

## 4. What ViralLab is not

Stated as plainly as the capabilities, because these are the failure modes
this product is most likely to drift into:

- **Not a vanity dashboard.** An impressive UI that does not answer "what
  should I make next, and why" is a failure, not a milestone.
- **Not a virality guarantee.** Never promises or implies guaranteed
  virality, in any phrasing, including hedged marketing phrasing.
- **Not a reverse-engineered algorithm.** We never claim to have
  reconstructed a platform's ranking system. We model *observable outcomes*.
- **Not a causal engine from observational data.** The only causal evidence
  this project can produce comes from the Phase 8 pre-registered experiment,
  with its stated power limits.
- **Not a creator database to resell.** Third-party data is internal
  research only (§2.3). Commercialisation involving it requires actual legal
  advice first.
- **Not an autoposter.** Publishing automation (Phase 11.4) is explicitly
  post-MVP and subordinate to the analytical core.

---

## 5. The intelligence database is one database

The architectural commitment that makes the product coherent: **every
observation lands in the same schema, whatever discovered it.**

```
                    discovery (hashtag, keyword, trend)
                                  │
     manual handle lookup ────────┼──────── own-account official API
                                  │
                                  ▼
            creators ─┬─ creator_accounts (per platform, reconciled)
                      │
                      ├─ videos (publications) ─┬─ metrics_snapshots
                      │                         ├─ analysis_results
                      │                         └─ publication_links
                      │
                      └─ creator_baselines ──── breakthroughs
                                  │
                      performances ─ local_masters (own content only)
```

Consequences that must hold:

- **One `creators` row per person**, with per-platform accounts hanging off
  it. Not one row per handle.
- **Provenance on every row** that did not come from an official API:
  source, collected-at, cost, permissions, coverage limits.
- **Re-running any collection is idempotent.** Re-discovery updates; it
  never duplicates.
- **Own-account and third-party data are distinguishable but not
  segregated.** `is_own_account` marks quality, not a separate pipeline.
- **Observation and interpretation are separate tables.** Raw snapshots are
  append-only and never mutated; baselines, outlier labels and breakthroughs
  are derived and re-derivable. Changing an analytical policy must never
  destroy collected data.

---

## 6. Interface vision

One unified graphical interface, served by the existing API process (no new
service), private and tailnet-only.

| Surface | Purpose |
|---|---|
| **Dashboard** | Is the system healthy, what did it collect, what needs me |
| **Corpus** | Publications across platforms, filterable, with fixed-age performance |
| **Creator** | One creator: timeline, outliers, breakthroughs, strategy summary |
| **Discovery** | Launch and review searches; see cost and coverage before and after |
| **Review** | Confirm or reject proposed relationships (delivered, Phase 2) |
| **Findings** | What the system believes, with evidence class and validation status |

The full progressive capability order is in 3.I.

Interface principles:

- **Show uncertainty, not just numbers.** A proposed link, an inferred
  grouping and a confirmed fact must look different.
- **Never display a metric without its anchor age.** A bare view count is
  misleading by construction.
- **Make missing data visible.** "TikTok does not report saves" must never
  render as 0.
- **Cost before commitment.** Any paid action shows its estimated cost and
  the remaining budget before it runs.

## 6.1 Sophisticated science underneath, effortless intelligence on top

**ViralLab is not an analytics dashboard.** It is a creative research
assistant that happens to be rigorous. The analysis underneath must satisfy
§3's evidence rules without compromise; the surface must be usable by
someone who has never heard of a rank correlation, a confidence interval or
a false-discovery rate, and who should never need to learn.

The user must never have to know a feature name, a coefficient, a schema or
a statistical term to understand what the system found.

Not this, as the thing a reader meets first:

> `background_edge_density: rho = −0.401; partial rho = −0.318`

But this:

> **Videos with more visual detail tended to have fewer viewers skipping
> them.**
>
> Across the 229 Instagram videos we could measure, this was a moderate
> tendency, and it held after accounting for how long each video is, how
> long it has been online, and how much paid promotion it received. This is
> an association, not proof: nothing here shows that changing one would
> change the other.

The coefficients are not deleted. They move into an **Evidence and
methodology** disclosure, one click away, where the number, the interval,
the correction and the controls are all available to anyone who wants them.

Four rules this surface obeys, each enforced by a test:

1. **A correlation is never written as a cause.** "Videos with X tended to
   have Y", never "X improves Y" and never "adding X will". The verb is what
   a reader remembers; the caveat underneath is not.
2. **A correlation is never rendered as a percentage.** A rho of −0.40 is
   not "40% fewer skips". Strength is reported on a coarse verbal scale,
   because a finer one would imply a precision these samples do not have.
3. **A measurement is described as what it is.** `text_region_score`
   measures visual busyness where captions usually sit. It is *not* a
   measure of text, it does not read words, and the field that does is
   named separately. Every measurement carries a `not_a_measure_of`.
4. **A finding belongs to the platform it was measured on.** Instagram's
   skip rate has no TikTok counterpart in this data, and the two are never
   presented as the same question answered twice.

And one that matters as much: **a withheld or unavailable measurement can
never be phrased as a finding.** It appears as a gap, with the reason, in
the same place a finding would have appeared.

### How this is enforced, 2026-10-27

The principle above was stated before the interface obeyed it. An audit
of the running application found **202 places** where a column name, a
method version or a bare statistic was the primary content on a page.
That is now **1**, and the remaining one is a creator's own username.

Three mechanisms, in order of how much they prevent:

1. **One vocabulary.** `server/features/lexicon.py` holds the plain
   English for all 79 measurements and every outcome — display name,
   what it measures, what high and low mean, *what it does not
   measure*, method, limitations. `webui/lexicon.js` is the only thing
   the interface may use to turn a key into a label, and a test fails
   if a view hard-codes one.
2. **Two presentation levels.** Anything marked `.technical` — exact
   feature names, coefficients, sample sizes, method versions — is
   hidden until the reader presses *Show technical details*. Nothing is
   deleted or duplicated to achieve it, and a live browser test asserts
   that `edge_density` is absent before the toggle and present after.
3. **An audit that reads the screen.** `scripts/provision/ui_audit.py`
   drives a real headless Chromium, signs in, visits every page at
   desktop and phone widths, and reads the *visible* text. It is the
   only check that can distinguish "the API returns a display name"
   from "the page shows one", and that distinction is the whole of this
   section.

Server-side prose written by analysts — a verdict line, a caveat, an
outcome note — passes through `lexicon.humanise()` at the API boundary,
which swaps stored keys for display names longest-first and softens the
statistical vocabulary. Rewriting each string by hand would have worked
once and drifted back.

**The eventual conversational assistant is an interpreter, not an oracle.**
It may use an external frontier model — running it locally is not a
requirement — but it retrieves verified observations and evidence records
through controlled, read-only tools, cites them, and is never permitted to
generate statistics, sample sizes, citations, platform capabilities or
performance predictions of its own, nor to issue arbitrary SQL against
production. Factual findings and creative hypotheses stay visibly separate.

---

## 7. Confounds the product must handle, permanently

These are not caveats to add later; they shape the schema and the analysis.

| Confound | Why it breaks naive analysis | Mitigation |
|---|---|---|
| **Age** | Lifetime views measure elapsed exposure | fixed-age anchors; never compare across ages |
| **Follower reach** | Big accounts' views reflect reach, not content | relative-to-own-baseline targets |
| **Survivorship** | We only see creators who succeeded | control cohorts; explicit caveat on every trajectory finding |
| **Platform algorithm change** | Niche-wide jumps look like individual breakthroughs | detect cohort-level shifts and subtract them |
| **Promotion / paid reach** | Boosted content is not organic performance | record where knowable; flag where not |
| **Cross-posting** | The same video on two platforms is not two observations | `publication_links`; performance/variant/publication layers |
| **Rounded metrics** | One provider rounds to 3 s.f. | recorded per-corpus; outlier boundaries treated as fuzzy |
| **Seasonality** | Repertoire and engagement are seasonal | time-aware baselines, not global ones |
| **Selection by hashtag** | Hashtag feeds favour promoted recent content | hashtags discover creators only, never measure performance |

---

## 8. Mapping to the phase plan

| Capability | Phase | State |
|---|---|---|
| D. Own-account intelligence | 2 | ✅ substantially delivered |
| Review interface | 2 | ✅ delivered |
| A. Automatic discovery | **3** | architecture prepared; collection not built |
| B. Targeted creator analysis | **3–4** | architecture prepared |
| Baselines & outlier labelling | 4 | prerequisite for C |
| Tiered multimodal analysis | 5 | Phase 0B benchmarked |
| C. Breakthrough & trajectory | **4–6** | detection design recorded; needs baselines |
| Creator timeline visualisation | **6** | part of the unified interface |
| E. Recommendations | 7 | — |
| Causal validation | 8 | the MVP's actual endpoint |
| F. Continuous learning | 11.7 | post-MVP |

**The MVP still ends at Phase 8**, the live own-account experiment. Adding
this vision document does not expand the MVP; it records where the MVP is
heading so that Phase 3's schema does not have to be rewritten later.

---

## 9. Open questions this vision does not settle

1. **Cross-platform creator reconciliation without content overlap.** When a
   creator posts different content to each platform, what evidence is strong
   enough? Bio links are strong but often absent. → Phase 3.
2. **Breakthrough detection with sparse history.** Third-party back-catalogues
   give one lifetime reading per video, not a time series. Can a change-point
   be detected from a single cross-sectional snapshot plus publication dates?
   → Phase 4; may require the age-decay model in §5.4 Strategy B.
3. **Control cohort construction.** Who is a fair comparison for a creator
   who broke through? → Phase 4, blocking any trajectory claim.
4. **Does niche-specific structure need domain extractors?** Generic
   multimodal features may miss repertoire, tempo and dynamics. → Phase 6.
5. **What a "trend" is, operationally.** An emerging audio, format, or
   framing — measurable how, and at what lag? → Phase 3.
