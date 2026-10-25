"""Reproducible discovery params, and competitive research collections

Two additions, both because Phase 4.5 writes to them:

  * `discovery_jobs.params` - exactly what was sent to the provider, so a
    concept search is reproducible from the row rather than from memory.
    `docs/SCHEMA_PLAN.md` 2 promised this column; the Phase 4 migration
    shipped without it because only creator research existed, where the
    handle *was* the whole query. A keyword search also carries a niche, a
    cursor and a limit, and reconstructing those by guesswork is how a
    "repeat this search" button silently runs a different search.

  * `metrics_snapshots.discovery_job_id` - which search produced this
    observation. Without it, "what did job N return" had to be answered from
    `videos.discovery_job_id`, which records only the FIRST job to see a
    publication - so re-running a search appeared to return nothing at all,
    and a creator already found by an earlier search vanished from the new
    result set. The observation is the right place for it: a job observed a
    publication at a moment, and that is exactly one snapshot row.

  * `creator_collections` / `creator_collection_members` - named groups of
    creators, with notes. The point is not organisation for its own sake:
    `docs/SCHEMA_PLAN.md` 3 makes a control cohort of non-breakthrough
    creators in the same niche a prerequisite for promoting any pattern
    above `hypothesis`, and a cohort has to be an explicit, stable set of
    accounts before it can serve as a control. These tables are that set.

Deliberately NOT added: any denormalised performance figure on the
collection. A collection is a membership list; every number shown for it is
derived from the observations at read time, so changing an analytical
policy cannot corrupt a saved group.

Revision ID: e7c2b94f83da
Revises: d9f4a2b76c81
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e7c2b94f83da"
down_revision = "d9f4a2b76c81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_jobs", sa.Column("params", JSONB(),
                                              nullable=True))

    # Nullable: NULL means the owner's own collector produced it, which is
    # the overwhelming majority of rows and must stay cheap.
    op.add_column("metrics_snapshots",
                  sa.Column("discovery_job_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_snapshots_discovery_job", "metrics_snapshots",
                          "discovery_jobs", ["discovery_job_id"], ["id"])
    op.create_index("ix_snapshots_discovery_job", "metrics_snapshots",
                    ["discovery_job_id"])

    op.create_table(
        "creator_collections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        # Why this group exists. A collection assembled to find formats to
        # copy and one assembled as a statistical control are different
        # things, and a later cohort analysis has to be able to tell.
        sa.Column("purpose", sa.String(32), nullable=False,
                  server_default="research"),
        sa.Column("niche", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_collection_name"),
        sa.CheckConstraint(
            "purpose in ('research','control_cohort','watchlist',"
            "'inspiration')", name="ck_collection_purpose"),
    )

    op.create_table(
        "creator_collection_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("collection_id", sa.Integer(),
                  sa.ForeignKey("creator_collections.id",
                                ondelete="CASCADE"), nullable=False),
        sa.Column("creator_id", sa.Integer(),
                  sa.ForeignKey("creators.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        # Why this creator was added, in the owner's words. Kept separate
        # from `notes` so a one-line reason stays scannable in a list.
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # Which discovery run surfaced them, where one did. Nullable: a
        # creator can also be added by hand.
        sa.Column("discovered_by_job_id", sa.Integer(),
                  sa.ForeignKey("discovery_jobs.id"), nullable=True),
        sa.UniqueConstraint("collection_id", "creator_id",
                            name="uq_collection_member"),
    )
    op.create_index("ix_collection_members_creator",
                    "creator_collection_members", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_discovery_job",
                  table_name="metrics_snapshots")
    op.drop_constraint("fk_snapshots_discovery_job", "metrics_snapshots",
                       type_="foreignkey")
    op.drop_column("metrics_snapshots", "discovery_job_id")

    op.drop_index("ix_collection_members_creator",
                  table_name="creator_collection_members")
    op.drop_table("creator_collection_members")
    op.drop_table("creator_collections")
    op.drop_column("discovery_jobs", "params")
