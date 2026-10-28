"""strategist_turns: every question with the evidence it was answered from

BLUEPRINT M8 requires the conversation log to carry the evidence
context, not just the answer. Without the tool results stored beside
the text, a wrong figure is unattributable - it could be the model
inventing or a tool returning something wrong, and those need
opposite fixes.

Revision ID: f1a3d6b82c47
Revises: e4b7c2a91f30
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a3d6b82c47"
down_revision: Union[str, Sequence[str], None] = "e4b7c2a91f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategist_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("violations", postgresql.JSONB(), nullable=True),
        sa.Column("unverified_numbers", postgresql.JSONB(), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("refused_before_spending", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("billed_usd", sa.Float(), nullable=True),
        sa.Column("latency_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategist_turns_conversation_id",
                    "strategist_turns", ["conversation_id"])
    op.create_index("ix_strategist_turns_created", "strategist_turns",
                    ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_strategist_turns_created",
                  table_name="strategist_turns")
    op.drop_index("ix_strategist_turns_conversation_id",
                  table_name="strategist_turns")
    op.drop_table("strategist_turns")
