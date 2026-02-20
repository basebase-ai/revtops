"""
Event system for workflow triggers.

Events are stored in Redis and processed by the workflow task.
This allows workflows to be triggered by events like:
- sync.completed
- sync.failed
- deal.created
- call.recorded
- linear.issue.done (Linear issue moved to Done)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as redis

from config import get_redis_connection_kwargs, settings

logger = logging.getLogger(__name__)

# Redis key prefixes
EVENT_QUEUE_KEY = "revtops:events:queue"
EVENT_HISTORY_KEY = "revtops:events:history:{org_id}"


async def get_redis_client() -> redis.Redis:
    """Get an async Redis client."""
    return redis.from_url(
        settings.REDIS_URL, **get_redis_connection_kwargs(decode_responses=True)
    )


async def emit_event(
    event_type: str,
    organization_id: str,
    data: dict[str, Any],
) -> str:
    """
    Emit an event that can trigger workflows.
    
    Args:
        event_type: Type of event (e.g., 'sync.completed', 'deal.created')
        organization_id: UUID of the organization
        data: Event payload data
    
    Returns:
        Event ID
    """
    event_id = str(uuid4())
    event = {
        "id": event_id,
        "type": event_type,
        "organization_id": organization_id,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    }
    
    try:
        client = await get_redis_client()
        
        # Add to event queue for processing
        await client.rpush(EVENT_QUEUE_KEY, json.dumps(event))
        
        # Also store in history (with TTL)
        history_key = EVENT_HISTORY_KEY.format(org_id=organization_id)
        await client.rpush(history_key, json.dumps(event))
        await client.expire(history_key, 60 * 60 * 24 * 7)  # 7 days
        
        logger.info(f"Emitted event {event_type} for org {organization_id}: {event_id}")
        await client.aclose()
        
    except Exception as e:
        logger.error(f"Failed to emit event: {e}")
        # Don't fail the calling operation if event emission fails
    
    return event_id


async def get_pending_events(limit: int = 100) -> list[dict[str, Any]]:
    """
    Get pending events from the queue.
    
    Args:
        limit: Maximum number of events to retrieve
    
    Returns:
        List of event dictionaries
    """
    try:
        client = await get_redis_client()
        
        events: list[dict[str, Any]] = []
        for _ in range(limit):
            event_json = await client.lpop(EVENT_QUEUE_KEY)
            if not event_json:
                break
            events.append(json.loads(event_json))
        
        await client.aclose()
        return events
        
    except Exception as e:
        logger.error(f"Failed to get pending events: {e}")
        return []


async def get_event_history(
    organization_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """
    Get recent event history for an organization.
    
    Args:
        organization_id: UUID of the organization
        limit: Maximum number of events to retrieve
    
    Returns:
        List of event dictionaries (most recent first)
    """
    try:
        client = await get_redis_client()
        history_key = EVENT_HISTORY_KEY.format(org_id=organization_id)
        
        # Get last N events (most recent last in list)
        events_json = await client.lrange(history_key, -limit, -1)
        events = [json.loads(e) for e in events_json]
        
        await client.aclose()
        return list(reversed(events))  # Most recent first
        
    except Exception as e:
        logger.error(f"Failed to get event history: {e}")
        return []
