"""transient_media: uniqueness on path applies to LIVE rows only

The quarantine filename is derived from the video id, so a publication
that is acquired, analysed, deleted and later re-acquired reuses exactly
the same path. A global unique constraint made that second acquisition
impossible for as long as the historical row existed — and the
historical row is deliberately kept forever, because it is the audit
record showing the bytes were held and then removed.

The invariant that actually matters is narrower: no two *live* rows may
claim one file, or the reaper could delete it out from under a row that
still believes it is there. A partial unique index says exactly that.

Found by the second real acquisition of the same publication, which
failed on the constraint after the provider had already been paid.

Revision ID: b9e4f2c70d18
Revises: f7d3016ba845
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4f2c70d18"
down_revision: Union[str, Sequence[str], None] = "f7d3016ba845"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_transient_media_path", "transient_media",
                       type_="unique")
    op.create_index("uq_transient_media_live_path", "transient_media",
                    ["path"], unique=True,
                    postgresql_where=sa.text("deleted_at is null"))


def downgrade() -> None:
    # Restoring the global constraint requires that no path repeats across
    # live and deleted rows; refuse rather than delete audit history.
    conn = op.get_bind()
    dupes = conn.exec_driver_sql(
        "select count(*) from (select path from transient_media "
        "group by path having count(*) > 1) d").scalar()
    if dupes:
        raise RuntimeError(
            f"{dupes} path(s) appear on more than one transient_media row. "
            f"A global unique constraint would require deleting audit "
            f"records of media that was held and removed. Resolve by hand.")
    op.drop_index("uq_transient_media_live_path",
                  table_name="transient_media")
    op.create_unique_constraint("uq_transient_media_path",
                                "transient_media", ["path"])
