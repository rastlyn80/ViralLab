/**
 * GENERATED FILE - do not edit.
 *
 * Produced by scripts/gen_ui_types.py from the FastAPI OpenAPI schema, which
 * is itself generated from the Pydantic models in shared/schemas/ui.py.
 * Those models are the single source of truth for the API boundary
 * (ADR 0006); this file exists so an editor can check the frontend against
 * them without a build step.
 *
 * Regenerate with:  python scripts/gen_ui_types.py
 */

/**
 * @typedef {Object} AiOperationRow
 * @property {number|null=} id
 * @property {number|null=} video_id
 * @property {string} task
 * @property {string} provider
 * @property {string} model
 * @property {string} hosting
 * @property {string} category
 * @property {string|null=} mode
 * @property {string} status
 * @property {boolean|null=} schema_valid
 * @property {Array<Object>=} validation_errors
 * @property {string|null=} refused_reason
 * @property {number|null=} input_tokens
 * @property {number|null=} output_tokens
 * @property {number|null=} images
 * @property {number|null=} estimated_usd
 * @property {number|null=} actual_usd
 * @property {number|null=} latency_s
 * @property {number|null=} peak_vram_mib
 * @property {string|null=} created_at
 */

/**
 * @typedef {Object} AnalyseRequestBody
 * @property {number} video_id
 * @property {string|null=} mode
 * @property {boolean=} confirm_paid
 */

/**
 * @typedef {Object} AnalysedVideoPage
 * @property {number} total
 * @property {number} offset
 * @property {number} limit
 * @property {Array<AnalysedVideoRow>=} items
 * @property {number=} analysable
 * @property {number=} extracted
 * @property {number=} with_retention
 */

/**
 * @typedef {Object} AnalysedVideoRow
 * @property {number} video_id
 * @property {string} external_id
 * @property {string|null} posted_at
 * @property {number|null} duration_s
 * @property {string|null} caption
 * @property {number|null} views
 * @property {number|null=} skip_rate
 * @property {number|null=} avg_watch_ms
 * @property {boolean=} avg_watch_includes_replays
 * @property {number|null=} age_days_at_capture
 * @property {number=} features_extracted
 * @property {Array<string>=} method_versions
 * @property {boolean=} has_media
 * @property {number=} corrections
 * @property {number|null=} cut_count
 * @property {number|null=} text_region_score
 * @property {boolean|null=} speech_present
 * @property {number|null=} first_sound_at_s
 */

/**
 * @typedef {Object} AnalysisPlanView
 * @property {number} video_id
 * @property {string} task
 * @property {string} mode
 * @property {Array<string>=} providers
 * @property {boolean=} refused
 * @property {string=} reason
 * @property {number=} estimated_usd
 * @property {boolean=} requires_authorisation
 * @property {Array<Object>=} considered
 * @property {number=} frames_planned
 * @property {Array<number>=} frame_timestamps
 * @property {boolean=} is_own_media
 * @property {string=} category
 * @property {number=} category_cap_usd
 * @property {boolean=} category_authorised
 */

/**
 * @typedef {Object} AnalysisRequestBody
 * @property {number} video_id
 * @property {string=} tier
 * @property {Array<string>=} modalities
 */

/**
 * @typedef {Object} AnalysisRequestView
 * @property {number} video_id
 * @property {string} external_id
 * @property {string} platform
 * @property {string} tier
 * @property {Array<string>} modalities
 * @property {Array<number>=} batch_ids
 * @property {string} media_policy
 * @property {number} estimated_gpu_seconds
 * @property {number} estimated_transient_mb
 * @property {boolean} will_run
 * @property {string|null=} why_not
 * @property {Array<string>=} notes
 */

/**
 * @typedef {Object} AssociationView
 * @property {string} feature
 * @property {number} n
 * @property {number|null=} rho
 * @property {number|null=} p_value
 * @property {number|null=} p_adjusted
 * @property {number|null=} rho_partial_duration
 * @property {number|null=} rho_partial_duration_age
 * @property {number|null=} ci_low
 * @property {number|null=} ci_high
 * @property {string=} effect_size_label
 * @property {string|null=} direction
 * @property {string|null=} interpretation
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} BatchLease
 * What the server hands a worker on a successful dequeue.
 * @property {number} batch_id
 * @property {string} lease_token
 * @property {Modality} modality
 * @property {Tier} tier
 * @property {string} method_version
 * @property {Array<VideoRef>} videos
 * @property {string} lease_expires_at
 * @property {number} attempt
 * @property {number} max_attempts
 * @property {string=} schema_version
 */

/**
 * @typedef {Object} BreakthroughReportView
 * @property {number} creator_id
 * @property {string} method_version
 * @property {number} publications_considered
 * @property {BreakthroughView|null=} breakthrough
 * @property {Array<BreakthroughView>=} outliers
 * @property {Array<string>=} refusals
 * @property {Array<string>=} caveats
 * @property {boolean=} has_follower_history
 * @property {boolean=} has_repeated_observations
 */

