"""feature_evidence: the reliability record a feature's grade is read from

M4 asks for "one registry of every feature ... with grade, method
version, reliability evidence and platform availability". The first
three of those had nowhere to live.

Associations were computed live by `visual_structure.run()` and printed
to a terminal. The numbers reached `docs/benchmarks/6c-visual-density.md`
by being pasted there, which means a grade derived from them would have
been a grade derived from a document — and no code could check it. This
table is where an association result lands so that the catalogue can
*derive* a grade instead of asserting one, and so that a stale grade is
a query rather than a reading.

One row is one tested relationship: a feature, under a method version,
against one outcome, on one named population, with the controls that
were applied. The controls are part of the identity on purpose. The
same feature against the same outcome with and without the ad-exposure
covariate is two different findings, and M1 exists because conflating
them was already costing this project its outcome variable.

`q_value` is the Benjamini-Hochberg adjusted p across the family the
run tested. It is nullable because a single pre-registered test has no
family to correct against, and storing the raw p there would quietly
promote it.

Revision ID: a2f49c81b73e
Revises: b9e4f2c70d18
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a2f49c81b73e"
down_revision: Union[str, Sequence[str], None] = "b9e4f2c70d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_evidence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        # Which outcome the feature was tested against, e.g. the
        # retention proxy or a per-viewer engagement rate. Named rather
        # than assumed: M1 established that "views" and "retention" are
        # different questions with different contamination.
        sa.Column("outcome", sa.String(64), nullable=False),
        # A named, reproducible population - e.g. own_instagram_reels.
        # Two runs over different corpora are different evidence.
        sa.Column("population", sa.String(64), nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("rho", sa.Float),
        sa.Column("p_value", sa.Float),
        # Benjamini-Hochberg across the family this run tested. NULL
        # when there was no family, never a copy of the raw p.
        sa.Column("q_value", sa.Float),
        sa.Column("ci_low", sa.Float),
        sa.Column("ci_high", sa.Float),
        # After partialling out the controls below.
        sa.Column("partial_rho", sa.Float),
        # The covariates that were held constant, as a list. Part of the
        # identity of the finding, not a footnote.
        sa.Column("controls", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        # How many publications in the population had no value for this
        # feature. A strong rho on a third of the corpus is a different
        # claim from a strong rho on all of it.
        sa.Column("unavailable_n", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("direction", sa.String(16)),
        sa.Column("note", sa.Text),
        # What produced this row, so a finding can be traced to a run.
        sa.Column("analysis", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("n >= 0", name="ck_feature_evidence_n"),
        sa.CheckConstraint(
            "rho is null or (rho >= -1.0 and rho <= 1.0)",
            name="ck_feature_evidence_rho"),
        sa.CheckConstraint(
            "q_value is null or (q_value >= 0.0 and q_value <= 1.0)",
            name="ck_feature_evidence_q"),
        sa.CheckConstraint(
            "direction is null or direction in ('positive','negative','none')",
            name="ck_feature_evidence_direction"),
    )
    # One current result per tested relationship. A re-run upserts on
    # this rather than appending, because two live rows for the same
    # question would let the catalogue pick whichever it read first.
    op.create_index(
        "uq_feature_evidence_finding", "feature_evidence",
        ["feature", "method_version", "outcome", "population", "analysis"],
        unique=True)
    op.create_index("ix_feature_evidence_feature", "feature_evidence",
                    ["feature"])


def downgrade() -> None:
    op.drop_index("ix_feature_evidence_feature",
                  table_name="feature_evidence")
    op.drop_index("uq_feature_evidence_finding",
                  table_name="feature_evidence")
    op.drop_table("feature_evidence")
