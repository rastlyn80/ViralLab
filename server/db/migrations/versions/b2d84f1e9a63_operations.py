"""operations: work the platform runs for itself

The owner's objection, in his words: "the platform should be the
place of research, not coding sessions." He was right. The analysis
already lived in the product - `#/views`, `#/following`, `#/make`
all compute live from this database - but everything that FED it
was a provision script run over SSH by an operator. So every
refresh needed a person with a terminal.

This table is the record of a run the platform started itself: what
was asked for, by whom, how far it got, what it cost, and what it
said. One row per run, kept afterwards, because "when was this last
collected and what happened" is a question the owner will ask of a
number on a page.

Deliberately not the `analysis_batches` queue. That one leases
per-video modality batches to the GPU worker and is the right shape
for exactly that. An operation is a different animal: one long
server-side pass, no worker, no lease, and its unit of progress is
"publications done", not "videos in a batch".

Revision ID: b2d84f1e9a63
Revises: a7c5e91d4b2f
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d84f1e9a63"
down_revision: Union[str, Sequence[str], None] = "a7c5e91d4b2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.Integer, primary_key=True),
        # Which operation from the frozen catalogue. Never a command
        # line: the API accepts a key and nothing else, so no request
        # can choose what gets executed.
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False,
                  server_default="queued"),
        sa.Column("params", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        # Plain-English progress for the page, and a number for the
        # bar. Both optional: a pass that cannot count its work says
        # so rather than inventing a percentage.
        sa.Column("progress_note", sa.Text),
        sa.Column("done", sa.Integer),
        sa.Column("total", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False,
                  server_default="0"),
        # The tail of the output. Capped by the writer; a log that
        # grows without limit is a disk-filling bug wearing a
        # diagnostics costume.
        sa.Column("log", sa.Text),
        sa.Column("summary", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("pid", sa.Integer),
        sa.CheckConstraint(
            "status in ('queued','running','ok','partial','failed',"
            "'cancelled')",
            name="ck_operations_status"),
    )
    op.create_index("ix_operations_kind_requested",
                    "operations", ["kind", "requested_at"])
    # One run of a given kind at a time. These passes talk to other
    # people's servers and to paid APIs; two concurrent copies would
    # double the request rate and the spend, which is the one thing
    # the politeness argument in ADR 0010 cannot survive.
    op.create_index(
        "uq_operations_one_live_per_kind", "operations", ["kind"],
        unique=True,
        postgresql_where=sa.text("status in ('queued','running')"))


def downgrade() -> None:
    op.drop_index("uq_operations_one_live_per_kind",
                  table_name="operations")
    op.drop_index("ix_operations_kind_requested", table_name="operations")
    op.drop_table("operations")
