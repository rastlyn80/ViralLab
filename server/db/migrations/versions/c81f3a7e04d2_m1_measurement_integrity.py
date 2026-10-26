"""M1 measurement integrity: surface decomposition, ad exposures, imports

Three additions, all serving one purpose: make it possible to say how much
of a publication's exposure was paid, and to get retention figures for the
platform whose API has none.

1. `metrics_snapshots.total_views / facebook_views / crossposted_views`.
   Meta documents `views` as organic-only, so the paid share is not inside
   it and cannot be subtracted out of it — it is the *difference* against
   `total_views` (ADR 0008). The residual is derived at read time, never
   stored: this table stays append-only and holds only what the platform
   returned.

2. `ad_exposures`. The paid side proper, once an `ads_read` token exists.
   Empty until then, by design.

3. `analytics_imports` + `imported_metrics`. Owner-supplied TikTok Studio
   and Instagram Insights exports. The only route to TikTok retention that
   exists at all.

Revision ID: c81f3a7e04d2
Revises: b8e3c25d9f47
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c81f3a7e04d2"
down_revision: Union[str, Sequence[str], None] = "b8e3c25d9f47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- 1. surface decomposition on the existing snapshot table ----------
    # Nullable with no default on purpose. A zero here would read as "no
    # paid exposure", and the truth for every row collected before today is
    # "not measured". Those are not the same thing and the analysis treats
    # them differently.
    op.add_column("metrics_snapshots",
                  sa.Column("total_views", sa.BigInteger(), nullable=True))
    op.add_column("metrics_snapshots",
                  sa.Column("facebook_views", sa.BigInteger(), nullable=True))
    op.add_column("metrics_snapshots",
                  sa.Column("crossposted_views", sa.BigInteger(),
                            nullable=True))

    # -- 2. the paid side -------------------------------------------------
    op.create_table(
        "ad_exposures",
        sa.Column("id", sa.Integer, primary_key=True),
        # SET NULL, not CASCADE: an ad row records spend, and spend should
        # survive the deletion of the content it promoted.
        sa.Column("video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("platform", sa.String(32), nullable=False,
                  server_default="instagram"),
        sa.Column("ad_account_id", sa.String(64), nullable=False),
        sa.Column("ad_id", sa.String(64), nullable=False),
        sa.Column("adset_id", sa.String(64), nullable=True),
        sa.Column("campaign_id", sa.String(64), nullable=True),
        sa.Column("effective_instagram_media_id", sa.String(64),
                  nullable=True),
        sa.Column("date_start", sa.Date, nullable=False),
        sa.Column("date_stop", sa.Date, nullable=False),
        sa.Column("spend", sa.Numeric(12, 4), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("impressions", sa.BigInteger, nullable=True),
        sa.Column("reach", sa.BigInteger, nullable=True),
        sa.Column("video_p25", sa.BigInteger, nullable=True),
        sa.Column("video_p50", sa.BigInteger, nullable=True),
        sa.Column("video_p75", sa.BigInteger, nullable=True),
        sa.Column("video_p95", sa.BigInteger, nullable=True),
        sa.Column("video_p100", sa.BigInteger, nullable=True),
        sa.Column("video_avg_time_watched_s", sa.Float, nullable=True),
        sa.Column("raw", JSONB, nullable=True),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_ad_exposure_idem"),
    )
    op.create_index("ix_ad_exposures_video", "ad_exposures",
                    ["video_id", "date_start"])
    op.create_index("ix_ad_exposures_media", "ad_exposures",
                    ["effective_instagram_media_id"])

    # -- 3. manual analytics imports --------------------------------------
    op.create_table(
        "analytics_imports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("exported_at", sa.Date, nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("matched_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("parse_notes", JSONB, nullable=True),
        sa.UniqueConstraint("file_sha256", name="uq_analytics_import_sha"),
        sa.CheckConstraint("source in ('tiktok_studio','instagram_insights')",
                           name="ck_analytics_import_source"),
    )
    op.create_index("ix_analytics_imports_platform", "analytics_imports",
                    ["platform", "exported_at"])

    op.create_table(
        "imported_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("import_id", sa.Integer,
                  sa.ForeignKey("analytics_imports.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_ref", sa.Text, nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_age_days", sa.Float, nullable=True),
        sa.Column("provenance", sa.String(32), nullable=False,
                  server_default="manual_export"),
        sa.Column("raw", JSONB, nullable=True),
        sa.UniqueConstraint("import_id", "external_ref", "metric",
                            name="uq_imported_metric"),
        sa.CheckConstraint("provenance = 'manual_export'",
                           name="ck_imported_metric_provenance"),
    )
    op.create_index("ix_imported_metrics_video", "imported_metrics",
                    ["video_id", "metric"])


def downgrade() -> None:
    op.drop_index("ix_imported_metrics_video", table_name="imported_metrics")
    op.drop_table("imported_metrics")
    op.drop_index("ix_analytics_imports_platform",
                  table_name="analytics_imports")
    op.drop_table("analytics_imports")
    op.drop_index("ix_ad_exposures_media", table_name="ad_exposures")
    op.drop_index("ix_ad_exposures_video", table_name="ad_exposures")
    op.drop_table("ad_exposures")
    op.drop_column("metrics_snapshots", "crossposted_views")
    op.drop_column("metrics_snapshots", "facebook_views")
    op.drop_column("metrics_snapshots", "total_views")
