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
 * @typedef {Object} SessionRequest
 * @property {string} password
 */

/**
 * @typedef {Object} SessionState
 * @property {boolean} authenticated
 * @property {boolean} auth_configured
 */

/** @typedef {"1"|"2"|"3"} Tier */

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
 * @typedef {Object} WorkerStatus
 * Worker -> server: why it is not taking work, so the dashboard can show
 * @property {string} worker_id
 * @property {string} state
 * @property {string|null=} reason
 * @property {number|null=} free_vram_mib
 * @property {number|null=} total_vram_mib
 */

export {};
