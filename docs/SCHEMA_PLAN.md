# Data architecture plan: Phase 3 → 9

How the schema gets from what exists now to what
`docs/PRODUCT_VISION.md` requires. Written 2026-10-24 against migration
head `b5f2a81c04d7`.

**The rule that governs every table below:** observed data, inferred
relationships and generated recommendations are *different kinds of thing*
and never share a column.

| kind | meaning | examples | mutability |
|---|---|---|---|
| **observed** | something a platform or a file actually said | `metrics_snapshots`, `creator_snapshots`, `videos` | append-only; **never mutated** |
| **inferred** | our conclusion from observations | `video_master_links`, `publication_links`, `performances`, baselines, breakthroughs | re-derivable; carries method, version, confidence, and who decided |
| **generated** | something the system produced as output | recommendations, creative briefs, predictions | versioned; links back to the evidence it used |

Changing an analytical policy must never destroy collected data. That is why
anchors are computed at read time, and why every inferred table can be
dropped and rebuilt from the observed ones.

---

## 1. What exists today

```
creators ──┬─ videos ──┬─ metrics_snapshots        [observed]
           │           ├─ video_master_links       [inferred]
           │           └─ publication_links        [inferred]
           └─ creator_snapshots                    [observed]

performances ─── local_masters ─── video_master_links
analysis_batches ─ batch_videos ─ analysis_results  [queue, unused so far]
oauth_tokens · collection_runs · worker_heartbeats
```

Implemented in this cycle (migration `b5f2a81c04d7`), because code written
now depends on it:

| addition | why now |
|---|---|
| `collection_runs.cost_usd`, `.provider` | the spending guard sums month-to-date spend from it |
| `creators.source` / `discovered_via` / `first_seen_at` / `last_refreshed_at` / `provenance` / `display_name` / `profile_url` | the moment a discovered creator exists it must be distinguishable from an own account **in the same table** |
| `creator_snapshots` | follower history, so a growth claim can be evidenced or honestly refused rather than fabricated |

Everything below is **planned, not built.** Each is migrated in the phase
that writes to it — a table with no writer is a guess about the future, and
the instruction was to avoid speculative overengineering.

---

## 2. Discovery and provenance — Phase 4

### `discovery_runs` *(planned)*

One search. The unit of provenance for everything it produces.

| column | notes |
|---|---|
| `id` | |
| `entry_kind` | `keyword` / `hashtag` / `niche` / `genre` / `composer` / `handle` / `trend` |
| `query` | the literal text searched |
| `platform` | tiktok / instagram — never both in one run |
| `provider` | which actor/service served it |
| `params` JSONB | exactly what was sent, so a run is reproducible |
| `started_at` / `finished_at` / `status` | |
| `estimated_usd` / `cost_usd` | estimate before, actual after (CLAUDE.md §5.3) |
| `items_returned` / `creators_new` / `videos_new` | |
| `coverage_notes` JSONB | **the important one** — see below |
| `permissions_note` | which terms this ran under |

`coverage_notes` records what the source *cannot* see, because a partial
result silently treated as complete is the most dangerous kind of data.
Known entries from Phase 0A measurements:

- hashtag feeds returned **0 of 45 records aged ≥35 days**, so they cannot
  supply a mature fixed-age corpus;
- one provider **rounds view counts to ~3 significant figures**, which blurs
  outlier boundaries;
- one provider is **hard-capped at 10 items per run** on the free plan.

### `creator_identities` + `creator_identity_members` *(planned)*

The vision requires one row per *person*, with per-platform accounts
hanging off it. `creators` is per-account and the collectors depend on that
shape, so identity is added **above** it rather than by restructuring it:

| column | notes |
|---|---|
| `creator_identities.id`, `display_name`, `status` | `inferred` / `confirmed` |
| `creator_identity_members.identity_id`, `creator_id` | |
| `.evidence` JSONB, `.evidence_kind` | `bio_link` / `cross_posted_content` / `name_and_content` / `manual` |
| `.decided_by`, `.decided_at` | human or machine, as everywhere else |

**A coincidental handle match is not evidence.** `evidence_kind` exists so
that rule is enforceable rather than aspirational: the strongest available
evidence is cross-posted content detected by the existing fingerprint
pipeline, which is exactly what Phase 2 built.

