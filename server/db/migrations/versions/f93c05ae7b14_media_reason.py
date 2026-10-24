"""Record WHY media is missing, and stop counting images as preserved video

Two problems this fixes.

**1. "absent" conflated three different things.** A publication with no stored
media could be a genuine API restriction (no `media_url` returned), a
download or validation failure worth retrying, or simply not a video at all.
Retrying the third forever is waste, and reporting it as a gap in coverage is
wrong - there is nothing to preserve.

**2. Three image posts were stored as `.mp4` and marked `available`.** The
downloader validated with ffprobe, and ffprobe succeeds on a JPEG
(`format_name=jpeg_pipe`), so an image passed as video. That is why the
corpus showed 261 media files but only 258 fingerprints: the three
"successes" had no video stream and no duration. They are corrected here to
`media_status='unsupported'`, which is an honest description rather than a
failure.

Revision ID: f93c05ae7b14
Revises: e4b8f21c6d35
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f93c05ae7b14"
down_revision: Union[str, Sequence[str], None] = "e4b8f21c6d35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos",
                  sa.Column("media_reason", sa.String(length=64),
                            nullable=True))
    op.add_column("videos",
                  sa.Column("media_checked_at", sa.DateTime(timezone=True),
                            nullable=True))
    op.add_column("videos",
                  sa.Column("media_attempts", sa.Integer(), nullable=False,
                            server_default="0"))

    # An image post stored as .mp4 has no video stream, so it has no duration
    # either. That is exactly the signature, and it is how these rows are
    # identified without re-reading every file.
    op.execute("""
        update videos
           set media_status = 'unsupported',
               media_reason = 'not_a_video:no_video_stream'
         where media_status = 'available'
           and duration_s is null
    """)
    op.execute("""
        update videos
           set media_reason = 'unknown:not_yet_classified'
         where media_status = 'absent' and media_reason is null
    """)


def downgrade() -> None:
    op.execute("update videos set media_status = 'available' "
               "where media_status = 'unsupported' "
               "and media_path is not null")
    op.execute("update videos set media_status = 'absent' "
               "where media_status = 'unsupported'")
    op.drop_column("videos", "media_attempts")
    op.drop_column("videos", "media_checked_at")
    op.drop_column("videos", "media_reason")