/**
 * @typedef {Object} BreakthroughView
 * @property {number} id
 * @property {string} classification
 * @property {number|null=} video_id
 * @property {string|null=} external_id
 * @property {string|null=} posted_at
 * @property {string|null=} caption
 * @property {number|null=} publication_index
 * @property {number|null=} publications_total
 * @property {number|null=} observed_views
 * @property {number|null=} baseline_views
 * @property {number|null=} median_after
 * @property {number|null=} relative_performance
 * @property {number|null=} robust_z
 * @property {number|null=} step_ratio
 * @property {boolean|null=} sustained
 * @property {number|null=} confidence
 * @property {Object=} evidence
 * @property {Array<string>=} missing_evidence
 * @property {string|null=} decided_by
 * @property {string} method_version
 */

/**
 * @typedef {Object} CandidateSignalView
 * @property {string} key
 * @property {string} label
 * @property {number|null} value
 * @property {boolean} available
 * @property {string|null=} note
 */

/**
 * @typedef {Object} CandidateView
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null} display_name
 * @property {string|null} profile_url
 * @property {number|null} follower_count
 * @property {boolean} followers_available
 * @property {number} sampled_publications
 * @property {number|null} sampled_median_views
 * @property {number|null} sampled_best_views
 * @property {string|null} best_publication_external_id
 * @property {number|null} best_views_per_follower
 * @property {number|null} engagement_rate
 * @property {boolean} counts_rounded
 * @property {string|null} newest_publication_at
 * @property {string|null} oldest_publication_at
 * @property {string|null} niche
 * @property {boolean} already_researched
 * @property {string|null} last_refreshed_at
 * @property {number} publications_collected
 * @property {number} estimated_research_usd
 * @property {number} score
 * @property {string} tier
 * @property {Array<string>=} reasons
 * @property {Array<CandidateSignalView>=} signals
 * @property {boolean=} is_fixture
 */

/**
 * @typedef {Object} CapabilityList
 * @property {Array<CapabilityState>} capabilities
 */

/**
 * @typedef {Object} CapabilityState
 * Honest state of a product capability.
 * @property {string} key
 * @property {string} title
 * @property {string} summary
 * @property {boolean} implemented
 * @property {string} phase
 * @property {Array<string>=} blocked_by
 * @property {Object<string, number>=} counts
 */

/**
 * @typedef {Object} ChangePointDetail
 * @property {string} classification
 * @property {number|null} publication_index
 * @property {number|null} video_id
 * @property {string|null} at
 * @property {number|null} median_before
 * @property {number|null} median_after
 * @property {number|null} step_ratio
 * @property {boolean|null} sustained
 * @property {number|null} confidence
 * @property {boolean=} confidence_is_calibrated
 * @property {string} confidence_note
 * @property {Object=} evidence
 * @property {Array<string>=} missing_evidence
 */

/**
 * @typedef {Object} CollectionCreateRequest
 * @property {string} name
 * @property {string=} purpose
 * @property {string|null=} niche
 * @property {string|null=} notes
 */

/**
 * @typedef {Object} CollectionDetail
 * @property {number} id
 * @property {string} name
 * @property {string} purpose
 * @property {string} purpose_label
 * @property {string|null} niche
 * @property {string|null} notes
 * @property {string|null} created_at
 * @property {string|null} updated_at
 * @property {Array<CollectionMemberView>=} members
 * @property {number=} member_count
 * @property {number=} researched_count
 * @property {string|null=} stalest_refresh
 * @property {Array<string>=} needs_refresh
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} CollectionMemberRequest
 * @property {number} creator_id
 * @property {string|null=} reason
 * @property {string|null=} notes
 * @property {number|null=} discovered_by_job_id
 */

/**
 * @typedef {Object} CollectionMemberView
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null} display_name
 * @property {number|null} follower_count
 * @property {boolean} is_fixture
 * @property {string} research_status
 * @property {string|null} last_refreshed_at
 * @property {number} publications_collected
 * @property {string|null} notes
 * @property {string|null} reason
 * @property {string|null} added_at
 * @property {number=} observations
 * @property {number=} publications_observed
 * @property {number|null=} median_views
 * @property {number|null=} best_relative_performance
 * @property {number|null=} typical_relative_performance
 * @property {number|null=} age_span_days
 * @property {string|null=} breakthrough_classification
 * @property {string|null=} breakthrough_at
 * @property {number|null=} median_days_between_posts
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} CollectionRow
 * @property {number} id
 * @property {string} name
 * @property {string} purpose
 * @property {string} purpose_label
 * @property {string|null} niche
 * @property {string|null} notes
 * @property {string|null} created_at
 * @property {string|null} updated_at
 * @property {number} member_count
 * @property {number} researched_count
 * @property {string|null} stalest_refresh
 */

