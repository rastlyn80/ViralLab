"""suggestions: keep what the generator produced

The owner, after using it: "I just want to make sure that those
ideas are actually saved and retained so we can build up a
collection of ideas and stuff like captions, opening text,
otherwise we are just burning credits without retaining the..."

He is right and it was a real hole. Every call cost money and
every result lived until the page was refreshed. Worse than the
waste: a suggestion that is never recorded can never be connected
to what happened when he used it, so the system had no route to
learning whether its own advice was any good.

This table is the precondition for that loop. One row per
suggestion, with what it was grounded in, what it cost, and - when
he posts it - which publication it became.

`used_video_id` is the point of the whole thing. Once a suggestion
has a publication attached, the performance of that publication is
already measured by everything else here, and "did our advice
work" becomes a query rather than a question.

Revision ID: c3f67a2b8e10
Revises: b2d84f1e9a63
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3f67a2b8e10"
down_revision: Union[str, Sequence[str], None] = "b2d84f1e9a63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        # Everything the generator returned for this item, kept
        # whole. The columns beside it are for finding and sorting;
        # this is the record.
        sa.Column("body", postgresql.JSONB, nullable=False),
        # The headline, lifted out so it can be read, searched and
        # shown without unpacking the JSON.
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("grounded_in", sa.Text),
        # Which generation call produced it, so a batch can be read
        # back together - he asked for five and judged them as five.
        sa.Column("batch", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # His verdict. `new` until he says otherwise.
        sa.Column("status", sa.Text, nullable=False,
                  server_default="new"),
        sa.Column("note", sa.Text),
        # The publication this became. The reason the table exists:
        # with it, "did the advice work" is a query.
        sa.Column("used_video_id", sa.Integer,
                  sa.ForeignKey("videos.id", ondelete="SET NULL")),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('new','kept','discarded','used')",
            name="ck_suggestions_status"),
    )
    op.create_index("ix_suggestions_kind_created",
                    "suggestions", ["kind", "created_at"])
    op.create_index("ix_suggestions_status",
                    "suggestions", ["status", "created_at"])
    op.create_index("ix_suggestions_batch", "suggestions", ["batch"])


def downgrade() -> None:
    op.drop_index("ix_suggestions_batch", table_name="suggestions")
    op.drop_index("ix_suggestions_status", table_name="suggestions")
    op.drop_index("ix_suggestions_kind_created",
                  table_name="suggestions")
    op.drop_table("suggestions")
