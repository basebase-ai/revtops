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
from sqlalchemy.pool import NullPool

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
        
        # Disable prepared statement cache for pgbouncer/Supabase compatibility
        # pgbouncer in transaction mode doesn't support prepared statements
        connect_args = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }
        
        if is_production:
            _engine = create_async_engine(
                _db_url,
                echo=False,
                future=True,
                poolclass=NullPool,  # No local pooling - external pooler handles it
                connect_args=connect_args,
            )
            logger.info("Database engine created with NullPool (external pooler manages connections)")
        else:
            _engine = create_async_engine(
                _db_url,
                echo=True,
                future=True,
                poolclass=NullPool,  # NullPool since using Supabase session pooler
                connect_args=connect_args,
            )
            logger.info("Database engine created with NullPool")
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
async def get_session(organization_id: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async database session with Row-Level Security (RLS) context.
    
    IMPORTANT: organization_id SHOULD be provided to ensure RLS is enforced.
    Calling without it will log a warning. For system-level operations that 
    legitimately need to bypass RLS, use get_admin_session() instead.
    
    How it works:
    - Session checks out a connection from the pool
    - Sets role to non-superuser (revtops_app) that respects RLS
    - If organization_id provided, sets RLS context (app.current_org_id)
    - All queries are automatically filtered to this organization
    - When session closes, connection returns to pool (NOT destroyed)
    
    Args:
        organization_id: Organization ID for RLS context. Should always be provided
                        for tenant-scoped operations. If None, a warning is logged.
    
    Usage:
        async with get_session(organization_id="...") as session:
            result = await session.execute(query)
            await session.commit()  # Explicit commit if needed
    
    The session is automatically closed when the context exits.
    Any uncommitted changes are rolled back on error.
    
    SECURITY: We connect as postgres superuser but immediately SET ROLE to
    revtops_app, which respects RLS policies. This is required because
    superusers bypass RLS entirely.
    """
    import traceback
    from sqlalchemy import text
    
    if not organization_id:
        # Log warning with stack trace to help identify missing org_id calls
        stack = ''.join(traceback.format_stack()[-5:-1])
        logger.warning(
            "get_session() called without organization_id - RLS context not set!\n"
            "Use get_admin_session() for system operations or pass organization_id.\n"
            "Call stack:\n%s", stack
        )
    
    factory = get_session_factory()
    session: AsyncSession = factory()
    try:
        # CRITICAL: Switch to non-superuser role that respects RLS
        # The postgres superuser bypasses RLS entirely, so we must switch roles
        await session.execute(text("SET ROLE revtops_app"))
        
        # Set RLS context if organization_id provided
        if organization_id:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, false)"),
                {"org_id": str(organization_id)}
            )
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        # Reset role before returning connection to pool
        try:
            await session.execute(text("RESET ROLE"))
        except Exception:
            pass  # Connection might already be closed
        # This returns the connection to the pool, doesn't close it
        await session.close()


@asynccontextmanager
async def get_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an admin database session that BYPASSES RLS.
    
    WARNING: Use sparingly! This should only be used for:
    - System-level scheduled tasks that iterate across all organizations
    - Initial lookups where org_id is not yet known (then use get_session for subsequent ops)
    - Database migrations and maintenance
    
    This session keeps the superuser role (postgres) which bypasses RLS entirely.
    All tables are accessible without organization filtering.
    
    Usage:
        async with get_admin_session() as session:
            # Query across all organizations
            result = await session.execute(query)
    """
    factory = get_session_factory()
    session: AsyncSession = factory()
    try:
        # Keep superuser role - bypasses RLS for cross-org system operations
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
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
    
    Note: Using NullPool - pgbouncer handles actual connection pooling.
    """
    return {
        "pool_type": "NullPool",
        "pool_size": 0,
        "checked_in": 0,
        "checked_out": 0,
        "overflow": 0,
    }


# Create engine and factory on module load for backwards compatibility
# Code should prefer get_session() context manager
engine = get_engine()
AsyncSessionLocal = get_session_factory()