/**
 * @typedef {Object} CollectionRunSummary
 * @property {string} platform
 * @property {string} kind
 * @property {string} status
 * @property {string|null=} started_at
 * @property {string|null=} finished_at
 * @property {number|null=} videos_seen
 * @property {number|null=} videos_new
 * @property {number|null=} snapshots_written
 * @property {string|null=} error
 */

/**
 * @typedef {Object} CollectionUpdateRequest
 * @property {string|null=} name
 * @property {string|null=} purpose
 * @property {string|null=} niche
 * @property {string|null=} notes
 */

/**
 * @typedef {Object} CorrectionRequest
 * @property {number|boolean|string|null=} value
 * @property {string|null=} note
 */

/**
 * @typedef {Object} CreatorReportView
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null} display_name
 * @property {string|null} profile_url
 * @property {boolean} is_fixture
 * @property {string} generated_at
 * @property {Array<ReportSectionView>=} sections
 * @property {string|null=} research_date
 * @property {string|null=} provider
 * @property {number|null=} cost_usd
 * @property {number|null=} job_id
 * @property {Array<string>=} data_limitations
 */

/**
 * @typedef {Object} CreatorTimelineView
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null} display_name
 * @property {boolean} is_fixture
 * @property {string} mode
 * @property {string} mode_label
 * @property {Array<TimelinePointView>=} points
 * @property {ChangePointDetail|null=} change_point
 * @property {number=} outlier_candidates
 * @property {Array<SustainedRunView>=} sustained_runs
 * @property {Array<FrequencyBucketView>=} frequency
 * @property {number=} repeatedly_observed
 * @property {number=} anchored_points
 * @property {number|null=} age_span_days
 * @property {number|null=} median_days_between_posts
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} CredentialStatus
 * @property {string} key
 * @property {string} platform
 * @property {string} label
 * @property {string} purpose
 * @property {string} blocked_without
 * @property {string=} where
 * @property {boolean=} secret
 * @property {boolean=} paid
 * @property {boolean=} is_set
 * @property {string|null=} fingerprint
 * @property {string|null=} source
 */

/**
 * @typedef {Object} CredentialWrite
 * @property {string} value
 */

/**
 * @typedef {Object} CredentialsView
 * @property {Array<CredentialStatus>=} credentials
 * @property {SpendingStatus} spending
 * @property {boolean=} env_path_writable
 * @property {string=} note
 */

/**
 * @typedef {Object} DecisionRequest
 * @property {string} answer
 * @property {string|null=} notes
 */

/**
 * @typedef {Object} DecisionResult
 * @property {number} id
 * @property {string} status
 * @property {string|null=} decided_by
 * @property {string|null=} decided_at
 * @property {number} remaining
 */

/**
 * @typedef {Object} DiscoveryEstimate
 * What a search would cost, before running it.
 * @property {string} kind
 * @property {string} platform
 * @property {string} value
 * @property {number} limit
 * @property {string} provider
 * @property {boolean} is_fixture
 * @property {number} estimated_usd
 * @property {boolean} supported
 * @property {string|null=} unsupported_reason
 * @property {number|null=} reusable_job_id
 * @property {string|null=} reusable_started_at
 * @property {boolean=} spending_blocked
 * @property {number=} monthly_cap_usd
 * @property {number|null=} remaining_usd
 * @property {Array<string>=} coverage_warnings
 * @property {string=} recommendation
 * @property {number|null=} observed_new_ratio
 * @property {number=} observed_from_jobs
 * @property {number|null=} likely_new
 * @property {number|null=} likely_already_held
 * @property {number|null=} wasted_usd_estimate
 * @property {Object=} already_held
 * @property {Array<string>=} preflight_reasons
 * @property {Array<string>=} preflight_warnings
 */

/**
 * @typedef {Object} DiscoveryRequest
 * @property {string} kind keyword | hashtag
 * @property {string} platform
 * @property {string} value
 * @property {number=} limit
 * @property {string|null=} niche
 * @property {boolean=} force
 */

/**
 * @typedef {Object} DiscoveryResultView
 * @property {number|null} job_id
 * @property {string} status ok | reused | in_flight | refused | failed
 * @property {string} kind
 * @property {string} platform
 * @property {string} value
 * @property {string|null=} niche
 * @property {boolean=} is_fixture
 * @property {number=} publications_seen
 * @property {number=} publications_new
 * @property {number=} creators_seen
 * @property {number=} creators_new
 * @property {number|null=} estimated_usd
 * @property {number|null=} actual_usd
 * @property {Array<string>=} coverage_notes
 * @property {Array<Object>=} item_errors
 * @property {string|null=} message
 * @property {Array<CandidateView>=} candidates
 */

