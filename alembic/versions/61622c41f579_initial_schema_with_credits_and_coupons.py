"""initial_schema_with_credits_and_coupons

Revision ID: 61622c41f579
Revises: ba24b7fa87df
Create Date: 2026-03-23 13:57:36.497914

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61622c41f579'
down_revision: Union[str, None] = 'ba24b7fa87df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty: the initial schema (users, sessions, documents,
    # interview_qa, long_term_memory, coupons, site_settings, corpus_chunks)
    # was created by the preceding migration (ba24b7fa87df). This revision was
    # generated when credits/coupon fields were confirmed already present in the
    # baseline, so no DDL change is required here.
    pass


def downgrade() -> None:
    # No DDL to reverse — see upgrade() comment.
    pass
