from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from langchain_text_splitters import RecursiveCharacterTextSplitter
from datetime import datetime
import secrets
import string
import uuid
from typing import Optional
from app.db.database import get_db
from app.db.models import CorpusChunk, User, Session as InterviewSession, Coupon, SiteSettings
from app.core.security import get_current_user
from app.core.config import settings
from app.core.embeddings import embed

router = APIRouter()

_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)


async def _require_admin(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Allow access if user_id is in ADMIN_USER_IDS (UUID list) OR email is in ADMIN_EMAILS."""
    if user_id in settings.ADMIN_USER_IDS:
        return user_id
    if settings.ADMIN_EMAILS:
        result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user and user.email in settings.ADMIN_EMAILS:
            return user_id
    raise HTTPException(status_code=403, detail="Admin access required")


# ── Corpus ingestion ──────────────────────────────────────────────────────────

@router.post("/corpus/ingest")
async def ingest_corpus(
    corpus_name: str,
    domain: str,
    file: UploadFile = File(...),
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: Upload a PDF/TXT corpus file. Chunks it, embeds, and stores in pgvector."""
    content = (await file.read()).decode("utf-8", errors="ignore")
    chunks = _splitter.split_text(content)
    inserted = 0
    for chunk in chunks:
        emb = embed(chunk)
        db.add(CorpusChunk(
            corpus_name=corpus_name,
            domain=domain,
            content=chunk,
            embedding=emb,
            ingested_by=uuid.UUID(user_id),
        ))
        inserted += 1
    return {
        "message": f"Successfully ingested {inserted} chunks",
        "corpus_name": corpus_name,
        "domain": domain,
        "chunk_count": inserted,
    }


@router.get("/corpus/list")
async def list_corpus(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CorpusChunk.corpus_name, CorpusChunk.domain).distinct()
    )
    return [{"corpus_name": r[0], "domain": r[1]} for r in result.all()]


# ── User management ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users with pagination. Optional email search."""
    query = select(User).order_by(User.created_at.desc())
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return [
        {
            "user_id": str(u.user_id),
            "email": u.email,
            "oauth_provider": u.oauth_provider,
            "created_at": u.created_at,
            "interview_credits": u.interview_credits,
            "trial_used": u.trial_used,
            "feature_flags": u.feature_flags or {},
        }
        for u in users
    ]


