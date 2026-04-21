import asyncio
import json
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import uuid
from app.db.database import get_db, IS_LAMBDA
from app.db.models import Session as InterviewSession, SessionMode, User
from app.core.security import get_current_user
from app.core.logging_config import get_logger

log = get_logger("api.sessions")

router = APIRouter()


class StartSessionRequest(BaseModel):
    mode: SessionMode
    jd_doc_id: str
    resume_doc_id: str


@router.post("/start")
async def start_session(
    body: StartSessionRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # ── Fetch user to check credits / trial status ──────────────────────────
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Testing mode is reserved for admins (checked by ADMIN_USER_IDS / ADMIN_EMAILS elsewhere)
    if body.mode == SessionMode.testing:
        from app.core.config import settings
        is_admin = (
            user_id in settings.ADMIN_USER_IDS
            or user.email in settings.ADMIN_EMAILS
            or (user.feature_flags or {}).get("test_mode", False)
        )
        if not is_admin:
            raise HTTPException(status_code=403, detail="Testing mode is restricted to admins.")
        session_type = "testing"

    elif not user.trial_used:
        # First-ever session → free trial (3-question limit enforced in interview.py)
        user.trial_used = True
        session_type = "trial"

    else:
        # Paid credits required
        extra_credits = (user.feature_flags or {}).get("extra_credits", 0)
        effective_credits = user.interview_credits + extra_credits

        if effective_credits < 1:
            raise HTTPException(
                status_code=402,
                detail="No interview credits remaining. Purchase credits at /billing/checkout to continue.",
            )
        # Deduct one credit (extra_credits are virtual and not stored back)
        if user.interview_credits > 0:
            user.interview_credits -= 1
        session_type = "full"

    # ── Auto-close any existing active sessions (one active session per user) ──
    active_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.user_id == uuid.UUID(user_id),
            InterviewSession.status == "active",
        )
    )
    for old_sess in active_result.scalars().all():
        log.info(f"Auto-closing active session {old_sess.session_id} for user {user_id}")
        from app.core.session_utils import auto_close_session
        if IS_LAMBDA:
            import boto3 as _boto3
            _boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-east-1")).send_message(
                QueueUrl=os.environ["SESSION_CLEANUP_QUEUE_URL"],
                MessageBody=json.dumps({"session_id": str(old_sess.session_id)}),
            )
        else:
            asyncio.create_task(auto_close_session(str(old_sess.session_id)))

    # ── Create the session ──────────────────────────────────────────────────
    session = InterviewSession(
        user_id=uuid.UUID(user_id),
        mode=body.mode,
        session_type=session_type,
        status="active",
        pod_id=f"pod-{uuid.uuid4().hex[:8]}",
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    return {
        "session_id":   str(session.session_id),
        "pod_id":       session.pod_id,
        "mode":         session.mode,
        "session_type": session_type,
        "status":       "active",
    }


@router.get("/{session_id}/status")
async def session_status(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InterviewSession).where(InterviewSession.session_id == uuid.UUID(session_id))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id":   session_id,
        "status":       session.status,
        "session_type": session.session_type,
        "started_at":   session.started_at,
    }


@router.delete("/{session_id}/end")
async def end_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InterviewSession).where(InterviewSession.session_id == uuid.UUID(session_id))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = "completed"
    session.ended_at = datetime.now(timezone.utc)
    return {"session_id": session_id, "status": "completed"}
