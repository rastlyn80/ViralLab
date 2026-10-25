"""Competitor intelligence: identities, discovery jobs, breakthrough candidates

Extends what exists rather than duplicating it, per docs/SCHEMA_PLAN.md:

  * `creators` already models one account on one platform, and every
    collector depends on that shape. Identity *above* it is new
    (`creator_identities`), so one person's TikTok and Instagram accounts can
    be tied together on evidence without restructuring anything.
  * `videos` already models a publication; discovered publications are the
    same kind of thing from a different source, so they get columns rather
    than a parallel table. A separate `competitor_videos` would guarantee
    the two populations drift apart and get analysed inconsistently.
  * `metrics_snapshots` is already append-only with `captured_at`, which is
    exactly what repeated competitor observations need. Unchanged.

New, because nothing existing covers them:

  * `discovery_jobs` - one search, with its query, provider, provenance,
    estimated and actual cost, and the coverage limits of what it returned.
  * `creator_identities` / `creator_identity_members` - identity, and the
    evidence tying each account to it.
  * `breakthrough_candidates` - where a creator's trajectory may have
    changed, with a status that distinguishes an observed outlier from an
    inferred breakthrough from a verified trajectory change.

Revision ID: d9f4a2b76c81
Revises: c3e8b47d105a
Create Date: 2026-10-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9f4a2b76c81"
down_revision: Union[str, Sequence[str], None] = "c3e8b47d105a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- discovery jobs -------------------------------------------------
    op.create_table(
        "discovery_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        # creator | keyword | hashtag
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        # The normalised query, so re-running is recognisable as the same
        # request and does not pay twice.
        sa.Column("query_value", sa.Text(), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False,
                  server_default="50"),
        sa.Column("provider", sa.String(length=64), nullable=False),
        # True when served from committed synthetic fixtures. Carried all
        # the way to the interface so a demonstration can never be mistaken
        # for collected data.
        sa.Column("is_fixture", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        # queued | running | ok | failed | refused
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_usd", sa.Float(), nullable=True),
        sa.Column("actual_usd", sa.Float(), nullable=True),
        sa.Column("reservation_id", sa.Integer(), nullable=True),
        sa.Column("creators_seen", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("creators_new", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("publications_seen", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("publications_new", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("snapshots_written", sa.Integer(), nullable=False,
                  server_default="0"),
        # What the source could NOT see. A partial result treated as
        # complete is the most dangerous kind of data.
        sa.Column("coverage_notes", postgresql.JSONB(), nullable=True),
        # Per-item provider failures, kept rather than dropped: a private
        # account must not look like an empty one.
        sa.Column("item_errors", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["reservation_id"],
                                ["budget_reservations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind in ('creator','keyword','hashtag')",
                           name="ck_discovery_job_kind"),
        sa.CheckConstraint(
            "status in ('queued','running','ok','failed','refused')",
            name="ck_discovery_job_status"),
    )
    op.create_index("ix_discovery_jobs_lookup", "discovery_jobs",
                    ["platform", "kind", "query_value", "started_at"])

    # ---- creator identity -----------------------------------------------
    op.create_table(
        "creator_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        # inferred | confirmed. An identity grouping is a claim about a
        # person, so it is never silently promoted.
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="inferred"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status in ('inferred','confirmed')",
                           name="ck_creator_identity_status"),
    )
    op.create_table(
        "creator_identity_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        # bio_link | cross_posted_content | name_and_content | manual |
        # self_declared. A coincidental handle match is deliberately NOT a
        # value here: it is not evidence.
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["identity_id"], ["creator_identities.id"]),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One account belongs to at most one identity.
        sa.UniqueConstraint("creator_id", name="uq_identity_member_creator"),
        sa.CheckConstraint(
            "evidence_kind in ('bio_link','cross_posted_content',"
            "'name_and_content','manual','self_declared')",
            name="ck_identity_evidence_kind"),
        sa.CheckConstraint("decided_by is null or "
                           "decided_by in ('human','matcher')",
                           name="ck_identity_decided_by"),
    )
    op.create_index("ix_identity_members_identity",
                    "creator_identity_members", ["identity_id"])

    # ---- research state on the existing creators table ------------------
    # Extending rather than adding a table: `creators` already IS the
    # platform account, so its research state belongs on it.
    op.add_column("creators", sa.Column(
        "research_status", sa.String(length=16), nullable=False,
        server_default="none"))
    op.add_column("creators", sa.Column(
        "publications_collected", sa.Integer(), nullable=False,
        server_default="0"))
    op.add_column("creators", sa.Column(
        "last_job_id", sa.Integer(), nullable=True))
    op.add_column("creators", sa.Column(
        "collection_cost_usd", sa.Float(), nullable=True))
    op.add_column("creators", sa.Column(
        "is_fixture", sa.Boolean(), nullable=False,
        server_default=sa.text("false")))
    op.create_foreign_key("fk_creators_last_job", "creators",
                          "discovery_jobs", ["last_job_id"], ["id"])
    op.create_check_constraint(
        "ck_creator_research_status", "creators",
        "research_status in ('none','pending','collected','partial',"
        "'failed')")
    op.execute("update creators set research_status = 'collected' "
               "where is_own_account")

    # ---- discovered publications ----------------------------------------
    op.add_column("videos", sa.Column(
        "discovery_job_id", sa.Integer(), nullable=True))
    op.add_column("videos", sa.Column(
        "thumbnail_url", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column(
        "music", postgresql.JSONB(), nullable=True))
    op.add_column("videos", sa.Column(
        "provenance", postgresql.JSONB(), nullable=True))
    op.add_column("videos", sa.Column(
        "is_fixture", sa.Boolean(), nullable=False,
        server_default=sa.text("false")))
    op.create_foreign_key("fk_videos_discovery_job", "videos",
                          "discovery_jobs", ["discovery_job_id"], ["id"])
    op.create_index("ix_videos_discovery_job", "videos",
                    ["discovery_job_id"])

    # `source` gains the discovery values. The old constraint, if any, is
    # replaced rather than extended in place.
    op.execute("update videos set source = 'official_api' "
               "where source is null")

    # ---- breakthrough candidates ----------------------------------------
    op.create_table(
        "breakthrough_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        # The publication at the change point, where one can be named. A
        # gradual regime change has no single video, so this is nullable.
        sa.Column("video_id", sa.Integer(), nullable=True),
        # observed_outlier     - this post beat the creator's own baseline
        # inferred_breakthrough - the trajectory appears to shift here, but
        #                         the longitudinal evidence to prove it does
        #                         not exist
        # verified_trajectory_change - genuine repeated observations show a
        #                         sustained change
        # rejected             - a human looked and said no
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("method_version", sa.String(length=32), nullable=False),
        # Where in the creator's timeline, and what the numbers were.
        sa.Column("publication_index", sa.Integer(), nullable=True),
        sa.Column("baseline_views", sa.Float(), nullable=True),
        sa.Column("baseline_mad", sa.Float(), nullable=True),
        sa.Column("observed_views", sa.BigInteger(), nullable=True),
        sa.Column("relative_performance", sa.Float(), nullable=True),
        sa.Column("robust_z", sa.Float(), nullable=True),
        sa.Column("window_before", sa.Integer(), nullable=True),
        sa.Column("window_after", sa.Integer(), nullable=True),
        sa.Column("median_after", sa.Float(), nullable=True),
        sa.Column("sustained", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        # What supports the classification, and - crucially - what is
        # missing. An inferred breakthrough must say which evidence it
        # lacked, so nobody later reads it as established.
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("missing_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"]),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("creator_id", "video_id", "method_version",
                            name="uq_breakthrough_candidate"),
        sa.CheckConstraint(
            "classification in ('observed_outlier','inferred_breakthrough',"
            "'verified_trajectory_change','rejected')",
            name="ck_breakthrough_classification"),
        sa.CheckConstraint("decided_by is null or "
                           "decided_by in ('human','matcher')",
                           name="ck_breakthrough_decided_by"),
    )
    op.create_index("ix_breakthrough_creator", "breakthrough_candidates",
                    ["creator_id", "classification"])


def downgrade() -> None:
    op.drop_index("ix_breakthrough_creator",
                  table_name="breakthrough_candidates")
    op.drop_table("breakthrough_candidates")

    op.drop_index("ix_videos_discovery_job", table_name="videos")
    op.drop_constraint("fk_videos_discovery_job", "videos",
                       type_="foreignkey")
    for col in ("is_fixture", "provenance", "music", "thumbnail_url",
                "discovery_job_id"):
        op.drop_column("videos", col)

    op.drop_constraint("ck_creator_research_status", "creators",
                       type_="check")
    op.drop_constraint("fk_creators_last_job", "creators",
                       type_="foreignkey")
    for col in ("is_fixture", "collection_cost_usd", "last_job_id",
                "publications_collected", "research_status"):
        op.drop_column("creators", col)

    op.drop_index("ix_identity_members_identity",
                  table_name="creator_identity_members")
    op.drop_table("creator_identity_members")
    op.drop_table("creator_identities")

    op.drop_index("ix_discovery_jobs_lookup", table_name="discovery_jobs")
    op.drop_table("discovery_jobs")
