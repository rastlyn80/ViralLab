"""media_attempts: remember what happened last time we asked

Candidate selection excluded publications we were currently
holding and ones already fully analysed, and nothing else. The
outcome of an attempt lived in the result object of the run that
made it and was then discarded.

So a publication that TikTok serves without a video rendition was
picked again on every single pass. With no `ORDER BY` in the
candidate query, Postgres returned it first every time, the
collector classed it as a throttle and stopped — and five
consecutive passes each attempted one video, collected nothing,
and reported that the platform was refusing us. It was not; the
same bad candidate was being re-asked and re-misread.

One row per publication, upserted. It records what happened, why,
how many times we have tried, and when we last did. Routine
selection skips terminal outcomes and backs off transient ones.

Deliberately not a column on `videos`: an attempt is something we
did, not a property of the publication, and `videos` is the table
every analysis joins against.

Revision ID: d4a1c93b7e22
Revises: c3f67a2b8e10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a1c93b7e22"
down_revision: Union[str, Sequence[str], None] = "c3f67a2b8e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_attempts",
        sa.Column("id", sa.Integer, primary_key=True),
        # One row per publication. The unique constraint is what
        # makes this an upsert rather than a log: we want the last
        # outcome, not a history of them.
        sa.Column("video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        # Which route asked - the free yt-dlp path or the provider -
        # because they fail for different reasons and a refusal on
        # one says nothing about the other.
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        # The message underneath. Dropping this is what let a
        # format failure read as an IP block for four days.
        sa.Column("detail", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False,
                  server_default="1"),
        sa.Column("first_tried_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("last_tried_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # When it is worth asking again. NULL means never: the
        # answer is settled and re-asking is only rudeness.
        sa.Column("try_again_after", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_media_attempts_retry", "media_attempts",
                    ["try_again_after"])
    op.create_index("ix_media_attempts_status", "media_attempts",
                    ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_attempts_status", table_name="media_attempts")
    op.drop_index("ix_media_attempts_retry", table_name="media_attempts")
    op.drop_table("media_attempts")
