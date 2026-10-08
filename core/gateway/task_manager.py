"""
In-memory task lifecycle manager for the ORBIS FastAPI Gateway (Phase 12.8).
Coordinates background execution, status tracking, bounded task history,
and approval routing via the existing AgentOrchestrator.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
from typing import Any, Deque, Dict, Optional, Set, Tuple
from uuid import uuid4

from core.agent.models import AgentExecutionResult, TaskState
from core.agent.orchestrator import AgentOrchestrator
from core.events.sanitizer import EventSanitizer
from core.types import ExecutionContext

logger = logging.getLogger("orbis.gateway.task_manager")


class ManagedTask:
    """Represents a submitted task within the gateway lifecycle."""

    def __init__(
        self,
        task_id: str,
        correlation_id: str,
        prompt: str,
        status: str = "ACCEPTED",
    ) -> None:
        self.task_id = task_id
        self.correlation_id = correlation_id
        self.prompt = prompt
        self.status = status
        self.summary: Optional[str] = None
        self.answer: Optional[str] = None
        self.error: Optional[str] = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.result: Optional[AgentExecutionResult] = None
        self.asyncio_task: Optional[asyncio.Task] = None


class TaskManager:
    """
    Manages task submission, background task lifecycle, status tracking,
    and safe approval routing for the FastAPI gateway.
    Enforces bounded memory and prevents orphaned background tasks.
    """

    DEFAULT_MAX_TASKS: int = 200

    def __init__(self, max_tasks: int = DEFAULT_MAX_TASKS) -> None:
        self.max_tasks = max_tasks
        self._tasks: Dict[str, ManagedTask] = {}
        self._task_order: Deque[str] = deque(maxlen=max_tasks)
        self._active_asyncio_tasks: Set[asyncio.Task] = set()

    def get_task(self, task_id: str) -> Optional[ManagedTask]:
        """Retrieves a managed task by its ID."""
        return self._tasks.get(task_id)

    async def submit_task(
        self,
        prompt: str,
        orchestrator: AgentOrchestrator,
        correlation_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        sync: bool = False,
    ) -> ManagedTask:
        """
        Submits a new task to the orchestrator.
        If sync=False (default), schedules execution on the event loop in the background
        and returns immediately with status 'ACCEPTED'.
        If sync=True, waits for task completion before returning.
        """
        task_id = f"task_{uuid4().hex[:8]}"
        resolved_corr_id = correlation_id or f"corr_{task_id}"

        # Enforce bounded retention: evict oldest task if at capacity
        if len(self._tasks) >= self.max_tasks and self._task_order:
            oldest_id = self._task_order.popleft()
            self._tasks.pop(oldest_id, None)

        managed_task = ManagedTask(
            task_id=task_id,
            correlation_id=resolved_corr_id,
            prompt=prompt,
            status="ACCEPTED",
        )
        self._tasks[task_id] = managed_task
        self._task_order.append(task_id)

        ctx_meta = {
            "correlation_id": resolved_corr_id,
            "request_timestamp": managed_task.created_at.isoformat(),
        }
        if conversation_id:
            ctx_meta["conversation_id"] = conversation_id
        if metadata:
            ctx_meta.update(metadata)
            if not conversation_id and "conversation_id" in metadata:
                ctx_meta["conversation_id"] = metadata["conversation_id"]
        exec_ctx = ExecutionContext(task_id=task_id, metadata=ctx_meta)

        if sync:
            await self._run_task_coroutine(managed_task, orchestrator, exec_ctx)
        else:
            loop = asyncio.get_running_loop()
            bg_task = loop.create_task(
                self._run_task_coroutine(managed_task, orchestrator, exec_ctx),
                name=f"orbis_task_{task_id}",
            )
            managed_task.asyncio_task = bg_task
            self._active_asyncio_tasks.add(bg_task)
            bg_task.add_done_callback(self._active_asyncio_tasks.discard)

        return managed_task

    async def _run_task_coroutine(
        self,
        task: ManagedTask,
        orchestrator: AgentOrchestrator,
        exec_ctx: ExecutionContext,
    ) -> None:
        """Executes the task via AgentOrchestrator and updates managed state safely."""
        task.status = "RUNNING"
        task.updated_at = datetime.now(timezone.utc)

        try:
            res: AgentExecutionResult = await orchestrator.run_task(
                user_request=task.prompt,
                context=exec_ctx,
            )
            task.result = res
            raw_summary = getattr(res, "summary", None)
            task.summary = EventSanitizer.clean_message(raw_summary) if raw_summary else None

            from core.voice.service import extract_spoken_response
            extracted_ans = extract_spoken_response(res)
            if extracted_ans and extracted_ans != "Task completed successfully.":
                task.answer = EventSanitizer.clean_message(extracted_ans)
            elif task.summary:
                task.answer = task.summary

            res_state = getattr(res, "status", None)
            if res_state == TaskState.BLOCKED_PENDING_APPROVAL:
                task.status = "BLOCKED_PENDING_APPROVAL"
            elif res_state == TaskState.COMPLETED:
                task.status = "COMPLETED"
            elif res_state == TaskState.CANCELLED:
                task.status = "CANCELLED"
            else:
                task.status = "FAILED"
                raw_err = getattr(res, "error", None) or raw_summary or "Task execution failed."
                task.error = EventSanitizer.clean_message(raw_err)

        except asyncio.CancelledError:
            task.status = "CANCELLED"
            task.summary = "Task execution was cancelled."
            raise
        except Exception as e:
            logger.exception("Unexpected error in background task execution %s: %s", task.task_id, e)
            task.status = "FAILED"
            task.error = "An error occurred during task execution."
        finally:
            task.updated_at = datetime.now(timezone.utc)

    async def handle_approval(
        self,
        task_id: str,
        approved: bool,
        orchestrator: AgentOrchestrator,
        reason: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Processes human approval or rejection for a pending task.
        Reuses existing AgentOrchestrator pending approval mechanisms and guarantees
        identical safety invariants and event telemetry.
        Returns (status, message).
        """
        pending = orchestrator._pending_approval
        if pending is None or pending.task_id != task_id:
            raise ValueError(f"Task '{task_id}' has no pending approval.")

        now = datetime.now(timezone.utc)
        if now > pending.expires_at:
            orchestrator._pending_approval = None
            managed = self.get_task(task_id)
            if managed:
                managed.status = "FAILED"
                managed.error = "Approval request expired."
                managed.updated_at = now
            raise ValueError(f"Pending approval for task '{task_id}' has expired.")

        managed = self.get_task(task_id)
        corr_id = managed.correlation_id if managed else f"corr_{task_id}"
        ctx = ExecutionContext(task_id=task_id, metadata={"correlation_id": corr_id})

        if approved:
            # Affirmative confirmation triggers resumption of blocked step
            res = await orchestrator.run_task(user_request="yes", context=ctx)
            if managed:
                managed.result = res
                managed.updated_at = datetime.now(timezone.utc)
                res_state = getattr(res, "status", None)
                if res_state == TaskState.COMPLETED:
                    managed.status = "COMPLETED"
                    managed.summary = EventSanitizer.clean_message(getattr(res, "summary", None))
                elif res_state == TaskState.BLOCKED_PENDING_APPROVAL:
                    managed.status = "BLOCKED_PENDING_APPROVAL"
                    managed.summary = EventSanitizer.clean_message(getattr(res, "summary", None))
                else:
                    managed.status = "FAILED"
                    managed.error = EventSanitizer.clean_message(getattr(res, "error", None) or "Resumed execution failed.")
            return "RUNNING", "Approval granted; task execution resumed."
        else:
            # Negative confirmation cancels the pending action safely
            res = await orchestrator.run_task(user_request="no", context=ctx)
            if managed:
                managed.result = res
                managed.status = "CANCELLED"
                managed.summary = EventSanitizer.clean_message(getattr(res, "summary", "Action cancelled by user."))
                managed.updated_at = datetime.now(timezone.utc)
            return "CANCELLED", "Approval denied; task execution cancelled."

    async def close(self, timeout: float = 2.0) -> None:
        """Gracefully cancels all active background tasks on gateway shutdown."""
        if not self._active_asyncio_tasks:
            return

        tasks = list(self._active_asyncio_tasks)
        for t in tasks:
            if not t.done():
                t.cancel()

        try:
            await asyncio.wait(tasks, timeout=timeout)
        except Exception:
            pass
        self._active_asyncio_tasks.clear()
