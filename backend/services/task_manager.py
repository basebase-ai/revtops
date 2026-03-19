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
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlalchemy import and_, or_, select, update

from agents.orchestrator import ChatOrchestrator
from models.agent_task import AgentTask
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_admin_session, get_session

logger = logging.getLogger(__name__)


FANOUT_SEND_TIMEOUT_SECONDS = 1.5


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

        # Per-conversation execution locks so turns are processed in arrival order.
        # This prevents overlapping model runs from interleaving context when
        # multiple speakers/sources send messages to the same conversation quickly.
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._conversation_lock_refs: dict[str, int] = {}
        self._conversation_manager_lock = asyncio.Lock()

        # Stale task reaper - marks DB rows stuck in "running" as failed
        self._reaper_task: asyncio.Task[None] | None = None
        self._reaper_stale_minutes: int = 10
        self._reaper_interval_seconds: int = 60
        
        self._initialized = True
        logger.info("TaskManager initialized")

    @asynccontextmanager
    async def _conversation_execution_lock(self, conversation_id: str):
        """Serialize task execution per conversation while allowing fast enqueueing."""
        async with self._conversation_manager_lock:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._conversation_locks[conversation_id] = lock
                self._conversation_lock_refs[conversation_id] = 0
                logger.debug("Created conversation execution lock for %s", conversation_id)

            self._conversation_lock_refs[conversation_id] = (
                self._conversation_lock_refs.get(conversation_id, 0) + 1
            )
            queued_count: int = self._conversation_lock_refs[conversation_id]

        logger.info(
            "Task queued for conversation=%s queued_count=%d",
            conversation_id,
            queued_count,
        )
        await lock.acquire()
        logger.info("Task execution started for conversation=%s", conversation_id)

        try:
            yield
        finally:
            lock.release()
            logger.info("Task execution released for conversation=%s", conversation_id)
            async with self._conversation_manager_lock:
                remaining: int = max(
                    self._conversation_lock_refs.get(conversation_id, 1) - 1,
                    0,
                )
                if remaining == 0:
                    self._conversation_lock_refs.pop(conversation_id, None)
                    self._conversation_locks.pop(conversation_id, None)
                    logger.debug("Removed idle conversation lock for %s", conversation_id)
                else:
                    self._conversation_lock_refs[conversation_id] = remaining
    
    async def start_task(
        self,
        conversation_id: str,
        user_id: str,
        organization_id: str,
        user_message: str,
        user_email: str | None = None,
        local_time: str | None = None,
        timezone: str | None = None,
        is_new_conversation: bool = False,
        attachment_ids: list[str] | None = None,
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
            is_new_conversation: If True, skip history loading (no messages exist yet)
            attachment_ids: Optional list of upload IDs for attached files
            
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
                is_new_conversation=is_new_conversation,
                attachment_ids=attachment_ids,
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
        is_new_conversation: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> None:
        """
        Execute the agent task in the background.
        
        Streams output from the orchestrator, persists chunks to database,
        and broadcasts to subscribed WebSockets.
        """
        try:
            async with self._conversation_execution_lock(conversation_id):
                orchestrator = ChatOrchestrator(
                    user_id=user_id,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    user_email=user_email,
                    local_time=local_time,
                    timezone=timezone,
                )

                chunk_index = 0

                async for chunk in orchestrator.process_message(
                    user_message,
                    skip_history=is_new_conversation,
                    attachment_ids=attachment_ids,
                ):
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

                    # Broadcast FIRST — get the chunk to the UI as fast as possible
                    await self._broadcast(task_id, {
                        "type": "task_chunk",
                        "task_id": task_id,
                        "conversation_id": conversation_id,
                        "chunk": chunk_data,
                    })

                    # Persist important events to database in the background (fire-and-forget).
                    # Text deltas are ephemeral — the full message is saved at the end by orchestrator.
                    # DB persistence is for catchup on reconnect, not the critical display path.
                    if chunk_data["type"] != "text_delta":
                        asyncio.create_task(self._append_chunk_safe(task_id, chunk_data))

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

            # Broadcast final assistant message to other participants in shared conversations
            asyncio.create_task(
                self._broadcast_assistant_message_to_participants(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    exclude_user_id=user_id,
                )
            )

            # Fire-and-forget: generate conversation summary in background
            asyncio.create_task(
                self._generate_and_broadcast_summary(conversation_id, organization_id)
            )

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
    
    async def _append_chunk_safe(self, task_id: str, chunk: dict[str, Any]) -> None:
        """Fire-and-forget wrapper for _append_chunk that logs errors instead of raising."""
        try:
            await self._append_chunk(task_id, chunk)
        except Exception as e:
            logger.warning("Background chunk persist failed for task %s: %s", task_id, e)

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

    async def _broadcast_assistant_message_to_participants(
        self,
        conversation_id: str,
        organization_id: str,
        exclude_user_id: str,
    ) -> None:
        """Broadcast the latest assistant message to other participants in shared conversations."""
        try:
            from api.websockets import broadcast_conversation_message

            async with get_session(organization_id=organization_id) as session:
                conv_row = await session.execute(
                    select(Conversation.scope, Conversation.participating_user_ids).where(
                        Conversation.id == UUID(conversation_id)
                    )
                )
                row = conv_row.one_or_none()
            if not row or row[0] != "shared" or not row[1]:
                return
            scope: str = row[0]
            participant_ids: list[str] = [str(uid) for uid in row[1]]

            async with get_session(organization_id=organization_id) as session:
                msg_result = await session.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == UUID(conversation_id),
                        ChatMessage.role == "assistant",
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                latest: ChatMessage | None = msg_result.scalars().one_or_none()
            if not latest:
                return
            await broadcast_conversation_message(
                conversation_id=conversation_id,
                scope=scope,
                participant_user_ids=participant_ids,
                message_data=latest.to_dict(),
                sender_user_id=exclude_user_id,
            )
        except Exception:
            logger.warning(
                "Broadcast assistant message to participants failed for conversation %s",
                conversation_id,
                exc_info=True,
            )

    async def _generate_and_broadcast_summary(
        self,
        conversation_id: str,
        organization_id: str,
    ) -> None:
        """Fire-and-forget: generate summary and broadcast via WebSocket."""
        try:
            from services.conversation_summary import generate_conversation_summary
            from api.websockets import sync_broadcaster

            summary = await generate_conversation_summary(conversation_id, organization_id)
            if summary:
                await sync_broadcaster.broadcast(
                    organization_id,
                    "summary_updated",
                    {
                        "conversation_id": conversation_id,
                        "summary": summary,
                    },
                )
        except Exception:
            logger.warning(
                "Background summary generation failed for conversation %s",
                conversation_id,
                exc_info=True,
            )

    async def _broadcast(self, task_id: str, message: dict[str, Any]) -> None:
        """Broadcast a message to all WebSockets subscribed to a task."""
        async with self._lock:
            subscribers = self._subscriptions.get(task_id, set()).copy()

        if not subscribers:
            return

        message_str = json.dumps(message)
        send_tasks = [
            asyncio.create_task(
                asyncio.wait_for(
                    ws.send_text(message_str),
                    timeout=FANOUT_SEND_TIMEOUT_SECONDS,
                )
            )
            for ws in subscribers
        ]
        results = await asyncio.gather(*send_tasks, return_exceptions=True)
        dead_sockets = [
            ws
            for ws, result in zip(subscribers, results, strict=False)
            if isinstance(result, Exception)
        ]

        # Clean up dead sockets
        if dead_sockets:
            logger.debug(
                "Removed %s stale websocket subscription(s) for task %s",
                len(dead_sockets),
                task_id,
            )
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
                .join(Conversation, AgentTask.conversation_id == Conversation.id)
                .where(AgentTask.status == "running")
                .where(
                    or_(
                        AgentTask.user_id == UUID(user_id),
                        and_(
                            Conversation.scope == "shared",
                            Conversation.participating_user_ids.contains([UUID(user_id)]),
                        ),
                    )
                )
                .order_by(AgentTask.started_at.desc())
            )
            tasks = result.scalars().unique().all()
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

    async def _reap_stale_tasks(self) -> None:
        """Mark DB tasks stuck in 'running' with no recent activity as failed."""
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=self._reaper_stale_minutes)
            async with get_admin_session() as session:
                result = await session.execute(
                    select(AgentTask)
                    .where(AgentTask.status == "running")
                    .where(AgentTask.last_activity_at < cutoff)
                )
                stale = result.scalars().all()
                for task in stale:
                    task_id_str = str(task.id)
                    error_msg = f"Task timed out (no activity for {self._reaper_stale_minutes} minutes)"
                    await session.execute(
                        update(AgentTask)
                        .where(AgentTask.id == task.id)
                        .values(
                            status="failed",
                            completed_at=datetime.utcnow(),
                            last_activity_at=datetime.utcnow(),
                            error_message=error_msg,
                        )
                    )
                    logger.warning(
                        "Reaped stale task %s (conversation %s)",
                        task_id_str,
                        task.conversation_id,
                    )
                    asyncio_task = self._running_tasks.get(task_id_str)
                    if asyncio_task and not asyncio_task.done():
                        asyncio_task.cancel()
                    self._running_tasks.pop(task_id_str, None)
                    self._task_org_ids.pop(task_id_str, None)
                    await self._broadcast(
                        task_id_str,
                        {
                            "type": "task_complete",
                            "task_id": task_id_str,
                            "conversation_id": str(task.conversation_id),
                            "status": "failed",
                            "error": error_msg,
                        },
                    )
                if stale:
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Stale task reaper failed: %s", exc)

    def _start_reaper(self) -> None:
        """Start the background stale task reaper."""
        if self._reaper_task is not None:
            return

        async def _run_reaper() -> None:
            while True:
                try:
                    await asyncio.sleep(self._reaper_interval_seconds)
                    await self._reap_stale_tasks()
                except asyncio.CancelledError:
                    break

        self._reaper_task = asyncio.create_task(_run_reaper())
        logger.info(
            "Stale task reaper started (interval=%ds, stale=%dm)",
            self._reaper_interval_seconds,
            self._reaper_stale_minutes,
        )

    def _stop_reaper(self) -> None:
        """Stop the background stale task reaper."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            self._reaper_task = None
            logger.info("Stale task reaper stopped")


# Global singleton instance
task_manager = TaskManager()
