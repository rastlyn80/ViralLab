"""Versioned video feature evidence

One row per (video, feature, method_version). That grain is the whole point:

  * **reprocessing never overwrites.** A new `method_version` writes new
    rows beside the old ones, so a threshold change or a model swap leaves
    the previous evidence intact and comparable. The unique key includes
    the version precisely so an upsert cannot silently rewrite history.

  * **a measurement and an interpretation are different rows with
    different `method`.** `deterministic` is signal processing on the file
    and is reproducible to the bit; `proxy` is a threshold on such a
    measurement; `model` is a model's answer; `human` is a correction.
    Nothing averages across them.

  * **a human correction is additive, not destructive.** It lands in its
    own columns on the row it corrects, so what the machine said and what
    the person said are both still there. `docs/PRODUCT_VISION.md` requires
    a correction mechanism; a correction that erased the original would
    make it impossible to ever measure how often the machine was wrong.

`video_feature_runs` records the extraction pass itself - which videos,
which versions, how long it took, what failed - so a partially completed
run is visible rather than inferred from gaps.

Revision ID: f1a6c8d27b93
Revises: e7c2b94f83da
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f1a6c8d27b93"
down_revision = "e7c2b94f83da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_feature_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("videos_requested", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("videos_done", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("videos_failed", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("features_written", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("compute_seconds", sa.Float(), nullable=True),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('running','ok','partial','failed')",
            name="ck_feature_run_status"),
    )

    op.create_table(
        "video_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=False),
        # The exact file measured. A re-download can change the bytes under
        # a publication, and a feature measured from different bytes is a
        # different measurement.
        sa.Column("media_sha256", sa.String(64), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("feature", sa.String(64), nullable=False),
        # Exactly one of these carries the value, so a number is never
        # stringified and a string is never coerced to a number.
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_json", JSONB(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        # deterministic | proxy | model | human | unavailable
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("uncertainty", sa.Float(), nullable=True),
        sa.Column("uncertainty_kind", sa.String(24), nullable=True),
        # The window within the video this describes.
        sa.Column("interval_start_s", sa.Float(), nullable=True),
        sa.Column("interval_end_s", sa.Float(), nullable=True),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("video_feature_runs.id"), nullable=True),
        # ok | failed | superseded
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        # --- human correction, additive -------------------------------
        sa.Column("corrected_value_num", sa.Float(), nullable=True),
        sa.Column("corrected_value_bool", sa.Boolean(), nullable=True),
        sa.Column("corrected_value_text", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.String(32), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "method in ('deterministic','proxy','model','human',"
            "'unavailable')", name="ck_feature_method"),
        sa.CheckConstraint(
            "status in ('ok','failed','superseded')",
            name="ck_feature_status"),
        # The grain. Including method_version is what makes reprocessing
        # additive instead of destructive.
        sa.UniqueConstraint("video_id", "feature", "method_version",
                            name="uq_video_feature_version"),
    )
    op.create_index("ix_video_features_video", "video_features",
                    ["video_id", "method_version"])
    op.create_index("ix_video_features_feature", "video_features",
                    ["feature", "method_version"])
    op.create_index("ix_video_features_corrected", "video_features",
                    ["corrected_at"])


def downgrade() -> None:
    op.drop_index("ix_video_features_corrected", table_name="video_features")
    op.drop_index("ix_video_features_feature", table_name="video_features")
    op.drop_index("ix_video_features_video", table_name="video_features")
    op.drop_table("video_features")
    op.drop_table("video_feature_runs")
