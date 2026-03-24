"""change_embedding_dim_to_1024

Switches embedding columns from Vector(1536) (OpenAI) to Vector(1024) (Cohere
embed-english-v3.0). Embeddings are fully re-generatable from source text so
dropping and recreating the columns is safe.

NOTE: On a fresh Railway Postgres created by create_all(), this migration is a
no-op because the tables are already created with Vector(1024). Run
`alembic stamp head` after first deploy to initialise alembic tracking.

Revision ID: a1b2c3d4e5f6
Revises: 4fcd8a0a33e8
Create Date: 2026-03-24 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '4fcd8a0a33e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('long_term_memory', 'embedding')
    op.add_column('long_term_memory', sa.Column('embedding', Vector(1024), nullable=True))
    op.drop_column('corpus_chunks', 'embedding')
    op.add_column('corpus_chunks', sa.Column('embedding', Vector(1024), nullable=True))


def downgrade() -> None:
    op.drop_column('long_term_memory', 'embedding')
    op.add_column('long_term_memory', sa.Column('embedding', Vector(1536), nullable=True))
    op.drop_column('corpus_chunks', 'embedding')
    op.add_column('corpus_chunks', sa.Column('embedding', Vector(1536), nullable=True))
