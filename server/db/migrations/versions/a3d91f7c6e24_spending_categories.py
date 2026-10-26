"""Spending categories for AI inference

The budget system was built for one kind of charge: a discovery provider
returning rows. Phase 6B adds a second and third - cloud visual analysis and
cloud audio analysis - and Phase 8 will add creative-strategy generation.
Those behave nothing alike. Discovery costs cents per thousand items and
runs occasionally; visual analysis costs fractions of a cent per image and
could run across hundreds of videos in an afternoon. A single monthly cap
cannot express "I will pay for discovery but not for frame analysis", which
is exactly the control the owner needs.

So `category` joins `provider` as part of the budget grain. The per-period
advisory lock and the committed-spend sum both take it into account, which
means one category cannot consume another's allowance.

Backfilled to `discovery` rather than NULL: every existing reservation was a
discovery charge, and leaving them uncategorised would have excluded them
from the sums that the caps are checked against.

Revision ID: a3d91f7c6e24
Revises: f1a6c8d27b93
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3d91f7c6e24"
down_revision = "f1a6c8d27b93"
branch_labels = None
depends_on = None

CATEGORIES = ("discovery", "cloud_vision", "cloud_audio", "strategy")


def upgrade() -> None:
    op.add_column(
        "budget_reservations",
        sa.Column("category", sa.String(32), nullable=False,
                  server_default="discovery"))
    op.create_check_constraint(
        "ck_reservation_category", "budget_reservations",
        "category in ('discovery','cloud_vision','cloud_audio','strategy')")
    # The existing unique key is (provider, idempotency_key). Category does
    # not join it: an idempotency key is already unique per provider, and
    # adding category would let the same logical call be billed once per
    # category, which is the opposite of what the key is for.
    op.create_index("ix_reservations_category", "budget_reservations",
                    ["provider", "category", "period"])

    # Operation-level record of what an AI call actually did and cost.
    # Separate from `budget_reservations`, which is about money held;
    # this is about work performed, and the two have different lifetimes.
    op.create_table(
        "ai_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("hosting", sa.String(8), nullable=False),
        sa.Column("category", sa.String(32), nullable=False,
                  server_default="cloud_vision"),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ok"),
        sa.Column("schema_valid", sa.Boolean(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("refused_reason", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("images", sa.Integer(), nullable=True),
        sa.Column("estimated_usd", sa.Float(), nullable=True),
        # What the provider billed. An estimate is never promoted here.
        sa.Column("actual_usd", sa.Float(), nullable=True),
        sa.Column("latency_s", sa.Float(), nullable=True),
        sa.Column("peak_vram_mib", sa.Integer(), nullable=True),
        sa.Column("reservation_id", sa.Integer(),
                  sa.ForeignKey("budget_reservations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("hosting in ('local','cloud')",
                           name="ck_ai_operation_hosting"),
        sa.CheckConstraint(
            "status in ('ok','refused','failed','invalid')",
            name="ck_ai_operation_status"),
    )
    op.create_index("ix_ai_operations_video", "ai_operations",
                    ["video_id", "task"])
    op.create_index("ix_ai_operations_provider", "ai_operations",
                    ["provider", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_operations_provider", table_name="ai_operations")
    op.drop_index("ix_ai_operations_video", table_name="ai_operations")
    op.drop_table("ai_operations")
    op.drop_index("ix_reservations_category",
                  table_name="budget_reservations")
    op.drop_constraint("ck_reservation_category", "budget_reservations",
                       type_="check")
    op.drop_column("budget_reservations", "category")
