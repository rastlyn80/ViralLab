"""Atomic budget reservations for paid provider calls

The first spending guard read month-to-date spend, compared it to the cap and
returned. That is advisory, not enforcing, and it fails in four ways that all
end in spending money nobody authorised:

**1. Time-of-check to time-of-use.** Two concurrent jobs both read $0.90
against a $1.00 cap, both estimate $0.08, both pass, and the month ends at
$1.06.

**2. Nothing is reserved.** Between the check and the charge the budget is
unprotected, so any number of callers can pass the same check.

**3. Interrupted billing.** A process that dies after the provider call but
before recording the cost loses the charge entirely. The budget then looks
emptier than it is, and the error compounds with every crash.

**4. Duplicate requests.** A retried job has no way to recognise that it
already paid, so it pays again.

A reservation table fixes all four: the reservation is taken *before* the
call under a lock, it holds budget until settled, it is keyed by an
idempotency key so a retry returns the original rather than charging twice,
and a reservation whose outcome is unknown keeps holding budget rather than
silently freeing it.

Revision ID: c3e8b47d105a
Revises: b5f2a81c04d7
Create Date: 2026-10-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e8b47d105a"
down_revision: Union[str, Sequence[str], None] = "b5f2a81c04d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        # The caller's own identifier for this unit of work. A retry presents
        # the same key and gets the same reservation back instead of a second
        # charge.
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("estimated_usd", sa.Float(), nullable=False),
        # NULL until the provider tells us what it actually billed.
        sa.Column("actual_usd", sa.Float(), nullable=True),
        # reserved | settled | released | expired
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="reserved"),
        # The calendar month this reservation counts against, stored rather
        # than derived so a reservation cannot drift between months if it is
        # settled after a month boundary.
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["collection_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "idempotency_key",
                            name="uq_budget_reservation_key"),
        sa.CheckConstraint(
            "status in ('reserved','settled','released','expired')",
            name="ck_budget_reservation_status"),
        sa.CheckConstraint("estimated_usd >= 0",
                           name="ck_budget_reservation_estimate"),
        sa.CheckConstraint("actual_usd is null or actual_usd >= 0",
                           name="ck_budget_reservation_actual"),
        # A settled reservation must know what it cost.
        sa.CheckConstraint(
            "status <> 'settled' or actual_usd is not null",
            name="ck_budget_reservation_settled_has_actual"),
    )
    op.create_index("ix_budget_reservations_period", "budget_reservations",
                    ["provider", "period", "status"])


def downgrade() -> None:
    op.drop_index("ix_budget_reservations_period",
                  table_name="budget_reservations")
    op.drop_table("budget_reservations")
