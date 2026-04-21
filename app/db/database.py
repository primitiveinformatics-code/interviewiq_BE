import os
import re
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from app.core.config import settings

# True when running inside AWS Lambda (env var set automatically by the runtime)
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _prepare_async_url(url: str) -> tuple[str, dict]:
    """
    Make a database URL safe for asyncpg:
    - Normalise the driver prefix to postgresql+asyncpg://
    - Strip ?sslmode=… (asyncpg doesn't accept it as a kwarg) and
      convert require/verify-* to connect_args={'ssl': True}.
    """
    # Ensure the asyncpg driver is specified
    url = re.sub(r'^postgres(?:ql)?://', 'postgresql+asyncpg://', url)

    connect_args: dict = {}
    match = re.search(r'[?&]sslmode=([^&\s]+)', url)
    if match:
        sslmode = match.group(1)
        url = re.sub(r'([?&])sslmode=[^&]*', '', url)
        url = re.sub(r'[?&]$', '', url)
        if sslmode in ('require', 'verify-ca', 'verify-full'):
            connect_args['ssl'] = True

    return url, connect_args


_async_url, _async_connect_args = _prepare_async_url(settings.DATABASE_URL)

if IS_LAMBDA:
    # Lambda: NullPool prevents connection pool exhaustion across concurrent invocations.
    # Each invocation opens and closes its own connection; no idle connections held.
    engine = create_engine(
        settings.SYNC_DATABASE_URL,
        poolclass=NullPool,
        connect_args={'connect_timeout': 10},
    )
    async_engine = create_async_engine(
        _async_url,
        echo=False,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={**_async_connect_args, 'timeout': 10},
    )
else:
    engine = create_engine(
        settings.SYNC_DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={'connect_timeout': 10},
    )
    async_engine = create_async_engine(
        _async_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={**_async_connect_args, 'timeout': 10},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
