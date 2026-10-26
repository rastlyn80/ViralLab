"""vision analysis requests

The Ubuntu server cannot run the local vision model: it has no GPU, and
llama-server lives on the Windows workstation. So "analyse this video" from
the web interface cannot execute inline - it has to be recorded as an intent
that the Windows worker drains when it next runs.

That is what this table is. It is deliberately NOT a general job queue with
leases and heartbeats: the worker owns execution and already writes its
results idempotently on (video, feature, method_version), so a duplicate run
overwrites rather than duplicates. What was missing was a record of what a
person asked for, so the interface can show a queue and a progress figure
instead of silently doing nothing while the workstation is switched off.

Revision ID: b8e3c25d9f47
Revises: a3d91f7c6e24
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8e3c25d9f47"
down_revision: Union[str, Sequence[str], None] = "a3d91f7c6e24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vision_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        # queued | running | done | failed | cancelled
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="queued"),
        # local_only keeps a request from ever becoming a paid call just
        # because a cloud provider happened to be configured later.
        sa.Column("mode", sa.String(16), nullable=False,
                  server_default="local_only"),
        sa.Column("requested_by", sa.String(64)),
        sa.Column("requested_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("force", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("error", sa.Text),
        sa.Column("params", JSONB),
        sa.CheckConstraint(
            "status in ('queued','running','done','failed','cancelled')",
            name="ck_vision_request_status"),
        sa.CheckConstraint("attempts >= 0 and attempts <= 10",
                           name="ck_vision_request_attempts"),
    )
    # One OPEN request per (video, task, method version). A second click on
    # an already-queued video must not create a second unit of work; a new
    # request after the first finished is legitimate, which is why the index
    # is partial rather than a plain unique constraint.
    op.create_index(
        "uq_vision_request_open", "vision_requests",
        ["video_id", "task", "method_version"], unique=True,
        postgresql_where=sa.text("status in ('queued','running')"))
    op.create_index("ix_vision_requests_status", "vision_requests",
                    ["status", "requested_at"])


def downgrade() -> None:
    op.drop_index("ix_vision_requests_status", table_name="vision_requests")
    op.drop_index("uq_vision_request_open", table_name="vision_requests")
    op.drop_table("vision_requests")
