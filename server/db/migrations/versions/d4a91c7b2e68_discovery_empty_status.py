"""discovery jobs: an `empty` status for paid runs that returned nothing

MEASURED 2026-10-26. `apidojo/tiktok-scraper` began answering every query
— creator and hashtag alike — with ten `{"noResults": true}` sentinel
rows, while Apify continued to bill $0.0030 per run. Because the job was
recorded as `ok` with zero publications, a broken provider was
indistinguishable from a search for something that genuinely does not
exist, and three runs bought nothing before it was noticed.

`empty` makes that state visible and queryable. It is deliberately not
`failed`: nothing errored, the provider simply had nothing to give, and
the distinction matters when deciding whether to retry.

Revision ID: d4a91c7b2e68
Revises: c81f3a7e04d2
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "d4a91c7b2e68"
down_revision: Union[str, Sequence[str], None] = "c81f3a7e04d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "status in ('queued','running','ok','failed','refused')"
_NEW = "status in ('queued','running','ok','empty','failed','refused')"


def upgrade() -> None:
    op.drop_constraint("ck_discovery_job_status", "discovery_jobs",
                       type_="check")
    op.create_check_constraint("ck_discovery_job_status", "discovery_jobs",
                               _NEW)


def downgrade() -> None:
    # Any row already carrying the new value would violate the old
    # constraint, so it is mapped to the nearest older meaning rather than
    # left to break the downgrade.
    op.execute("update discovery_jobs set status = 'ok' "
               "where status = 'empty'")
    op.drop_constraint("ck_discovery_job_status", "discovery_jobs",
                       type_="check")
    op.create_check_constraint("ck_discovery_job_status", "discovery_jobs",
                               _OLD)
