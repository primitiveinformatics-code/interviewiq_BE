import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from app.api.routes import auth, sessions, documents, interview, reports, admin, audio, llmtest, billing, account
from app.api.routes.audio import warmup_whisper_model
from app.core.config import settings
from app.db.database import engine, Base, AsyncSessionLocal
from app.db.models import Session as InterviewSession
from app.core.logging_config import get_logger

log = get_logger("main")

# ── Redis client (module-level so interview.py can import it) ─────────────────
redis_client = None


async def _stale_session_cleanup_loop() -> None:
    """
    Background task: every 5 minutes, find sessions that have been 'active'
    for more than 1 hour and auto-close them (generate report + save QAs).
    """
    from app.core.session_utils import auto_close_session
    log.info("Stale-session cleanup loop started.")
    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        try:
            cutoff = datetime.utcnow() - timedelta(hours=1)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(InterviewSession).where(
                        InterviewSession.status == "active",
                        InterviewSession.started_at < cutoff,
                    )
                )
                stale = result.scalars().all()
                if stale:
                    log.info(f"Stale-session sweep: found {len(stale)} sessions to close")
                    for sess in stale:
                        asyncio.create_task(auto_close_session(str(sess.session_id)))
        except Exception as exc:
            log.error(f"Stale-session cleanup error: {exc}")


async def _init_database_with_retries(max_retries: int = 5) -> None:
    """Initialize database with retry logic for connection failures."""
    for attempt in range(max_retries):
        try:
            log.info("Enabling pgvector extension...")
            with engine.connect() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
            log.info("pgvector extension ready.")

            log.info("Creating database tables...")
            Base.metadata.create_all(bind=engine)
            log.info("Database tables created.")
            return  # Success
        except Exception as e:
            wait_time = 2 ** attempt  # exponential backoff
            if attempt < max_retries - 1:
                log.warning(
                    f"Database init failed (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
            else:
                log.error(
                    f"Database init failed after {max_retries} attempts: {e}. "
                    "Continuing without database initialization."
                )
                return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init database + Redis + background tasks. Shutdown: close Redis."""
    global redis_client
    import redis.asyncio as aioredis

    # Initialize database with retries
    await _init_database_with_retries()

    log.info("Connecting to Redis...")
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    log.info("Redis connected.")

    asyncio.create_task(_stale_session_cleanup_loop())

    # Pre-warm the Whisper model in the background so it is ready by the time
    # the first user reaches the interview page. Uses a background task so it
    # doesn't delay server startup or health-check responses.
    asyncio.create_task(warmup_whisper_model())

    yield  # ← application runs here

    log.info("Closing Redis connection...")
    await redis_client.aclose()
    log.info("Redis closed.")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="InterviewIQ API", version="1.0.0", lifespan=lifespan)
log.info("FastAPI app initialised.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,      prefix="/auth",      tags=["auth"])
app.include_router(sessions.router,  prefix="/sessions",  tags=["sessions"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(interview.router, prefix="/interview", tags=["interview"])
app.include_router(reports.router,   prefix="/reports",   tags=["reports"])
app.include_router(admin.router,     prefix="/admin",     tags=["admin"])
app.include_router(audio.router,     prefix="/audio",     tags=["audio"])
app.include_router(billing.router,   prefix="/billing",   tags=["billing"])
app.include_router(account.router,   prefix="/account",   tags=["account"])

# llmtest endpoints are only mounted in non-production environments.
if settings.APP_ENV != "production":
    app.include_router(llmtest.router, prefix="/llmtest", tags=["llmtest"])


@app.get("/health")
async def health():
    log.debug("Health check called.")
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    log.info("Starting uvicorn server on 0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
