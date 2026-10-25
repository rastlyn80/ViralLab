"""Store each preserved file's content hash

Deduplication re-hashed every preserved file at the start of every
preservation pass - 258 files and 736 MB of disk read on each daily run, for
information that never changes once a file is written. Storing the digest
makes the dedup index a single query.

It also makes orphan detection possible: a file on disk whose digest matches
no row is a leftover from a pass that died between writing the file and
committing the row.

Revision ID: a1d7e3c92f08
Revises: f93c05ae7b14
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d7e3c92f08"
down_revision: Union[str, Sequence[str], None] = "f93c05ae7b14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos",
                  sa.Column("media_sha256", sa.String(length=64),
                            nullable=True))
    # Not unique: a deduplicated publication deliberately points at the same
    # file, and therefore carries the same digest, as the row that owns it.
    op.create_index("ix_videos_media_sha256", "videos", ["media_sha256"])


def downgrade() -> None:
    op.drop_index("ix_videos_media_sha256", table_name="videos")
    op.drop_column("videos", "media_sha256")
