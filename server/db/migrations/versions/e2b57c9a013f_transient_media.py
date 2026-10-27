"""transient_media: the six-hour ledger for competitor bytes

`docs/MULTIMODAL_PIPELINE.md` §0 has said since Phase 4 that competitor
media is fetched, analysed and deleted rather than accumulated. Nothing
enforced it, and one collector was found violating it. This table is the
enforcement surface: a row exists for as long as a file does, carries a
NOT NULL expiry, and keeps its deletion record after the bytes are gone
so an audit can see that something was acquired and then removed.

`expires_at` is deliberately NOT NULL — a nullable expiry would make
"keep forever" representable, which is the one state this table exists
to prevent.

Revision ID: e2b57c9a013f
Revises: d4a91c7b2e68
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e2b57c9a013f"
down_revision: Union[str, Sequence[str], None] = "d4a91c7b2e68"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transient_media",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("permission_basis", sa.Text, nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_reason", sa.String(32), nullable=True),
        sa.Column("audit", JSONB, nullable=True),
        sa.UniqueConstraint("path", name="uq_transient_media_path"),
        sa.CheckConstraint(
            "delete_reason is null or delete_reason in "
            "('features_persisted','expired','cancelled','crash_recovery',"
            "'manual')",
            name="ck_transient_media_delete_reason"),
        sa.CheckConstraint("(deleted_at is null) = (delete_reason is null)",
                           name="ck_transient_media_deletion_complete"),
    )
    op.create_index("ix_transient_media_live", "transient_media",
                    ["expires_at"],
                    postgresql_where=sa.text("deleted_at is null"))
    op.create_index("ix_transient_media_video", "transient_media",
                    ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_transient_media_video", table_name="transient_media")
    op.drop_index("ix_transient_media_live", table_name="transient_media")
    op.drop_table("transient_media")