/**
 * @typedef {Object} ExpansionPlanView
 * @property {Array<CandidateView>=} candidates
 * @property {Array<string>=} skipped_already_fresh
 * @property {number=} total_estimated_usd
 * @property {number|null=} budget_remaining_usd
 * @property {boolean=} within_budget
 * @property {Array<string>=} notes
 */

/**
 * @typedef {Object} ExpansionRequest
 * @property {number} job_id
 * @property {Array<number>=} creator_ids
 * @property {number|null=} max_creators
 * @property {number|null=} max_spend_usd
 */

/**
 * @typedef {Object} ExtractionRequest
 * @property {Array<number>=} video_ids
 * @property {string=} scope retention | sample | all | ids
 * @property {number=} sample_size
 * @property {boolean=} include_speech
 * @property {boolean=} force
 */

/**
 * @typedef {Object} ExtractionResultView
 * @property {number|null} run_id
 * @property {string} kind
 * @property {string} method_version
 * @property {number} requested
 * @property {number} done
 * @property {number} failed
 * @property {number} features_written
 * @property {number} compute_seconds
 * @property {Array<Object>=} errors
 * @property {Array<string>=} notes
 */

/**
 * @typedef {Object} FeatureContractView
 * @property {string} version
 * @property {Object} modalities
 * @property {Array<string>} required_item_fields
 * @property {Array<Object>=} pending_requests
 */

/**
 * @typedef {Object} FeatureView
 * @property {number} id
 * @property {string} feature
 * @property {number|boolean|string|Object|Array<Object>|null=} value
 * @property {string|null=} unit
 * @property {string} method
 * @property {string} method_version
 * @property {string|null=} model
 * @property {number|null=} uncertainty
 * @property {string|null=} uncertainty_kind
 * @property {number|null=} interval_start_s
 * @property {number|null=} interval_end_s
 * @property {Object=} params
 * @property {string|null=} note
 * @property {string|null=} unavailable_reason
 * @property {string|null=} extracted_at
 * @property {number|boolean|string|null=} corrected_value
 * @property {string|null=} corrected_by
 * @property {string|null=} corrected_at
 * @property {string|null=} correction_note
 * @property {boolean=} is_corrected
 */

/**
 * @typedef {Object} FrameRef
 * What the worker was actually given, by content hash.
 * @property {number} at_s
 * @property {string} sha256
 * @property {number} bytes
 */

/**
 * @typedef {Object} FrequencyBucketView
 * @property {string} month
 * @property {number} publications
 */

/**
 * @typedef {Object} HTTPValidationError
 * @property {Array<ValidationError>=} detail
 */

/**
 * @typedef {Object} Heartbeat
 * Worker -> server: 'still alive, extend my lease'.
 * @property {number} batch_id
 * @property {string} lease_token
 * @property {string} worker_id
 * @property {number=} videos_done
 * @property {number|null=} peak_vram_mib
 */

/**
 * @typedef {Object} HookCoverage
 * @property {number} analysable
 * @property {number} deterministic_done
 * @property {number} speech_done
 * @property {number} with_retention
 * @property {number} corrections
 * @property {string} method_version
 * @property {string=} note
 */

/**
 * @typedef {Object} HookDetail
 * @property {number} video_id
 * @property {string} external_id
 * @property {string|null} posted_at
 * @property {number|null} duration_s
 * @property {string|null} caption
 * @property {string} platform
 * @property {number|null=} skip_rate
 * @property {number|null=} avg_watch_ms
 * @property {number|null=} views
 * @property {string|null=} media_url
 * @property {string|null=} media_status
 * @property {number=} hook_seconds
 * @property {Array<FeatureView>=} features
 * @property {Array<Object>=} runs
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} LeaseRequest
 * Worker -> server: ask for work it can actually run right now.
 * @property {string} worker_id
 * @property {Array<Modality>} capabilities
 * @property {number|null=} free_vram_mib
 * @property {number=} max_videos
 */

/**
 * @typedef {Object} MasterLinkView
 * @property {number} id
 * @property {number} master_id
 * @property {string} filename
 * @property {number|null=} master_duration_s
 * @property {string|null=} performance
 * @property {number|null=} score
 * @property {number|null=} frame_overlap
 * @property {number|null=} audio_similarity
 * @property {string|null=} audio_method
 * @property {number|null=} audio_offset_s
 * @property {number|null=} duration_delta_s
 * @property {number|null=} runner_up
 * @property {string} status
 * @property {string|null=} decided_by
 * @property {string|null=} decided_at
 * @property {Object|null=} evidence
 * @property {string|null=} notes
 */

