"""
Celery application configuration.

This configures Celery with Redis as the broker and result backend.
Beat schedule is defined here for periodic tasks.
"""
from __future__ import annotations

import os
import sys
import logging
from datetime import timedelta
from pathlib import Path

# Ensure backend directory is in Python path for Celery workers
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# CRITICAL: Load .env BEFORE importing config/settings
# This ensures Celery workers use the same DATABASE_URL as the API server
from dotenv import load_dotenv
env_file = backend_dir / ".env"
if not env_file.exists():
    env_file = backend_dir.parent / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"[Celery] Loaded environment from: {env_file}")

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown


class _WorkerLogFormatter(logging.Formatter):
    """Align Celery worker logs with API pipe-delimited format."""

    def format(self, record: logging.LogRecord) -> str:
        record.levelname = record.levelname.lower()
        return super().format(record)


def _configure_worker_logging(log_level: int = logging.INFO) -> None:
    """Configure root logger format so worker/beat logs match API style."""
    formatter = _WorkerLogFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    logging.captureWarnings(True)


@setup_logging.connect
def _on_celery_setup_logging(loglevel=None, **kwargs) -> None:
    """Override Celery defaults so workers emit consistent structured text logs."""
    if isinstance(loglevel, int):
        level = loglevel
    else:
        level = logging.getLevelName(str(loglevel).upper()) if loglevel else logging.INFO
        if not isinstance(level, int):
            level = logging.INFO
    _configure_worker_logging(level)

# Get Redis URL from environment
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Create Celery app
celery_app = Celery(
    "revtops",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "workers.tasks.sync",
        "workers.tasks.workflows",
        "workers.tasks.bulk_operations",
        "workers.tasks.monitoring",
    ],
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # Timezone
    timezone="UTC",
    enable_utc=True,
    
    # Task settings
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max per task
    task_soft_time_limit=25 * 60,  # Soft limit at 25 minutes
    
    # Result settings
    result_expires=60 * 60 * 24,  # Results expire after 24 hours
    
    # Worker settings
    # Keep concurrency low to limit database connections
    # Each worker process creates its own connection pool
    worker_prefetch_multiplier=1,  # One task at a time per worker
    worker_concurrency=4,  # 4 concurrent tasks per worker
    
    # Queue configuration
    # All tasks go to the default queue. Separate queues add operational
    # complexity (forgetting -Q enrichment) with no benefit when running a
    # single worker instance. If you later need independent scaling, re-add
    # queues and route tasks explicitly.
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
)

# Beat schedule for periodic tasks.
# Only enabled when ENABLE_CELERY_BEAT=true (set in production only) so that
# local dev servers never accidentally run hourly syncs against shared APIs.
_ENABLE_BEAT: bool = os.environ.get("ENABLE_CELERY_BEAT", "").lower() in ("true", "1", "yes")

if _ENABLE_BEAT:
    celery_app.conf.beat_schedule = {
        "hourly-sync-all-organizations": {
            "task": "workers.tasks.sync.sync_all_organizations",
            "schedule": crontab(minute=0),
        },
        "check-scheduled-workflows": {
            "task": "workers.tasks.workflows.check_scheduled_workflows",
            "schedule": timedelta(minutes=1),
        },
        "process-workflow-events": {
            "task": "workers.tasks.workflows.process_pending_events",
            "schedule": timedelta(seconds=10),
        },
        "monitor-critical-dependencies": {
            "task": "workers.tasks.monitoring.monitor_dependencies",
            "schedule": timedelta(minutes=15),
        },
        "sweep-active-huddles": {
            "task": "workers.tasks.sync.sweep_active_huddles",
            "schedule": timedelta(minutes=5),
        },
        "sweep-completed-meetings": {
            "task": "workers.tasks.sync.sweep_completed_meetings",
            "schedule": timedelta(minutes=5),
        },
        "monitoring-heartbeat-watchdog": {
            "task": "workers.tasks.monitoring.monitoring_heartbeat_watchdog",
            "schedule": timedelta(minutes=5),
        },
    }
else:
    celery_app.conf.beat_schedule = {}
    logging.getLogger(__name__).info(
        "Celery beat schedule DISABLED (set ENABLE_CELERY_BEAT=true to enable)"
    )


@worker_process_init.connect
def setup_backend_path(**kwargs) -> None:
    """Ensure backend/ is on sys.path and reset async state in every forked worker.

    After fork, event loops and connection pools from the parent are invalid.
    Reset sync task state and dispose DB engine so the first task gets fresh
    resources bound to its loop (avoids 'Future attached to different loop').
    """
    _dir = str(Path(__file__).resolve().parent.parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

    # Reset async state so first task creates fresh loop/engine (no stale refs from parent)
    try:
        from models.database import dispose_engine
        dispose_engine()
    except Exception:
        pass

    try:
        from workers.run_async import reset_worker_loop
        reset_worker_loop()
    except Exception:
        pass


@worker_process_shutdown.connect
def cleanup_db_connections(**kwargs) -> None:
    """Clean up database connections when a Celery worker process shuts down.
    
    This ensures connections are properly released back to Supabase's pool
    when worker processes exit (during shutdown or restarts).
    """
    try:
        from models.database import dispose_engine
        dispose_engine()
        print("[Celery] Database connections cleaned up on worker shutdown")
    except Exception as e:
        print(f"[Celery] Error cleaning up database connections: {e}")