### `videos.discovery_run_id` *(planned)*

Nullable FK. NULL means the publication came from an official API.

---

## 3. Baselines and breakthroughs — Phase 5

### `creator_baselines` *(planned)*

Rolling per-creator expectation. **Inferred, re-derivable, versioned.**

| column | notes |
|---|---|
| `creator_id`, `as_of_date`, `window_days` | the window is stored, not assumed |
| `anchor_age_days` | 7 / 30 / 90 — a baseline is only meaningful at a fixed age |
| `median_views`, `mad_views`, `n_publications` | MAD not standard deviation: robust to the outliers we are hunting |
| `method_version` | so a policy change adds rows rather than rewriting them |

### `breakthroughs` *(planned)*

| column | notes |
|---|---|
| `creator_id`, `detected_at`, `method_version` | |
| `change_point_date` | where the distribution shifted |
| `trigger_video_id` | nullable — a gradual regime change has no single video |
| `shape` | `single_breakthrough` / `successive` / `gradual` |
| `status` | **`inferred_breakthrough`** / `established` / `rejected` |
| `evidence` JSONB | before/after medians, window, cohort comparison |
| `missing_evidence` | **required when status is `inferred_breakthrough`** |
| `sustained` | did performance stay up, measured at a fixed age |
| `cohort_shift_pct` | the niche's movement over the same period |
| `decided_by`, `decided_at` | |

Four constraints the vision makes non-negotiable:

1. **A breakthrough is a change-point in the creator's own distribution**,
   not a single high-view video. One lucky video is noise.
2. **`status` defaults to `inferred_breakthrough`.** Promotion to
   `established` requires historical follower counts *and* age-normalised
   views. Where either is missing — the normal case for third-party
   creators — `missing_evidence` says which, and no causal claim is made.
3. **`cohort_shift_pct` is mandatory.** If the whole niche jumped that
   month, that is a platform change, not this creator's breakthrough.
4. **Survivorship bias is inherent.** A control cohort of
   non-breakthrough creators in the same niche is required before any
   pattern is promoted above `hypothesis`.

---

## 4. Multimodal features — Phase 6

### `video_features` *(planned)*

One row per (video, feature, extractor version). **Never updated in place.**

| column | notes |
|---|---|
| `video_id`, `feature_key`, `value_text` / `value_num` / `value_json` | |
| `modality` | `musical` / `visual` / `editing` / `language` |
| `method` | `vlm_read` / `asr` / `classical_cv` / `heuristic` / `human` |
| `model`, `model_version`, `extracted_at` | provenance, per the vision |
| `confidence` | low-confidence features are excluded from modelling, not averaged in |
| `superseded_by` | nullable FK to a newer extraction |

Re-extraction **adds a row**. Model drift must be visible in the data
rather than hidden by it.

### `feature_corrections` *(planned)*

| column | notes |
|---|---|
| `video_feature_id`, `corrected_value`, `corrected_by`, `corrected_at`, `note` | |

Stored **alongside** the machine value, never over it. An AI classification
is not ground truth; a correction is both the truth for analysis and
labelled evaluation data for the extractor.

The feature vocabulary itself (`docs/FEATURE_SCHEMA.md`, Phase 6) is a
separate document because it is a long controlled list — piece, composer,
tempo, dynamics, camera angle, framing, cut count, hook type, and so on.

---

## 5. Temporal intelligence — Phase 7

### `trend_measurements` *(planned)*

A feature's association with performance **as a time series**, not one
coefficient.

| column | notes |
|---|---|
| `feature_key`, `platform`, `niche` | tracked separately: TikTok and Instagram do not move together |
| `window_start`, `window_end` | explicit, always |
| `effect_size`, `ci_low`, `ci_high`, `n` | |
| `direction` | `emerging` / `stable` / `declining` |
| `method_version` | |

### `findings` *(planned)*

What the system believes, versioned so the time-travel view can reconstruct
what it believed on a past date.

| column | notes |
|---|---|
| `claim`, `evidence_class` | `established_finding` / `hypothesis` / `not_yet_validated` |
| `window_start`, `window_end`, `recency_weight_halflife_days` | the decay rate is a recorded parameter, not a hidden constant |
| `superseded_by`, `expired_at` | **a silently expired finding is worse than none** |
| `attributed_cause` | `algorithm` / `audience` / `sampling` / `competition` / `creator_behaviour` / `unknown` |

