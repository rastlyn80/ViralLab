"""label_reviews: the owner's verdict on labels Claude was unsure about

Every vision benchmark here has been scored against labels Claude
wrote. The Claude Sonnet run of 2026-10-27 turned that from a
footnote into the binding constraint: a perfect 40 of 40 against an
answer key written by another Claude model measures agreement between
two models at least as much as it measures either being right.

Twenty-one labels were flagged genuinely ambiguous when written. This
table holds a human verdict on them, beside the model's label rather
than instead of it - knowing a label was overturned is evidence about
the labelling process, and silently correcting it would destroy the
record that makes the next benchmark honest.

Revision ID: e4b7c2a91f30
Revises: c71e9a3f05d8
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c2a91f30"
down_revision: Union[str, Sequence[str], None] = "c71e9a3f05d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "label_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("cohort", sa.String(length=16), nullable=False),
        sa.Column("why_uncertain", sa.Text(), nullable=True),
        sa.Column("model_label", sa.String(length=32), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("owner_label", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_from", sa.String(length=16), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "verdict is null or verdict in "
            "('confirmed','overturned','cannot_tell')",
            name="ck_label_reviews_verdict"),
        sa.CheckConstraint("cohort in ('own','competitor')",
                           name="ck_label_reviews_cohort"),
        sa.CheckConstraint(
            "reviewed_from is null or reviewed_from in "
            "('frames','live_post')",
            name="ck_label_reviews_source"),
    )
    op.create_index("ix_label_reviews_video_id", "label_reviews",
                    ["video_id"])
    op.create_index("uq_label_reviews_question", "label_reviews",
                    ["video_id", "field"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_label_reviews_question", table_name="label_reviews")
    op.drop_index("ix_label_reviews_video_id", table_name="label_reviews")
    op.drop_table("label_reviews")
