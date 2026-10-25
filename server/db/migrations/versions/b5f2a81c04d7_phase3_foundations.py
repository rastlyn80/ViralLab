"""Phase 3 foundations: provider cost, creator provenance, follower history

Deliberately small. `docs/SCHEMA_PLAN.md` describes the full Phase 3-8 data
model; this migration adds only the three things that code written now
depends on, because a table with no writer is a guess about the future.

**1. `collection_runs.cost_usd`.** The spending guard in `server/config.py`
computes month-to-date spend from this column. Without it the guard has no
denominator, and CLAUDE.md §5.3 requires cost to be logged from actual
billing rather than projected.

**2. Creator provenance.** `creators` currently distinguishes own accounts
from others only by `is_own_account`. The moment a discovered creator
exists, we must be able to say where it came from, when it was first seen
and when it was last refreshed - the product vision requires discovery and
manual lookup to share one table, which only works if provenance is a
column rather than a separate dataset.

**3. `creator_snapshots`.** Follower counts change, and the vision forbids
claiming a video caused follower growth without historical evidence. A
single mutable `creators.follower_count` cannot support that claim or
refuse it honestly; an append-only observation table can. It is added now
rather than later because the alternative is overwriting history we cannot
recover.

Additive only. No existing column is dropped or retyped.

Revision ID: b5f2a81c04d7
Revises: a1d7e3c92f08
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5f2a81c04d7"
down_revision: Union[str, Sequence[str], None] = "a1d7e3c92f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. provider cost -------------------------------------------------
    op.add_column("collection_runs",
                  sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("collection_runs",
                  sa.Column("provider", sa.String(length=64), nullable=True))

    # --- 2. creator provenance -------------------------------------------
    op.add_column("creators",
                  sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("creators",
                  sa.Column("profile_url", sa.Text(), nullable=True))
    # own_api | discovery | manual_lookup | cross_reference
    op.add_column("creators",
                  sa.Column("source", sa.String(length=32), nullable=False,
                            server_default="own_api"))
    #: The query, hashtag or handle that surfaced this creator. Free text
    #: because a discovery entry point can be any of several kinds.
    op.add_column("creators",
                  sa.Column("discovered_via", sa.Text(), nullable=True))
    op.add_column("creators",
                  sa.Column("first_seen_at", sa.DateTime(timezone=True),
                            nullable=True))
    op.add_column("creators",
                  sa.Column("last_refreshed_at", sa.DateTime(timezone=True),
                            nullable=True))
    #: Coverage and restrictions of whatever produced this row, so a partial
    #: back-catalogue is never mistaken for a complete one.
    op.add_column("creators",
                  sa.Column("provenance", postgresql.JSONB(), nullable=True))
    op.execute("""
        update creators
           set source = case when is_own_account then 'own_api'
                             else 'manual_lookup' end,
               first_seen_at = created_at
    """)
    op.create_check_constraint(
        "ck_creator_source", "creators",
        "source in ('own_api','discovery','manual_lookup','cross_reference')")
    op.create_index("ix_creators_source", "creators", ["source"])

    # --- 3. follower history ---------------------------------------------
    op.create_table(
        "creator_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("follower_count", sa.BigInteger(), nullable=True),
        sa.Column("following_count", sa.BigInteger(), nullable=True),
        sa.Column("publication_count", sa.BigInteger(), nullable=True),
        sa.Column("total_likes", sa.BigInteger(), nullable=True),
        # Where the reading came from, and whether the source rounded it.
        sa.Column("source", sa.String(length=32), nullable=False,
                  server_default="own_api"),
        sa.Column("is_rounded", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Append-only and idempotent: one reading per creator per instant,
        # so re-running a collection cannot duplicate observations.
        sa.UniqueConstraint("creator_id", "captured_at",
                            name="uq_creator_snapshot"),
    )
    op.create_index("ix_creator_snapshots_creator", "creator_snapshots",
                    ["creator_id", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_creator_snapshots_creator",
                  table_name="creator_snapshots")
    op.drop_table("creator_snapshots")

    op.drop_index("ix_creators_source", table_name="creators")
    op.drop_constraint("ck_creator_source", "creators", type_="check")
    for col in ("provenance", "last_refreshed_at", "first_seen_at",
                "discovered_via", "source", "profile_url", "display_name"):
        op.drop_column("creators", col)

    op.drop_column("collection_runs", "provider")
    op.drop_column("collection_runs", "cost_usd")
