"""
Account routes — self-service profile & billing info for authenticated users
============================================================================
GET  /account/me           → full profile, credit balance, session history
GET  /account/llm-settings → user's custom OpenRouter key/model (admin-gated)
PATCH /account/llm-settings → save custom key/model
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
import uuid

from app.db.database import get_db
from app.db.models import User, Session as InterviewSession, InterviewQA
from app.core.security import get_current_user

router = APIRouter()


@router.get("/me")
async def get_my_account(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the authenticated user's full account summary:
    - Profile (email, provider, joined date)
    - Credit balance and trial status
    - Session stats (total, this month)
    - 10 most recent sessions with basic info
    """
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Session stats
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_sessions = (await db.execute(
        select(func.count(InterviewSession.session_id))
        .where(InterviewSession.user_id == user.user_id)
    )).scalar() or 0

    sessions_this_month = (await db.execute(
        select(func.count(InterviewSession.session_id))
        .where(
            InterviewSession.user_id == user.user_id,
            InterviewSession.started_at >= month_start,
        )
    )).scalar() or 0

    # Recent sessions
    sessions_result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user.user_id)
        .order_by(InterviewSession.started_at.desc())
        .limit(10)
    )
    sessions = sessions_result.scalars().all()

    # For each session, count questions answered
    session_rows = []
    for s in sessions:
        qa_count = (await db.execute(
            select(func.count(InterviewQA.qa_id))
            .where(InterviewQA.session_id == s.session_id)
        )).scalar() or 0
        session_rows.append({
            "session_id":   str(s.session_id),
            "mode":         s.mode,
            "session_type": s.session_type,
            "status":       s.status,
            "started_at":   s.started_at,
            "ended_at":     s.ended_at,
            "questions_answered": qa_count,
        })

    # Redeemed coupons list (from feature_flags, excluding internal admin flags)
    flags = user.feature_flags or {}
    redeemed_coupons = flags.get("redeemed_coupons", [])

    return {
        # Profile
        "user_id":        str(user.user_id),
        "email":          user.email,
        "oauth_provider": user.oauth_provider,
        "joined_at":      user.created_at,
        # Credits
        "interview_credits":  user.interview_credits,
        "trial_used":         user.trial_used,
        "redeemed_coupons":   redeemed_coupons,
        # Session stats
        "total_sessions":      total_sessions,
        "sessions_this_month": sessions_this_month,
        "recent_sessions":     session_rows,
        # Feature flags (public subset — full flags stay server-side)
        "feature_flags": {
            "can_use_custom_llm": bool(flags.get("can_use_custom_llm")),
        },
    }


# ── Custom LLM settings ───────────────────────────────────────────────────────

class LLMSettingsRequest(BaseModel):
    api_key: str | None = None
    model: str | None = None


@router.get("/llm-settings")
async def get_llm_settings(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    flags = user.feature_flags or {}
    if not flags.get("can_use_custom_llm"):
        raise HTTPException(status_code=403, detail="Custom LLM access not enabled for this account")
    raw_key = flags.get("custom_openrouter_api_key", "")
    masked = ("sk-or-v1-****" + raw_key[-6:]) if raw_key else ""
    return {
        "api_key_set":    bool(raw_key),
        "api_key_masked": masked,
        "model":          flags.get("custom_openrouter_model", ""),
    }


@router.patch("/llm-settings")
async def save_llm_settings(
    body: LLMSettingsRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    flags = dict(user.feature_flags or {})
    if not flags.get("can_use_custom_llm"):
        raise HTTPException(status_code=403, detail="Custom LLM access not enabled for this account")
    if body.api_key:   # only overwrite if a non-empty key is supplied
        flags["custom_openrouter_api_key"] = body.api_key
    if body.model:     # only overwrite if a non-empty model is supplied
        flags["custom_openrouter_model"] = body.model
    user.feature_flags = flags
    await db.commit()
    return {"success": True}