/**
 * @typedef {Object} MasterReviewItem
 * A pending master link with everything needed to decide it.
 * @property {MasterLinkView} link
 * @property {VideoListItem} video
 * @property {string|null=} video_media_url
 * @property {string|null=} video_caption
 * @property {string|null=} master_local_path
 */

/**
 * @typedef {Object} MediaCoverage
 * @property {string} platform
 * @property {number} total
 * @property {boolean=} media_obtainable
 * @property {number} preservable
 * @property {number} preserved
 * @property {number|null=} coverage_pct
 * @property {Object<string, number>=} by_status
 * @property {Object<string, number>=} by_reason_class
 */

/**
 * @typedef {Object} MetricReading
 * One observation, with the age that makes it interpretable.
 * @property {string} captured_at
 * @property {number|null=} age_days
 * @property {number|null=} views
 * @property {number|null=} likes
 * @property {number|null=} comments
 * @property {number|null=} shares
 * @property {number|null=} saves
 * @property {boolean=} is_rounded
 * @property {number|null=} avg_watch_time_ms
 * @property {number|null=} total_view_time_ms
 * @property {number|null=} skip_rate_pct
 */

/** @typedef {"vision"|"transcription"|"audio"|"shots"|"screening"} Modality */

/**
 * @typedef {Object} NicheView
 * @property {string} key
 * @property {string} label
 * @property {Array<string>} keywords
 * @property {Array<string>} hashtags
 */

/**
 * @typedef {Object} Overview
 * @property {string} generated_at
 * @property {Array<PlatformSummary>} platforms
 * @property {Array<CollectionRunSummary>} recent_runs
 * @property {Array<MediaCoverage>} media
 * @property {ReviewCounts} review
 * @property {number} local_masters
 * @property {number} performances
 * @property {number} snapshot_days
 * @property {string|null=} earliest_snapshot
 * @property {string|null=} latest_snapshot
 */

/**
 * @typedef {Object} PairSide
 * @property {number} video_id
 * @property {string} platform
 * @property {string} external_id
 * @property {string|null=} posted_at
 * @property {number|null=} duration_s
 * @property {string|null=} caption
 * @property {string|null=} media_url
 * @property {string|null=} cover_url
 * @property {number|null=} latest_views
 * @property {number|null=} latest_age_days
 * @property {string|null=} master_filename
 * @property {string|null=} performance
 */

/**
 * @typedef {Object} PerformanceSummary
 * Typical performance, with the caveats as part of the payload.
 * @property {number} publications
 * @property {number} with_views
 * @property {string|null=} first_published
 * @property {string|null=} last_published
 * @property {string|null=} observed_at
 * @property {number|null=} median_views
 * @property {number|null=} median_views_recent
 * @property {number|null=} top_views
 * @property {number|null=} median_likes
 * @property {number|null=} age_span_days
 * @property {boolean=} counts_rounded
 * @property {Array<string>=} caveats
 */

/**
 * @typedef {Object} PlatformSummary
 * @property {string} platform
 * @property {string|null=} account_ref
 * @property {string|null=} handle
 * @property {boolean} connected
 * @property {boolean} needs_reauth
 * @property {string|null=} token_expires_at
 * @property {number} publications
 * @property {number} snapshots
 * @property {string|null=} first_published
 * @property {string|null=} last_published
 */

/**
 * @typedef {Object} PolicyView
 * @property {string} mode
 * @property {boolean} escalation_authorised
 * @property {Array<string>=} authorised_providers
 * @property {number} max_usd_per_request
 * @property {number} escalate_below_confidence
 * @property {boolean} allow_third_party_media_to_cloud
 * @property {number} monthly_cap_usd
 * @property {Array<SpendingCategoryView>=} categories
 * @property {Array<string>=} modes
 * @property {string=} note
 */

/**
 * @typedef {Object} ProviderCapabilityView
 * @property {string} platform
 * @property {string} provider
 * @property {Array<string>=} supported_queries
 * @property {boolean} follower_count
 * @property {boolean} creator_bio
 * @property {boolean} view_count
 * @property {boolean} views_rounded
 * @property {boolean} saves_count
 * @property {boolean} media_url
 * @property {boolean} full_history
 * @property {Array<string>=} missing_creator_fields
 * @property {Array<string>=} coverage_notes
 */

/**
 * @typedef {Object} ProviderRow
 * @property {string} provider
 * @property {string} model
 * @property {string} hosting
 * @property {boolean} available
 * @property {string|null=} unavailable_reason
 * @property {Array<string>=} accepts
 * @property {Array<string>=} supported_tasks
 * @property {boolean=} accepts_video_natively
 * @property {number|null=} max_images_per_request
 * @property {number|null=} max_image_bytes
 * @property {boolean=} structured_output
 * @property {Array<string>=} documented_limits
 * @property {number|null=} usd_per_mtok_in
 * @property {number|null=} usd_per_mtok_out
 * @property {string=} verified_on
 * @property {string=} source_url
 * @property {Array<string>=} notes
 */

