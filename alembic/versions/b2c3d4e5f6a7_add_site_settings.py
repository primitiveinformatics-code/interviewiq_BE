"""add_site_settings

Creates the site_settings key-value table and seeds default contact info
and banner text.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-25 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULTS = {
    "contact_email":        "primitiveinformatics@gmail.com",
    "contact_phone":        "+91 7907341911",
    "contact_whatsapp":     "+91 7907341911",
    "banner_message":       "Found a bug in production? Report it and earn interview credits!",
    "banner_popup_details": (
        "Send details of the bug to our contact email ID. "
        "Credits are subject to terms and conditions and are at the sole discretion of the admin."
    ),
}


def upgrade() -> None:
    site_settings = op.create_table(
        "site_settings",
        sa.Column("key",   sa.String(), primary_key=True, nullable=False),
        sa.Column("value", sa.String(), nullable=False),
    )
    op.bulk_insert(site_settings, [{"key": k, "value": v} for k, v in DEFAULTS.items()])


def downgrade() -> None:
    op.drop_table("site_settings")
