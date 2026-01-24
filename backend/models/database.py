"""
Database connection and session management.

Uses SQLAlchemy async with connection pooling optimized for Supabase's session pooler.

Connection Pool Strategy:
- Engine is created once (singleton) with a connection pool
- Sessions are lightweight wrappers that checkout connections from the pool
- When a session closes, the connection returns to the pool for reuse
- Connections are NOT created/destroyed per request - they're reused from the pool
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, AsyncEngine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool, QueuePool

from config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

# Ensure URL uses asyncpg driver
_db_url = settings.DATABASE_URL
if _db_url and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://")

# Global singletons - created once, reused forever
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Get the database engine (singleton - created once, reused)."""
    global _engine
    if _engine is None:
        # Use NullPool in production when using external connection pooler (Supabase/PgBouncer)
        # This lets the external pooler manage all connections
        is_production = settings.ENVIRONMENT == "production"
        
        if is_production:
            _engine = create_async_engine(
                _db_url,
                echo=False,
                future=True,
                poolclass=NullPool,  # No local pooling - external pooler handles it
            )
            logger.info("Database engine created with NullPool (external pooler manages connections)")
        else:
            _engine = create_async_engine(
                _db_url,
                echo=True,
                future=True,
                poolclass=QueuePool,
                pool_size=3,  # Small pool for local dev
                max_overflow=2,
                pool_pre_ping=True,
                pool_recycle=300,
                pool_timeout=30,
                pool_reset_on_return="rollback",
            )
            logger.info("Database engine created with QueuePool: size=3, max_overflow=2")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the async session factory (singleton - created once, reused)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,  # Don't auto-flush, we control when to commit
        )
        logger.info("Session factory created (will reuse pooled connections)")
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async database session that uses a pooled connection.
    
    How it works:
    - Session checks out a connection from the pool
    - When session closes, connection returns to pool (NOT destroyed)
    - Next request reuses the same connection from the pool
    
    Usage:
        async with get_session() as session:
            result = await session.execute(query)
            await session.commit()  # Explicit commit if needed
    
    The session is automatically closed when the context exits.
    Any uncommitted changes are rolled back on error.
    """
    factory = get_session_factory()
    session: AsyncSession = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        # This returns the connection to the pool, doesn't close it
        await session.close()


async def init_db() -> None:
    """Create all tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Close the database engine and release all pooled connections.
    Call this on application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        pool_status = get_pool_status()
        logger.info(
            "Closing database pool: %d checked_in, %d checked_out",
            pool_status["checked_in"],
            pool_status["checked_out"]
        )
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed, all connections closed")


def get_pool_status() -> dict[str, int | str]:
    """
    Get current connection pool status for monitoring.
    
    Returns:
        - pool_size: Configured base pool size
        - checked_in: Connections available in pool (ready to reuse)
        - checked_out: Connections currently in use
        - overflow: Extra connections beyond pool_size
    """
    engine = get_engine()
    pool = engine.pool
    
    # NullPool doesn't track connections - it creates/destroys per request
    if isinstance(pool, NullPool):
        return {
            "pool_type": "NullPool",
            "pool_size": 0,
            "checked_in": 0,
            "checked_out": 0,
            "overflow": 0,
        }
    
    return {
        "pool_type": "QueuePool",
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


# Create engine and factory on module load for backwards compatibility
# Code should prefer get_session() context manager
engine = get_engine()
AsyncSessionLocal = get_session_factory()