/**
 * @typedef {Object} PublicationLinkView
 * @property {number} id
 * @property {number} other_video_id
 * @property {string} other_platform
 * @property {string} other_external_id
 * @property {string|null=} other_posted_at
 * @property {number|null=} other_duration_s
 * @property {number|null=} other_latest_views
 * @property {number|null=} other_latest_age_days
 * @property {string} relationship_type
 * @property {string} status
 * @property {number|null=} score
 * @property {string|null=} decided_by
 * @property {Object|null=} evidence
 */

/**
 * @typedef {Object} PublicationPairView
 * A cross-platform pair as the review queue needs it: both sides in
 * @property {number} id
 * @property {string} relationship_type
 * @property {string} status
 * @property {number|null=} score
 * @property {string|null=} decided_by
 * @property {Object|null=} evidence
 * @property {PairSide} a
 * @property {PairSide} b
 */

/**
 * @typedef {Object} ReportSectionView
 * @property {string} key
 * @property {string} title
 * @property {string} basis
 * @property {Object=} body
 * @property {Array<string>=} caveats
 * @property {string|null=} unavailable_reason
 */

/**
 * @typedef {Object} ResearchJobView
 * @property {number} id
 * @property {string} kind
 * @property {string} platform
 * @property {string} query_value
 * @property {string} provider
 * @property {boolean} is_fixture
 * @property {string} status
 * @property {string} started_at
 * @property {string|null=} finished_at
 * @property {number|null=} estimated_usd
 * @property {number|null=} actual_usd
 * @property {number=} creators_new
 * @property {number=} publications_seen
 * @property {number=} publications_new
 * @property {number=} snapshots_written
 * @property {Array<string>=} coverage_notes
 * @property {Array<Object>=} item_errors
 * @property {string|null=} error
 */

/**
 * @typedef {Object} ResearchPublicationPage
 * @property {number} total
 * @property {number} offset
 * @property {number} limit
 * @property {Array<ResearchPublicationRow>=} items
 * @property {Array<string>=} notes
 */

/**
 * @typedef {Object} ResearchPublicationRow
 * @property {number} video_id
 * @property {string} external_id
 * @property {string|null=} posted_at
 * @property {string|null=} caption
 * @property {Array<string>=} hashtags
 * @property {number|null=} duration_s
 * @property {string|null=} url
 * @property {string|null=} thumbnail_url
 * @property {number|null=} views
 * @property {number|null=} likes
 * @property {number|null=} comments
 * @property {number|null=} shares
 * @property {number|null=} saves
 * @property {boolean=} counts_rounded
 * @property {string|null=} observed_at
 * @property {number|null=} age_days
 * @property {number|null=} relative_performance
 * @property {number|null=} robust_z
 * @property {string|null=} music_title
 * @property {string|null=} music_artist
 * @property {string|null=} breakthrough
 * @property {boolean=} is_fixture
 */

/**
 * @typedef {Object} ResearchRequest
 * @property {string} platform
 * @property {string} handle
 * @property {number=} limit
 * @property {boolean=} refresh
 */

/**
 * @typedef {Object} ResearchResultView
 * @property {string} status
 * @property {string|null=} message
 * @property {number|null=} creator_id
 * @property {ResearchJobView|null=} job
 * @property {boolean=} reused
 * @property {boolean=} is_fixture
 * @property {number=} breakthroughs_found
 */

/**
 * @typedef {Object} ResearchedCreatorDetail
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null=} display_name
 * @property {string|null=} profile_url
 * @property {string|null=} bio
 * @property {number|null=} follower_count
 * @property {Array<Object>=} follower_observations
 * @property {string} research_status
 * @property {string|null=} last_refreshed_at
 * @property {boolean=} is_stale
 * @property {boolean=} is_fixture
 * @property {string} source
 * @property {string|null=} discovered_via
 * @property {number|null=} refresh_cost_usd
 * @property {PerformanceSummary} summary
 * @property {ProviderCapabilityView|null=} capabilities
 * @property {ResearchJobView|null=} last_job
 */

/**
 * @typedef {Object} ResearchedCreatorRow
 * One researched account, for the research-history list.
 * @property {number} creator_id
 * @property {string} platform
 * @property {string} handle
 * @property {string|null=} display_name
 * @property {number|null=} follower_count
 * @property {number=} publications_collected
 * @property {string} research_status
 * @property {string|null=} last_refreshed_at
 * @property {boolean=} is_stale
 * @property {number|null=} refresh_cost_usd
 * @property {boolean=} is_fixture
 * @property {string} source
 * @property {number=} breakthrough_count
 * @property {number|null=} collection_cost_usd
 */

