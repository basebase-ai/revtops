"""
Celery application configuration.

This configures Celery with Redis as the broker and result backend.
Beat schedule is defined here for periodic tasks.
"""
from __future__ import annotations

import os
import sys
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
from celery.signals import worker_process_shutdown

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

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    # Hourly sync for all organizations - runs at the top of every hour
    "hourly-sync-all-organizations": {
        "task": "workers.tasks.sync.sync_all_organizations",
        "schedule": crontab(minute=0),  # Every hour at :00
    },
    
    # Check for scheduled workflows every minute
    "check-scheduled-workflows": {
        "task": "workers.tasks.workflows.check_scheduled_workflows",
        "schedule": timedelta(minutes=1),
    },
    
    # Process event-triggered workflows (check queue every 10 seconds)
    "process-workflow-events": {
        "task": "workers.tasks.workflows.process_pending_events",
        "schedule": timedelta(seconds=10),
    },
}


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
