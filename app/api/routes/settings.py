from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from app.db.database import get_db
from app.db.models import SiteSettings

router = APIRouter()

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


async def _get(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(SiteSettings).where(SiteSettings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULTS.get(key, "")


class ContactResponse(BaseModel):
    email: str
    phone: str
    whatsapp: str
    whatsapp_url: str
    banner_message: str
    banner_popup_details: str


@router.get("/contact", response_model=ContactResponse)
async def get_contact(db: AsyncSession = Depends(get_db)):
    phone = await _get(db, "contact_whatsapp")
    digits = phone.replace("+", "").replace(" ", "")
    return ContactResponse(
        email=await _get(db, "contact_email"),
        phone=await _get(db, "contact_phone"),
        whatsapp=phone,
        whatsapp_url=f"https://wa.me/{digits}",
        banner_message=await _get(db, "banner_message"),
        banner_popup_details=await _get(db, "banner_popup_details"),
    )
