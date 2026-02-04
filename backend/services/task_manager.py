"""
Task Manager for background agent execution.

Manages agent tasks that run independently of WebSocket connections:
- Spawns background asyncio tasks
- Persists task state and output to database
- Broadcasts updates to subscribed WebSockets
- Supports catchup for reconnecting clients
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlalchemy import select, update

from agents.orchestrator import ChatOrchestrator
from models.agent_task import AgentTask
from models.database import get_session

logger = logging.getLogger(__name__)


# Type alias for broadcast callback
BroadcastCallback = Callable[[str], Coroutine[Any, Any, None]]


class TaskManager:
    """
    Manages background agent tasks with WebSocket subscriptions.
    
    Singleton pattern - one instance manages all tasks across the application.
    """
    
    _instance: "TaskManager | None" = None
    
    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        
        # Active asyncio tasks by task_id
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        
        # Organization ID per task (for RLS context)
        self._task_org_ids: dict[str, str] = {}
        
        # WebSocket subscriptions: task_id -> set of websockets
        self._subscriptions: dict[str, set[WebSocket]] = {}
        
        # Lock for thread-safe subscription management
        self._lock = asyncio.Lock()
        
        self._initialized = True
        logger.info("TaskManager initialized")
    
    async def start_task(
        self,
        conversation_id: str,
        user_id: str,
        organization_id: str,
        user_message: str,
        user_email: str | None = None,
        local_time: str | None = None,
        timezone: str | None = None,
    ) -> str:
        """
        Start a new background agent task.
        
        Creates a database record and spawns an asyncio task that runs
        independently of any WebSocket connection.
        
        Args:
            conversation_id: UUID of the conversation
            user_id: UUID of the user
            organization_id: UUID of the organization
            user_message: The user's message to process
            user_email: User's email address
            local_time: User's local time (ISO format)
            timezone: User's timezone
            
        Returns:
            The task_id (UUID string)
        """
        task_id = str(uuid4())
        
        # Create database record (with RLS context)
        async with get_session(organization_id=organization_id) as session:
            agent_task = AgentTask(
                id=UUID(task_id),
                conversation_id=UUID(conversation_id),
                user_id=UUID(user_id),
                organization_id=UUID(organization_id),
                user_message=user_message,
                status="running",
                output_chunks=[],
            )
            session.add(agent_task)
            await session.commit()
        
        logger.info(
            "Starting task %s for conversation %s",
            task_id, conversation_id
        )
        
        # Spawn background task
        asyncio_task = asyncio.create_task(
            self._run_task(
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                organization_id=organization_id,
                user_message=user_message,
                user_email=user_email,
                local_time=local_time,
                timezone=timezone,
            )
        )
        
        self._running_tasks[task_id] = asyncio_task
        self._task_org_ids[task_id] = organization_id
        
        return task_id
    
    async def _run_task(
        self,
        task_id: str,
        conversation_id: str,
        user_id: str,
        organization_id: str,
        user_message: str,
        user_email: str | None,
        local_time: str | None,
        timezone: str | None,
    ) -> None:
        """
        Execute the agent task in the background.
        
        Streams output from the orchestrator, persists chunks to database,
        and broadcasts to subscribed WebSockets.
        """
        try:
            orchestrator = ChatOrchestrator(
                user_id=user_id,
                organization_id=organization_id,
                conversation_id=conversation_id,
                user_email=user_email,
                local_time=local_time,
                timezone=timezone,
            )
            
            chunk_index = 0
            
            async for chunk in orchestrator.process_message(user_message):
                # Create chunk record
                chunk_data: dict[str, Any] = {
                    "index": chunk_index,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                
                # Parse chunk - could be JSON (tool call/result) or plain text
                try:
                    parsed = json.loads(chunk)
                    # Only treat as structured data if it's a dict with a type field
                    if isinstance(parsed, dict):
                        chunk_data["type"] = parsed.get("type", "json")
                        chunk_data["data"] = parsed
                    else:
                        # JSON parsed but not a dict (e.g., number, string, list)
                        chunk_data["type"] = "text_delta"
                        chunk_data["data"] = chunk
                except json.JSONDecodeError:
                    chunk_data["type"] = "text_delta"
                    chunk_data["data"] = chunk
                
                # Only persist important events to database (tool calls/results)
                # Text deltas are ephemeral - the full message is saved at the end by orchestrator
                if chunk_data["type"] != "text_delta":
                    await self._append_chunk(task_id, chunk_data)
                
                # Broadcast ALL chunks to subscribers (including text deltas for live streaming)
                await self._broadcast(task_id, {
                    "type": "task_chunk",
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "chunk": chunk_data,
                })
                
                chunk_index += 1
            
            # Mark task as completed
            await self._complete_task(task_id, "completed")
            
            # Broadcast completion
            await self._broadcast(task_id, {
                "type": "task_complete",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "completed",
            })
            
            logger.info("Task %s completed successfully", task_id)
            
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled", task_id)
            await self._complete_task(task_id, "cancelled")
            await self._broadcast(task_id, {
                "type": "task_complete",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "cancelled",
            })
            raise
            
        except Exception as e:
            logger.exception("Task %s failed with error: %s", task_id, e)
            await self._complete_task(task_id, "failed", str(e))
            await self._broadcast(task_id, {
                "type": "task_complete",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "failed",
                "error": str(e),
            })
            
        finally:
            # Clean up
            self._running_tasks.pop(task_id, None)
            self._task_org_ids.pop(task_id, None)
    
    async def _append_chunk(self, task_id: str, chunk: dict[str, Any]) -> None:
        """Append a chunk to the task's output_chunks in the database."""
        org_id = self._task_org_ids.get(task_id)
        async with get_session(organization_id=org_id) as session:
            # Use raw SQL for atomic append to JSONB array
            await session.execute(
                update(AgentTask)
                .where(AgentTask.id == UUID(task_id))
                .values(
                    output_chunks=AgentTask.output_chunks + [chunk],
                    last_activity_at=datetime.utcnow(),
                )
            )
            await session.commit()
    
    async def _complete_task(
        self,
        task_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Mark a task as completed/failed/cancelled in the database."""
        org_id = self._task_org_ids.get(task_id)
        async with get_session(organization_id=org_id) as session:
            values: dict[str, Any] = {
                "status": status,
                "completed_at": datetime.utcnow(),
                "last_activity_at": datetime.utcnow(),
            }
            if error_message:
                values["error_message"] = error_message
            
            await session.execute(
                update(AgentTask)
                .where(AgentTask.id == UUID(task_id))
                .values(**values)
            )
            await session.commit()
    
    async def _broadcast(self, task_id: str, message: dict[str, Any]) -> None:
        """Broadcast a message to all WebSockets subscribed to a task."""
        async with self._lock:
            subscribers = self._subscriptions.get(task_id, set()).copy()
        
        if not subscribers:
            return
        
        message_str = json.dumps(message)
        dead_sockets: list[WebSocket] = []
        
        for ws in subscribers:
            try:
                await ws.send_text(message_str)
            except Exception:
                # WebSocket is dead, mark for removal
                dead_sockets.append(ws)
        
        # Clean up dead sockets
        if dead_sockets:
            async with self._lock:
                for ws in dead_sockets:
                    self._subscriptions.get(task_id, set()).discard(ws)
    
    async def subscribe(self, task_id: str, websocket: WebSocket) -> None:
        """
        Subscribe a WebSocket to receive updates for a task.
        
        Args:
            task_id: The task to subscribe to
            websocket: The WebSocket connection
        """
        async with self._lock:
            if task_id not in self._subscriptions:
                self._subscriptions[task_id] = set()
            self._subscriptions[task_id].add(websocket)
        
        logger.debug("WebSocket subscribed to task %s", task_id)
    
    async def unsubscribe(self, task_id: str, websocket: WebSocket) -> None:
        """
        Unsubscribe a WebSocket from a task.
        
        Args:
            task_id: The task to unsubscribe from
            websocket: The WebSocket connection
        """
        async with self._lock:
            if task_id in self._subscriptions:
                self._subscriptions[task_id].discard(websocket)
                if not self._subscriptions[task_id]:
                    del self._subscriptions[task_id]
        
        logger.debug("WebSocket unsubscribed from task %s", task_id)
    
    async def unsubscribe_all(self, websocket: WebSocket) -> None:
        """
        Unsubscribe a WebSocket from all tasks.
        
        Called when a WebSocket disconnects.
        """
        async with self._lock:
            for task_id in list(self._subscriptions.keys()):
                self._subscriptions[task_id].discard(websocket)
                if not self._subscriptions[task_id]:
                    del self._subscriptions[task_id]
    
    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task.
        
        Args:
            task_id: The task to cancel
            
        Returns:
            True if the task was cancelled, False if not found/already done
        """
        asyncio_task = self._running_tasks.get(task_id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()
            logger.info("Cancelled task %s", task_id)
            return True
        return False
    
    async def get_active_tasks(self, user_id: str, organization_id: str | None = None) -> list[dict[str, Any]]:
        """
        Get all active (running) tasks for a user.
        
        Args:
            user_id: UUID of the user
            organization_id: Optional org ID for RLS context
            
        Returns:
            List of task state dictionaries
        """
        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(AgentTask)
                .where(AgentTask.user_id == UUID(user_id))
                .where(AgentTask.status == "running")
                .order_by(AgentTask.started_at.desc())
            )
            tasks = result.scalars().all()
            return [task.to_state_dict() for task in tasks]
    
    async def get_task(self, task_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        """
        Get a task by ID including all output chunks.
        
        Args:
            task_id: The task ID
            organization_id: Optional org ID for RLS context (uses cached if not provided)
            
        Returns:
            Task state dictionary or None if not found
        """
        # Use cached org_id if not provided
        org_id = organization_id or self._task_org_ids.get(task_id)
        async with get_session(organization_id=org_id) as session:
            task = await session.get(AgentTask, UUID(task_id))
            if task:
                return task.to_state_dict()
            return None
    
    async def get_task_chunks(
        self,
        task_id: str,
        since_index: int = 0,
        organization_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get output chunks for a task since a given index.
        
        Used for catchup when a client reconnects.
        
        Args:
            task_id: The task ID
            since_index: Return chunks with index >= this value
            organization_id: Optional org ID for RLS context (uses cached if not provided)
            
        Returns:
            List of chunk dictionaries
        """
        # Use cached org_id if not provided
        org_id = organization_id or self._task_org_ids.get(task_id)
        async with get_session(organization_id=org_id) as session:
            task = await session.get(AgentTask, UUID(task_id))
            if not task or not task.output_chunks:
                return []
            
            return [
                chunk for chunk in task.output_chunks
                if chunk.get("index", 0) >= since_index
            ]
    
    def is_task_running(self, task_id: str) -> bool:
        """Check if a task is currently running in memory."""
        task = self._running_tasks.get(task_id)
        return task is not None and not task.done()


# Global singleton instance
task_manager = TaskManager()
