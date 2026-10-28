"""experiments: the only place a causal claim can be born

BLUEPRINT M9. Everything else in this project measures what already
happened; an experiment with its prediction fixed in advance can do
more. The fields that constitute the prediction are frozen at
`locked_at`, the arm assignment is drawn before any video is made,
and the readout refuses to compute anything before the
pre-registered n is reached.

Revision ID: a7c5e91d4b2f
Revises: f1a3d6b82c47
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c5e91d4b2f"
down_revision: Union[str, Sequence[str], None] = "f1a3d6b82c47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("opportunity_key", sa.String(length=200), nullable=True),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("predicted_direction", sa.String(length=16),
                  nullable=False),
        sa.Column("prediction", sa.Text(), nullable=False),
        sa.Column("n_per_arm", sa.Integer(), nullable=False),
        sa.Column("anchor_age_days", sa.Integer(), nullable=False,
                  server_default="7"),
        sa.Column("ad_policy", sa.String(length=16), nullable=False,
                  server_default="held_constant"),
        sa.Column("analysis", sa.String(length=32), nullable=False,
                  server_default="mann_whitney_u"),
        sa.Column("alpha", sa.Float(), nullable=False,
                  server_default="0.05"),
        sa.Column("stopping_rule", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="draft"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("abandoned_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status in ('draft','running','complete','abandoned')",
            name="ck_experiments_status"),
        sa.CheckConstraint(
            "verdict is null or verdict in "
            "('supported','not_supported','inconclusive')",
            name="ck_experiments_verdict"),
        sa.CheckConstraint("predicted_direction in ('higher','lower')",
                           name="ck_experiments_direction"),
        sa.CheckConstraint("ad_policy in ('held_constant','randomised')",
                           name="ck_experiments_ad_policy"),
        sa.CheckConstraint("n_per_arm >= 2", name="ck_experiments_n"),
    )
    op.create_table(
        "experiment_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("arm", sa.String(length=16), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("arm in ('treatment','control')",
                           name="ck_assignment_arm"),
    )
    op.create_index("ix_experiment_assignments_experiment_id",
                    "experiment_assignments", ["experiment_id"])
    op.create_index("uq_assignment_slot", "experiment_assignments",
                    ["experiment_id", "slot"], unique=True)
    # One publication cannot fill two slots: without this a good video
    # could be counted into both arms.
    op.create_index("uq_assignment_video", "experiment_assignments",
                    ["experiment_id", "video_id"], unique=True,
                    postgresql_where=sa.text("video_id is not null"))


def downgrade() -> None:
    op.drop_index("uq_assignment_video",
                  table_name="experiment_assignments")
    op.drop_index("uq_assignment_slot",
                  table_name="experiment_assignments")
    op.drop_index("ix_experiment_assignments_experiment_id",
                  table_name="experiment_assignments")
    op.drop_table("experiment_assignments")
    op.drop_table("experiments")