`attributed_cause` defaults to `unknown`. Not every change is the algorithm.

---

## 6. Models and predictions — Phase 7

### `model_runs` *(planned)*

| column | notes |
|---|---|
| `id`, `trained_at`, `algorithm`, `feature_version` | |
| `training_window_start/end` | chronological splits only, never shuffled |
| `sampling_weights_ref` | inverse-probability weights from `analysis_samples` |
| `validation` JSONB | V1–V5 **reported separately**, never averaged into one headline |
| `beat_baseline_own_median`, `beat_baseline_generic_advice` | both, explicitly |

### `predictions` *(planned)*

| column | notes |
|---|---|
| `model_run_id`, `video_id`, `predicted_at` | |
| `predicted_relative_performance`, `ci_low`, `ci_high` | relative, never raw view counts |
| `observed_relative_performance`, `observed_at` | filled later — this is the feedback loop |

Only pre-publication-knowable features may predict. The automated leakage
check stays passing.

---

## 7. Recommendations and the closed loop — Phase 8

```
findings ─→ recommendations ─→ creative_briefs ─→ local_masters
                                                      ↓
         predictions ←───────────────────────── videos (published)
                ↓                                     ↓
         model_runs ←──────────────────── metrics_snapshots
```

### `recommendations` *(planned)*

| column | notes |
|---|---|
| `id`, `created_at`, `request` JSONB | the conversational ask and the refinements |
| `concept`, `hook`, `repertoire`, `camera`, `editing`, `caption`, `hashtags`, `posting_window` JSONB | the structured brief from the vision |
| `platform_variants` JSONB | TikTok and Instagram adaptations |
| `evidence_class`, `uncertainty_note` | |
| `model_run_id`, `llm_model`, `llm_version` | the strategist is layer 4 over retrieved evidence, and must be identifiable |

### `recommendation_evidence` *(planned)*

| column | notes |
|---|---|
| `recommendation_id`, `finding_id` / `video_id` / `trend_measurement_id`, `weight` | |

**A recommendation with no row here is a bug.** Every claim must be
traceable to a statistic.

### `creative_briefs` and lineage *(planned)*

`creative_briefs` ties a recommendation to what was actually produced:
`recommendation_id` → `performance_id` / `local_master_id` → the resulting
`videos` row. That completes
*evidence → recommendation → brief → variant → publication → performance →
updated model*, which is what makes the live experiment answerable at all.

---

## 8. Phased migration order

| phase | migration adds | depends on | acceptance |
|---|---|---|---|
| **done** | `cost_usd`, creator provenance, `creator_snapshots` | — | spending guard enforces the cap; 15 tests |
| **4** | `discovery_runs`, `creator_identities`, `creator_identity_members`, `videos.discovery_run_id` | authorised budget | a discovery run is reproducible from its stored params; re-running updates and never duplicates |
| **5** | `creator_baselines`, `breakthroughs` | Phase 4 corpus; ≥2 capture days | a detected breakthrough carries a cohort comparison and either full evidence or a named gap |
| **6** | `video_features`, `feature_corrections` | GPU feature jobs | re-extraction adds rows; a correction never overwrites a machine value |
| **7** | `trend_measurements`, `findings`, `model_runs`, `predictions` | Phase 6 features | V1–V5 reported separately; a finding can expire |
| **8** | `recommendations`, `recommendation_evidence`, `creative_briefs` | Phase 7 models | every recommendation traces to evidence rows |

---

## 9. Things deliberately **not** in this plan

- **No separate competitor database.** Discovery writes into `creators` and
  `videos` with a different `source`. A parallel dataset would guarantee the
  two populations drift apart and be analysed inconsistently.
- **No vector table yet.** pgvector is installed on the server and
  `analysis_results` can carry embeddings when Phase 6 produces them. One
  Postgres serves data, queue and vectors (CLAUDE.md §6).
- **No mutable "current metrics" table.** Latest values are a query over
  append-only snapshots. A cached current value is a second source of truth
  that will disagree.
- **No `is_viral` flag.** Performance tiers are derived at read time from a
  creator's baseline at a fixed age, because the threshold is a policy that
  will change and must not be frozen into rows.
