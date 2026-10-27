"""videos.permalink: the public URL of a publication

Clicking a figure and landing on the actual post is the difference
between a number you have to trust and one you can check, and that
link did not exist.

TikTok needs no column - a canonical URL is fully determined by the
handle and the publication id, so it is rebuilt on demand rather than
denormalised into something that goes stale when a creator renames
themselves. Instagram does: a permalink carries a *shortcode* which is
not the media id and cannot be computed from it in either direction.

The value was never missing from the API. `instagram_official.
MEDIA_FIELDS` has requested `permalink` since the first version of
this project and the response was then dropped on the floor, so the
backfill costs nothing and asks for nothing new.

Nullable on purpose: most publications have no stored permalink today,
and a NOT NULL here would mean inventing one. A null means "we never
kept it", which is exactly true and renders as an honest absence.

Revision ID: c71e9a3f05d8
Revises: a2f49c81b73e
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c71e9a3f05d8"
down_revision: Union[str, Sequence[str], None] = "a2f49c81b73e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("permalink", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "permalink")
