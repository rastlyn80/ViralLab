"""Persist platform cover images, so cross-platform matching has a visual signal

TikTok's Video Object exposes `cover_image_url` and the collector has always
requested it, but nothing stored it. That matters more than it sounds: the
TikTok API returns no media file, so there is no TikTok audio to fingerprint,
and without the cover the only cross-platform signals left are duration, date
and caption - none of which can establish that two publications are the same
video.

`cover_url` is stored for provenance and `cover_hash` holds the dHash, because
the URL is short-lived (an expiring CDN link) while the hash stays valid
forever.

Additive only.

Revision ID: e4b8f21c6d35
Revises: c7a1d4e9b082
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8f21c6d35"
down_revision: Union[str, Sequence[str], None] = "c7a1d4e9b082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("cover_url", sa.Text(), nullable=True))
    op.add_column("videos",
                  sa.Column("cover_hash", sa.String(length=32), nullable=True))
    op.add_column("videos",
                  sa.Column("cover_fetched_at", sa.DateTime(timezone=True),
                            nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "cover_fetched_at")
    op.drop_column("videos", "cover_hash")
    op.drop_column("videos", "cover_url")