/**
 * @typedef {Object} ResultSubmission
 * Worker -> server: commit results for part or all of a leased batch.
 * @property {number} batch_id
 * @property {string} lease_token
 * @property {string} worker_id
 * @property {Array<VideoResult>} results
 * @property {boolean=} final
 * @property {string|null=} partial_reason
 * @property {number|null=} batch_peak_vram_mib
 */

/**
 * @typedef {Object} RetentionReportView
 * @property {string} outcome
 * @property {string} outcome_note
 * @property {number} n_videos
 * @property {number} n_with_media
 * @property {number} n_with_features
 * @property {number} n_analysed
 * @property {Array<number>|null=} duration_range_s
 * @property {Array<number>|null=} age_range_days
 * @property {Array<number>|null=} skip_range
 * @property {Array<AssociationView>=} associations
 * @property {Array<string>=} survivors
 * @property {Array<Object>=} survivor_collinearity
 * @property {string} method_version
 * @property {string} verdict
 * @property {Array<string>=} limitations
 */

/**
 * @typedef {Object} ReviewCounts
 * @property {number} master_links_pending
 * @property {number} master_links_auto_unverified
 * @property {number} master_links_human_decided
 * @property {number} publication_links_proposed
 * @property {number} publication_links_confirmed
 * @property {number} performances_inferred
 * @property {number} performances_confirmed
 */

/**
 * @typedef {Object} SearchHistoryRow
 * @property {number} job_id
 * @property {string} kind
 * @property {string} platform
 * @property {string} value
 * @property {string|null} niche
 * @property {string} provider
 * @property {boolean} is_fixture
 * @property {string} status
 * @property {string|null} started_at
 * @property {number} publications
 * @property {number} creators
 * @property {number|null} estimated_usd
 * @property {number|null} actual_usd
 * @property {boolean=} reusable
 */

/**
 * @typedef {Object} SessionRequest
 * @property {string} password
 */

/**
 * @typedef {Object} SessionState
 * @property {boolean} authenticated
 * @property {boolean} auth_configured
 */

/**
 * @typedef {Object} SpendingCategoryView
 * @property {string} category
 * @property {number} cap_usd
 * @property {boolean} authorised
 * @property {number=} committed_usd
 * @property {number=} remaining_usd
 * @property {string=} note
 */

/**
 * @typedef {Object} SpendingState
 * Whether research may cost money, and how much is left.
 * @property {boolean} spending_blocked
 * @property {number} monthly_cap_usd
 * @property {number} committed_usd
 * @property {number} available_usd
 * @property {Array<string>=} providers_authorised
 * @property {string} period
 * @property {string} active_provider
 * @property {boolean} fixture_mode
 * @property {Array<Object>=} available_fixtures
 */

/**
 * @typedef {Object} SpendingStatus
 * @property {number=} monthly_cap_usd
 * @property {Array<string>=} providers_authorised
 * @property {Object<string, number>=} category_caps_usd
 * @property {boolean=} any_authorised
 * @property {string=} research_provider
 * @property {string=} research_reason
 */

/**
 * @typedef {Object} SpendingWrite
 * Authorising money. Both fields are required, deliberately.
 * @property {number} monthly_cap_usd
 * @property {Array<string>=} providers_authorised
 * @property {Object<string, number>|null=} category_caps_usd
 * @property {boolean=} confirm
 */

/**
 * @typedef {Object} SustainedRunView
 * @property {string} kind
 * @property {number} start_index
 * @property {number} end_index
 * @property {string} start_at
 * @property {string} end_at
 * @property {number} publications
 * @property {number} median_relative
 */

/** @typedef {"1"|"2"|"3"} Tier */

/**
 * @typedef {Object} TimelinePointView
 * @property {number} video_id
 * @property {string} external_id
 * @property {string} published_at
 * @property {number|null} views
 * @property {string|null} observed_at
 * @property {number|null} age_days
 * @property {number|null=} views_at_anchor
 * @property {number|null=} anchor_days
 * @property {string|null=} anchor_method
 * @property {string|null=} anchor_unavailable_reason
 * @property {number|null=} relative_performance
 * @property {number|null=} robust_z
 * @property {number|null=} baseline_views
 * @property {boolean=} is_outlier_candidate
 * @property {boolean=} is_change_point
 * @property {number=} observation_count
 * @property {string|null=} caption
 * @property {number|null=} duration_s
 * @property {string|null=} url
 * @property {string|null=} thumbnail_url
 * @property {boolean=} media_preview_available
 * @property {string|null=} media_preview_note
 * @property {boolean=} counts_rounded
 */

/**
 * @typedef {Object} ValidationError
 * @property {Array<string|number>} loc
 * @property {string} msg
 * @property {string} type
 * @property {Object=} input
 * @property {Object=} ctx
 */

