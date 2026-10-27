"""budget_reservations: allow the `competitor_media` spending category

M2 downloads competitor video through the provider's paid add-on, which
is a different decision from paying for metadata: a different legal
basis, a different per-item price, and a ceiling that must not be shared
with discovery or a batch of downloads could consume the budget metadata
collection runs on.

`budget.CATEGORIES` was extended for this first, and the database
constraint was not. The first real acquisition was refused by Postgres at
the moment of reserving — before the actor ran and before any money was
spent. That is the correct failure, and it only happened because the
constraint duplicates the Python check rather than trusting it.

Revision ID: f7d3016ba845
Revises: e2b57c9a013f
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "f7d3016ba845"
down_revision: Union[str, Sequence[str], None] = "e2b57c9a013f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "category in ('discovery','cloud_vision','cloud_audio','strategy')"
_NEW = ("category in ('discovery','competitor_media','cloud_vision',"
        "'cloud_audio','strategy')")


def upgrade() -> None:
    op.drop_constraint("ck_reservation_category", "budget_reservations",
                       type_="check")
    op.create_check_constraint("ck_reservation_category",
                               "budget_reservations", _NEW)


def downgrade() -> None:
    # Rows in the new category would violate the old constraint. They are
    # real spending records, so they are NOT deleted or relabelled to a
    # category that did not pay for them: the downgrade refuses instead.
    conn = op.get_bind()
    n = conn.exec_driver_sql(
        "select count(*) from budget_reservations "
        "where category = 'competitor_media'").scalar()
    if n:
        raise RuntimeError(
            f"{n} budget_reservations row(s) are in the competitor_media "
            f"category. Downgrading would need them relabelled to a "
            f"category that did not incur them, which would misstate "
            f"where money went. Resolve them by hand first.")
    op.drop_constraint("ck_reservation_category", "budget_reservations",
                       type_="check")
    op.create_check_constraint("ck_reservation_category",
                               "budget_reservations", _OLD)
