"""
Database connection and session management.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import settings

Base = declarative_base()

# Ensure URL uses asyncpg driver
_db_url = settings.DATABASE_URL
if _db_url and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(
    _db_url,
    echo=settings.ENVIRONMENT == "development",
    future=True,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
