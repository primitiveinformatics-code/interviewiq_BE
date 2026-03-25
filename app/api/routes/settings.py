"""
Settings routes — public site-wide configuration
=================================================
GET /settings/contact  → contact/support information (no auth required)
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import SiteSettings

router = APIRouter()

# Hardcoded defaults — overridden by rows in the site_settings table if present.
_CONTACT_DEFAULTS = {
    "support_email": "support@interviewiq.app",
    "twitter":       "",
    "linkedin":      "",
    "instagram":     "",
    "discord":       "",
}


@router.get("/contact")
async def get_contact(db: AsyncSession = Depends(get_db)):
    """Return public contact / social links. No authentication required."""
    result = await db.execute(
        select(SiteSettings).where(
            SiteSettings.key.in_(list(_CONTACT_DEFAULTS.keys()))
        )
    )
    overrides = {row.key: row.value for row in result.scalars().all()}
    return {**_CONTACT_DEFAULTS, **overrides}
