"""Content relationships: performances, publication links, match evidence

Separates the three layers that cross-platform analysis depends on:
performance (what was played) / variant (which edit) / publication (where and
when it was posted). See the Performance docstring in server/db/models.py.

Additive only. No existing column is dropped or retyped, and no row is
rewritten, so the authoritative archive on the server is untouched by this
migration beyond gaining nullable columns and two new tables.

Revision ID: c7a1d4e9b082
Revises: 119ecf544559
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7a1d4e9b082"
down_revision: Union[str, Sequence[str], None] = "119ecf544559"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "performances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("work", sa.Text(), nullable=True),
        sa.Column("recorded_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="inferred"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('inferred','confirmed')",
                           name="ck_performance_status"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("local_masters",
                  sa.Column("performance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_local_masters_performance", "local_masters",
                          "performances", ["performance_id"], ["id"])
    op.create_index("ix_local_masters_performance", "local_masters",
                    ["performance_id"])

    # Evidence columns on the existing link table. `score` alone could not
    # show WHICH audio descriptor backed a link, so a link scored with the
    # old envelope was indistinguishable from one scored with the measured
    # spectral fingerprint.
    for col in (
        sa.Column("audio_similarity", sa.Float(), nullable=True),
        sa.Column("audio_method", sa.String(length=16), nullable=True),
        sa.Column("audio_offset_s", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("video_master_links", col)

    # Existing rows: everything currently in the table was set by the matcher,
    # except rows a human already confirmed or rejected during review. Those
    # two statuses can only have come from scripts/review_matches.py, so they
    # are attributed to the human and the rest to the matcher. Nothing is
    # invented: decided_at stays NULL because the original decision time was
    # never recorded.
    op.execute("""
        update video_master_links
           set decided_by = case when status in ('confirmed','rejected')
                                 then 'human' else 'matcher' end
    """)
    # Pre-existing links were scored with the 32-bin envelope.
    op.execute("""
        update video_master_links
           set audio_method = 'envelope'
         where method = 'dhash-seq-v1'
    """)

    op.drop_constraint("ck_link_status", "video_master_links",
                       type_="check")
    op.create_check_constraint(
        "ck_link_status", "video_master_links",
        "status in ('auto','confirmed','rejected','pending','variant')")
    op.create_check_constraint(
        "ck_link_audio_method", "video_master_links",
        "audio_method is null or audio_method in "
        "('spectral','envelope','none')")
    op.create_check_constraint(
        "ck_link_decided_by", "video_master_links",
        "decided_by is null or decided_by in ('human','matcher')")

    op.create_table(
        "publication_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_a_id", sa.Integer(), nullable=False),
        sa.Column("video_b_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="proposed"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("method", sa.String(length=32), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["video_a_id"], ["videos.id"]),
        sa.ForeignKeyConstraint(["video_b_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_a_id", "video_b_id",
                            name="uq_publication_pair"),
        sa.CheckConstraint("video_a_id < video_b_id",
                           name="ck_pub_link_order"),
        sa.CheckConstraint(
            "relationship_type in ('same_variant','platform_edit','repost')",
            name="ck_pub_link_type"),
        sa.CheckConstraint("status in ('proposed','confirmed','rejected')",
                           name="ck_pub_link_status"),
        sa.CheckConstraint("decided_by is null or decided_by in "
                           "('human','matcher')",
                           name="ck_pub_link_decided_by"),
    )
    op.create_index("ix_pub_links_status", "publication_links", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pub_links_status", table_name="publication_links")
    op.drop_table("publication_links")

    op.drop_constraint("ck_link_decided_by", "video_master_links",
                       type_="check")
    op.drop_constraint("ck_link_audio_method", "video_master_links",
                       type_="check")
    op.drop_constraint("ck_link_status", "video_master_links", type_="check")
    # Any row a human marked as a variant has no representation in the old
    # constraint, so it reverts to pending rather than being deleted.
    op.execute("update video_master_links set status = 'pending' "
               "where status = 'variant'")
    op.create_check_constraint(
        "ck_link_status", "video_master_links",
        "status in ('auto','confirmed','rejected','pending')")

    for name in ("decided_at", "decided_by", "evidence", "audio_offset_s",
                 "audio_method", "audio_similarity"):
        op.drop_column("video_master_links", name)

    op.drop_index("ix_local_masters_performance", table_name="local_masters")
    op.drop_constraint("fk_local_masters_performance", "local_masters",
                       type_="foreignkey")
    op.drop_column("local_masters", "performance_id")
    op.drop_table("performances")