@router.get("/users/{target_user_id}")
async def get_user_detail(
    target_user_id: str,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """User detail: profile + session history + credits."""
    result = await db.execute(
        select(User).where(User.user_id == uuid.UUID(target_user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    sessions_result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == uuid.UUID(target_user_id))
        .order_by(InterviewSession.started_at.desc())
        .limit(20)
    )
    sessions = sessions_result.scalars().all()

    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "oauth_provider": user.oauth_provider,
        "created_at": user.created_at,
        "interview_credits": user.interview_credits,
        "trial_used": user.trial_used,
        "stripe_customer_id": user.stripe_customer_id,
        "feature_flags": user.feature_flags or {},
        "recent_sessions": [
            {
                "session_id": str(s.session_id),
                "mode": s.mode,
                "session_type": s.session_type,
                "status": s.status,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
            }
            for s in sessions
        ],
    }


class GrantCreditsRequest(BaseModel):
    credits: int


@router.patch("/users/{target_user_id}/credits")
async def grant_credits(
    target_user_id: str,
    body: GrantCreditsRequest,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: set interview_credits for a user (absolute value, not additive)."""
    result = await db.execute(
        select(User).where(User.user_id == uuid.UUID(target_user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.interview_credits = body.credits
    return {"user_id": target_user_id, "interview_credits": user.interview_credits}


class UpdateFlagsRequest(BaseModel):
    flags: dict  # e.g. {"extra_credits": 2, "test_mode": true}


@router.patch("/users/{target_user_id}/flags")
async def update_feature_flags(
    target_user_id: str,
    body: UpdateFlagsRequest,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: merge feature flags for a user (does not wipe existing flags)."""
    result = await db.execute(
        select(User).where(User.user_id == uuid.UUID(target_user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    current = dict(user.feature_flags or {})
    current.update(body.flags)
    user.feature_flags = current
    return {"user_id": target_user_id, "feature_flags": user.feature_flags}


class ResetPasswordRequest(BaseModel):
    temp_password: str


@router.post("/users/{target_user_id}/reset-password")
async def admin_reset_password(
    target_user_id: str,
    body: ResetPasswordRequest,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: set a temporary password for a local-auth user; forces them to change it on next login."""
    from app.core.security import pwd_context
    result = await db.execute(
        select(User).where(User.user_id == uuid.UUID(target_user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.oauth_provider != "local":
        raise HTTPException(status_code=400, detail="Password reset only applies to local (email/password) accounts")
    user.password_hash = pwd_context.hash(body.temp_password)
    flags = dict(user.feature_flags or {})
    flags["must_change_password"] = True
    user.feature_flags = flags
    await db.commit()
    return {"success": True, "temp_password": body.temp_password}


@router.get("/analytics")
async def get_analytics(
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Usage statistics: users, sessions, credits."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)

    total_users = (await db.execute(select(func.count(User.user_id)))).scalar()
    trial_users = (await db.execute(
        select(func.count(User.user_id)).where(User.trial_used == True)  # noqa: E712
    )).scalar()
    paid_users = (await db.execute(
        select(func.count(User.user_id)).where(User.interview_credits > 0)
    )).scalar()
    credits_outstanding = (await db.execute(
        select(func.sum(User.interview_credits))
    )).scalar() or 0

    sessions_today = (await db.execute(
        select(func.count(InterviewSession.session_id))
        .where(InterviewSession.started_at >= today_start)
    )).scalar()
    sessions_this_month = (await db.execute(
        select(func.count(InterviewSession.session_id))
        .where(InterviewSession.started_at >= month_start)
    )).scalar()

    return {
        "total_users": total_users,
        "trial_users": trial_users,
        "paid_users": paid_users,
        "credits_outstanding": credits_outstanding,
        "sessions_today": sessions_today,
        "sessions_this_month": sessions_this_month,
    }


# ── Coupon management ─────────────────────────────────────────────────────────

def _generate_code(length: int = 10) -> str:
    """Generate a random uppercase alphanumeric coupon code, e.g. 'BETA2X9KQR'."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class CreateCouponRequest(BaseModel):
    credits:   int             # credits granted per redemption
    max_uses:  Optional[int] = None   # None = unlimited
    expires_at: Optional[datetime] = None
    note:      Optional[str] = None   # internal label
    code:      Optional[str] = None   # custom code; auto-generated if omitted


@router.post("/coupons")
async def create_coupon(
    body: CreateCouponRequest,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: create a new coupon code that grants interview credits on redemption."""
    if body.credits <= 0:
        raise HTTPException(status_code=400, detail="credits must be a positive integer")

    code = (body.code or "").strip().upper() or _generate_code()

    # Ensure unique code
    existing = await db.execute(select(Coupon).where(Coupon.code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Coupon code '{code}' already exists")

    coupon = Coupon(
        code=code,
        credits=body.credits,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
        note=body.note,
        created_by=uuid.UUID(user_id),
    )
    db.add(coupon)
    await db.flush()

    return {
        "coupon_id": str(coupon.coupon_id),
        "code":      coupon.code,
        "credits":   coupon.credits,
        "max_uses":  coupon.max_uses,
        "uses":      coupon.uses,
        "is_active": coupon.is_active,
        "expires_at": coupon.expires_at,
        "note":      coupon.note,
        "created_at": coupon.created_at,
    }


@router.get("/coupons")
async def list_coupons(
    include_inactive: bool = False,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list all coupons, sorted newest first. Pass include_inactive=true to see deactivated ones."""
    query = select(Coupon).order_by(Coupon.created_at.desc())
    if not include_inactive:
        query = query.where(Coupon.is_active == True)  # noqa: E712
    result = await db.execute(query)
    coupons = result.scalars().all()
    return [
        {
            "coupon_id":  str(c.coupon_id),
            "code":       c.code,
            "credits":    c.credits,
            "max_uses":   c.max_uses,
            "uses":       c.uses,
            "is_active":  c.is_active,
            "expires_at": c.expires_at,
            "note":       c.note,
            "created_at": c.created_at,
        }
        for c in coupons
    ]


@router.patch("/coupons/{coupon_id}/deactivate")
async def deactivate_coupon(
    coupon_id: str,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: deactivate a coupon so it can no longer be redeemed (soft delete)."""
    result = await db.execute(select(Coupon).where(Coupon.coupon_id == uuid.UUID(coupon_id)))
    coupon = result.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = False
    return {"coupon_id": coupon_id, "is_active": False}


@router.patch("/coupons/{coupon_id}/reactivate")
async def reactivate_coupon(
    coupon_id: str,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: re-enable a previously deactivated coupon."""
    result = await db.execute(select(Coupon).where(Coupon.coupon_id == uuid.UUID(coupon_id)))
    coupon = result.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = True
    return {"coupon_id": coupon_id, "is_active": True}


# ── Site settings management ──────────────────────────────────────────────────

class UpdateSettingsRequest(BaseModel):
    settings: dict  # e.g. {"contact_email": "new@example.com"}


@router.patch("/settings")
async def update_site_settings(
    body: UpdateSettingsRequest,
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: upsert one or more site_settings key-value pairs."""
    for key, value in body.settings.items():
        result = await db.execute(select(SiteSettings).where(SiteSettings.key == key))
        row = result.scalar_one_or_none()
        if row:
            row.value = str(value)
        else:
            db.add(SiteSettings(key=key, value=str(value)))
    await db.commit()
    return {"updated": list(body.settings.keys())}


@router.get("/settings")
async def get_site_settings(
    user_id: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list all site_settings entries."""
    result = await db.execute(select(SiteSettings))
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}
