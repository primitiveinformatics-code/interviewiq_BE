"""
/llmtest API routes
===================
Endpoints used by the LLM Test Panel (pages/llmtest.py) to read pending
prompts and submit tester-provided responses during test-mode interviews.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
from app.agents.llmtest_store import llmtest_store
from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models import User
from app.core.config import settings

router = APIRouter()


async def _require_admin(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Allow access if user_id is in ADMIN_USER_IDS or email is in ADMIN_EMAILS."""
    if user_id in settings.ADMIN_USER_IDS:
        return user_id
    if settings.ADMIN_EMAILS:
        result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user and user.email in settings.ADMIN_EMAILS:
            return user_id
    raise HTTPException(status_code=403, detail="Admin access required")


class RespondRequest(BaseModel):
    response: str


@router.get("/pending")
async def get_pending(admin: str = Depends(_require_admin)):
    """Return the current pending LLM prompt, or null if none is waiting."""
    return {"pending": llmtest_store.get_pending()}


@router.post("/respond")
async def post_response(body: RespondRequest, admin: str = Depends(_require_admin)):
    """Submit a tester response for the currently pending LLM prompt."""
    ok = llmtest_store.submit_response(body.response)
    if not ok:
        raise HTTPException(status_code=409, detail="No pending prompt to respond to.")
    return {"ok": True}


@router.get("/history")
async def get_history(admin: str = Depends(_require_admin)):
    """Return the full history of all intercepted prompts and responses."""
    return {"history": llmtest_store.get_history()}


@router.delete("/history")
async def clear_history(admin: str = Depends(_require_admin)):
    """Clear the prompt/response history."""
    llmtest_store.clear_history()
    return {"ok": True}
