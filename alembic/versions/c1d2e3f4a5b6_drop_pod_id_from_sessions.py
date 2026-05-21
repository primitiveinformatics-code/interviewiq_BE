"""drop_pod_id_from_sessions

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-05-20 00:00:00.000000

Removes the dead `pod_id` column from the sessions table.
This column was leftover K8s scaffolding (intended for pod-per-session routing)
that was never used in the current Docker / Railway deployment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('sessions', 'pod_id')


def downgrade() -> None:
    op.add_column(
        'sessions',
        sa.Column('pod_id', sa.String(), nullable=True),
    )
