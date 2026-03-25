"""resize_vector_columns_to_1024

Revision ID: a1b2c3d4e5f6
Revises: 4fcd8a0a33e8
Create Date: 2026-03-25 19:00:00.000000

The embedding columns were originally created as vector(1536) (OpenAI ada-002 size).
The system now uses Cohere embeddings which produce 1024-dimensional vectors.
Existing stored vectors (if any) are dropped during the resize — they were
generated with the wrong model and would give incorrect similarity scores anyway.
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '4fcd8a0a33e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop and re-add the columns — PostgreSQL cannot resize a vector column
    # in-place because the stored binary representation changes with dimensions.
    # Any previously stored embeddings were produced by a different model and
    # must be re-ingested regardless, so data loss here is intentional.
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE long_term_memory ADD COLUMN embedding vector(1024)")

    op.execute("ALTER TABLE corpus_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE corpus_chunks ADD COLUMN embedding vector(1024)")


def downgrade() -> None:
    op.execute("ALTER TABLE long_term_memory DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE long_term_memory ADD COLUMN embedding vector(1536)")

    op.execute("ALTER TABLE corpus_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE corpus_chunks ADD COLUMN embedding vector(1536)")
