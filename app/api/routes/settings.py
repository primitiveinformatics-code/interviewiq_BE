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
    "contact_email":        "admin@primitiveinformatics.in",
    "contact_phone":        "+91 7907341911",
    "contact_whatsapp":     "+91 7907341911",
    "banner_message":       "",
    "banner_popup_details": "",
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
    merged = {**_CONTACT_DEFAULTS, **overrides}

    # Derive WhatsApp URL from the stored number (strip non-digit chars)
    whatsapp_digits = "".join(c for c in merged["contact_whatsapp"] if c.isdigit())

    return {
        "email":               merged["contact_email"],
        "phone":               merged["contact_phone"],
        "whatsapp":            merged["contact_whatsapp"],
        "whatsapp_url":        f"https://wa.me/{whatsapp_digits}",
        "banner_message":      merged["banner_message"],
        "banner_popup_details": merged["banner_popup_details"],
    }
