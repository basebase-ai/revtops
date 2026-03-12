"""
Shared async-to-sync bridge for all Celery task modules.

Every task module MUST use the single `run_async` from here instead of
maintaining its own `_worker_loop` global.  Having multiple event loops
in the same worker process causes asyncpg connections created on one loop
to be used by another, producing:

    RuntimeError: ... got Future ... attached to a different loop

A single loop per worker process guarantees the SQLAlchemy engine's
connection pool only ever sees one event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_worker_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously inside a Celery worker process.

    Reuses a single event loop per worker process so that asyncpg connections
    remain valid across task invocations.
    """
    global _worker_loop

    if _worker_loop is None or _worker_loop.is_closed():
        from models.database import dispose_engine

        dispose_engine()
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
        logger.debug("Created new worker event loop id=%s", id(_worker_loop))

    return _worker_loop.run_until_complete(coro)


def reset_worker_loop() -> None:
    """Reset the shared event loop after fork.

    Called from the ``worker_process_init`` signal handler so the first
    task in a freshly-forked process creates a brand-new loop.
    """
    global _worker_loop
    _worker_loop = None
