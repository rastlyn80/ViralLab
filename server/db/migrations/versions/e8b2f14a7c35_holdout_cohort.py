"""label_reviews: allow the holdout as a third cohort

`cohort` records which set a question came from, and there were
two: `own` and `competitor`, the two label fixtures. The
prospective holdout is a third, and the check constraint refused
it — so every answer to it failed with a 500 and the owner got
"Internal Server Error" on the page he had just been asked to
spend twenty minutes on.

The holdout's publications are all own-account, so `own` would
have stored without complaint. It would also have been wrong:
scoring the holdout means separating answers written against a
model's guess from answers written blind, and if both say `own`
that separation is gone.

Revision ID: e8b2f14a7c35
Revises: d4a1c93b7e22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "e8b2f14a7c35"
down_revision: Union[str, Sequence[str], None] = "d4a1c93b7e22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAME = "ck_label_reviews_cohort"


def upgrade() -> None:
    op.drop_constraint(NAME, "label_reviews", type_="check")
    op.create_check_constraint(
        NAME, "label_reviews",
        "cohort in ('own','competitor','holdout')")


def downgrade() -> None:
    # Rows recorded against the holdout would violate the old
    # constraint, so they go first. Downgrading loses them, which
    # is what downgrading past the thing that stores them means.
    op.execute("delete from label_reviews where cohort = 'holdout'")
    op.drop_constraint(NAME, "label_reviews", type_="check")
    op.create_check_constraint(
        NAME, "label_reviews", "cohort in ('own','competitor')")