/**
 * @typedef {Object} VideoDetail
 * @property {number} id
 * @property {string} platform
 * @property {string} external_id
 * @property {string|null=} caption
 * @property {Array<string>=} hashtags
 * @property {string|null=} posted_at
 * @property {number|null=} duration_s
 * @property {string} source
 * @property {string} media_status
 * @property {string|null=} media_reason
 * @property {string|null=} media_url
 * @property {string|null=} cover_url
 * @property {string|null=} creator_handle
 * @property {boolean} is_own_account
 * @property {Array<MetricReading>} history
 * @property {Array<MasterLinkView>} master_links
 * @property {Array<PublicationLinkView>} publication_links
 * @property {Object<string, string|number|null>=} anchors
 */

/**
 * @typedef {Object} VideoListItem
 * @property {number} id
 * @property {string} platform
 * @property {string} external_id
 * @property {string|null=} caption_preview
 * @property {string|null=} posted_at
 * @property {number|null=} duration_s
 * @property {number|null=} latest_views
 * @property {number|null=} latest_age_days
 * @property {boolean} has_media
 * @property {string} media_status
 * @property {number} master_links
 * @property {boolean} master_link_verified
 * @property {number} cross_platform_links
 * @property {string|null=} performance
 */

/**
 * @typedef {Object} VideoPage
 * @property {number} total
 * @property {number} offset
 * @property {number} limit
 * @property {Array<VideoListItem>} items
 */

/**
 * @typedef {Object} VideoRef
 * One unit of work inside a batch.
 * @property {number} video_id
 * @property {string} media_url
 * @property {number|null=} duration_s
 * @property {number=} frames
 */

/**
 * @typedef {Object} VideoResult
 * One video's analysis output, committed independently of its siblings.
 * @property {number} video_id
 * @property {string} idempotency_key
 * @property {Object=} payload
 * @property {string|null=} error
 * @property {number|null=} compute_seconds
 * @property {number|null=} peak_vram_mib
 */

/**
 * @typedef {Object} VisionPendingItem
 * @property {number} video_id
 * @property {string} external_id
 * @property {number|null=} duration_s
 * @property {string|null=} posted_at
 */

/**
 * @typedef {Object} VisionPendingList
 * @property {Array<VisionPendingItem>=} items
 * @property {number=} total_analysable
 * @property {number=} already_done
 * @property {string} method_version
 */

/**
 * @typedef {Object} VisionQueueView
 * The queue, plus whether the machine that drains it is awake.
 * @property {Array<VisionRequestRow>=} requests
 * @property {Object<string, number>=} counts
 * @property {Object=} worker
 * @property {string} method_version
 * @property {number=} analysed
 */

/**
 * @typedef {Object} VisionRequestBody
 * Ask for one publication's opening to be analysed visually.
 * @property {number} video_id
 * @property {boolean=} force
 */

/**
 * @typedef {Object} VisionRequestRow
 * @property {number} id
 * @property {number} video_id
 * @property {string|null=} external_id
 * @property {string} task
 * @property {string} method_version
 * @property {string} status
 * @property {string} mode
 * @property {boolean=} force
 * @property {number=} attempts
 * @property {string|null=} claimed_by
 * @property {string|null=} requested_at
 * @property {string|null=} started_at
 * @property {string|null=} finished_at
 * @property {string|null=} error
 */

/**
 * @typedef {Object} VisionResultBody
 * @property {number} video_id
 * @property {string=} status
 * @property {string|null=} task
 * @property {string|null=} provider
 * @property {string|null=} model
 * @property {string|null=} hosting
 * @property {Object|null=} data
 * @property {string|null=} raw_text
 * @property {boolean|null=} schema_valid
 * @property {Array<string>=} validation_errors
 * @property {string|null=} refused_reason
 * @property {string|null=} error
 * @property {number|null=} input_tokens
 * @property {number|null=} output_tokens
 * @property {number|null=} images
 * @property {number|null=} latency_s
 * @property {number|null=} peak_vram_mib
 * @property {Array<FrameRef>=} frames
 * @property {string|null=} media_sha256
 */

/**
 * @typedef {Object} VisionResultView
 * @property {number} video_id
 * @property {string} status
 * @property {number} features_written
 * @property {string} method_version
 * @property {number|null=} run_id
 * @property {number|null=} operation_id
 */

/**
 * @typedef {Object} VisionTaskView
 * @property {string} task
 * @property {Object=} schema
 * @property {string} prompt
 * @property {number} hook_seconds
 * @property {number} hook_frames
 * @property {Array<number>=} later_offsets_s
 * @property {Array<string>=} refusals
 */

/**
 * @typedef {Object} WorkerStatus
 * Worker -> server: why it is not taking work, so the dashboard can show
 * @property {string} worker_id
 * @property {string} state
 * @property {string|null=} reason
 * @property {number|null=} free_vram_mib
 * @property {number|null=} total_vram_mib
 */

export {};
