"""
Auto-close sessions: load Redis state, generate report, save QAs to DB.
Called from:
  - sessions.py  → when a new session starts (one active session per user)
  - main.py      → background sweep for sessions inactive >1 hour
"""
import asyncio
import pickle
import uuid
from datetime import datetime
from sqlalchemy import select
import redis.asyncio as aioredis

from app.db.database import SessionLocal
from app.db.models import Session as InterviewSession, InterviewQA
from app.core.config import settings
from app.core.logging_config import get_logger

log = get_logger("core.session_utils")

# Module-level singleton so this module works in Lambda (lifespan never runs there).
_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_keepalive=True,
            socket_connect_timeout=10,
        )
    return _redis_client


async def auto_close_session(session_id: str) -> None:
    """
    Gracefully close an active interview session:
    1. Load in-progress state from Redis
    2. Run the report agent if scored answers exist
    3. Save QA pairs to interview_qa table
    4. Mark session status = 'completed' with ended_at = now
    5. Delete the Redis key
    """
    log.info(f"auto_close: starting for session {session_id}")

    # ── 1. Load Redis state ───────────────────────────────────────────────
    state: dict | None = None
    try:
        redis = await _get_redis()
        raw = await redis.get(f"interview:{session_id}")
        if raw:
            state = pickle.loads(raw)
            log.info(
                f"auto_close: Redis state loaded for {session_id} "
                f"({len(state.get('answers', []))} answers)"
            )
    except Exception as exc:
        log.warning(f"auto_close: Redis load failed for {session_id}: {exc}")

    # ── 2. Generate report if answers with scores exist ───────────────────
    if state:
        scored = [
            a for a in state.get("answers", [])
            if a.get("question") and a.get("scores")
        ]
        if scored:
            try:
                from app.agents.report_agent import report_agent_node
                result = await asyncio.to_thread(report_agent_node, state)
                state.update(result)
                score = (result.get("final_report") or {}).get("aggregate_score", "?")
                log.info(f"auto_close: report generated for {session_id}, score={score}")
            except Exception as exc:
                log.error(f"auto_close: report generation failed for {session_id}: {exc}")

    # ── 3 + 4. Save QAs + mark session completed ──────────────────────────
    db = SessionLocal()
    try:
        answers_to_save = []
        if state:
            answers_to_save = [
                a for a in state.get("answers", [])
                if a.get("question") and a.get("scores")
            ]

        for qa in answers_to_save:
            db.add(InterviewQA(
                session_id=uuid.UUID(session_id),
                question=qa["question"],
                answer=qa.get("answer", ""),
                topic=qa.get("topic", ""),
                scores=qa.get("scores"),
            ))

        sess = db.execute(
            select(InterviewSession)
            .where(InterviewSession.session_id == uuid.UUID(session_id))
        ).scalar_one_or_none()

        if sess and sess.status == "active":
            sess.status = "completed"
            sess.ended_at = datetime.utcnow()

        db.commit()
        log.info(
            f"auto_close: {len(answers_to_save)} QAs saved, "
            f"session {session_id} marked completed"
        )
    except Exception as exc:
        log.error(f"auto_close: DB error for {session_id}: {exc}")
        db.rollback()
    finally:
        db.close()

    # ── 5. Clean up Redis ─────────────────────────────────────────────────
    try:
        redis = await _get_redis()
        await redis.delete(f"interview:{session_id}")
    except Exception as exc:
        log.warning(f"auto_close: Redis cleanup failed for {session_id}: {exc}")
