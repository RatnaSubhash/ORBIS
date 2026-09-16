"""
REST API route definitions for the ORBIS FastAPI Gateway (Phase 12.8).
Exposes task submission, status retrieval, bounded historical event queries,
and human approval / continuation endpoints.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.gateway.models import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorResponse,
    PendingApprovalDetails,
    TaskEventsResponse,
    TaskStatusResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
)
from core.gateway.task_manager import TaskManager

logger = logging.getLogger("orbis.gateway.routes")

router = APIRouter(prefix="/api", tags=["Agent Gateway"])

# Dependencies placeholders to be overridden in app creation
def get_orchestrator() -> AgentOrchestrator:
    raise NotImplementedError("Orchestrator dependency not configured.")


def get_task_manager() -> TaskManager:
    raise NotImplementedError("TaskManager dependency not configured.")


def get_event_bus() -> EventBus:
    raise NotImplementedError("EventBus dependency not configured.")


TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_task_id(task_id: str) -> str:
    """Validates task_id format to prevent injection or invalid lookups."""
    if not task_id or not TASK_ID_PATTERN.match(task_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format. Must contain only alphanumeric characters, underscores, and hyphens.",
        )
    return task_id


@router.get("/health", summary="Gateway Health Check")
async def health_check():
    """Returns gateway service health and version."""
    return {"status": "ok", "service": "orbis-gateway", "version": "0.2.0"}


@router.post(
    "/tasks",
    response_model=TaskSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Submit Agent Task",
)
async def submit_task(
    request: TaskSubmitRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Submits a user task to the existing AgentOrchestrator.
    By default, dispatches execution in the background and immediately returns
    HTTP 202 Accepted with task_id and correlation_id for real-time tracking.
    """
    try:
        task = await task_manager.submit_task(
            prompt=request.prompt,
            orchestrator=orchestrator,
            correlation_id=request.correlation_id,
            metadata=request.metadata,
            sync=request.sync,
        )
        http_code = status.HTTP_200_OK if request.sync else status.HTTP_202_ACCEPTED
        return TaskSubmitResponse(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            status=task.status,
            message="Task accepted for execution." if not request.sync else "Task execution completed.",
        )
    except Exception as e:
        logger.exception("Failed to submit task: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate task execution.",
        )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Get Task Status",
)
async def get_task_status(
    task_id: str,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
    event_bus: EventBus = Depends(get_event_bus),
):
    """
    Returns the user-safe operational status of an agent task.
    Strictly prevents leakage of chain-of-thought, system prompts, raw tool outputs,
    or internal secrets.
    """
    valid_id = validate_task_id(task_id)
    managed = task_manager.get_task(valid_id)

    # Check for live pending approval details from orchestrator
    pending_details: Optional[PendingApprovalDetails] = None
    if orchestrator._pending_approval and orchestrator._pending_approval.task_id == valid_id:
        p = orchestrator._pending_approval
        pending_details = PendingApprovalDetails(
            task_id=p.task_id,
            blocked_step_id=p.blocked_step_id,
            action_description=p.action_description,
            tool_name=p.tool_name,
            target_resource=p.target_resource,
            expires_at=p.expires_at,
        )

    if managed:
        return TaskStatusResponse(
            task_id=managed.task_id,
            correlation_id=managed.correlation_id,
            status=managed.status,
            summary=managed.summary,
            pending_approval=pending_details,
            created_at=managed.created_at,
            updated_at=managed.updated_at,
            error=managed.error,
        )

    # If not in TaskManager memory, check if EventBus holds historical events for this task
    events = event_bus.get_task_events(valid_id)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{valid_id}' not found.",
        )

    latest_event = events[-1]
    first_event = events[0]
    return TaskStatusResponse(
        task_id=valid_id,
        correlation_id=latest_event.correlation_id,
        status=latest_event.status,
        summary=latest_event.safe_user_message,
        pending_approval=pending_details,
        created_at=first_event.timestamp,
        updated_at=latest_event.timestamp,
    )


@router.get(
    "/tasks/{task_id}/events",
    response_model=TaskEventsResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Get Task Historical Events",
)
async def get_task_events(
    task_id: str,
    event_bus: EventBus = Depends(get_event_bus),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Returns bounded historical AgentEvents for a specific task.
    Enforces task isolation and returns only pre-sanitized event telemetry.
    """
    valid_id = validate_task_id(task_id)
    events = event_bus.get_task_events(valid_id)
    managed = task_manager.get_task(valid_id)

    if not events and not managed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{valid_id}' not found.",
        )

    serialized_events = [ev.model_dump(mode="json") for ev in events]
    return TaskEventsResponse(
        task_id=valid_id,
        events=serialized_events,
        total=len(serialized_events),
    )


@router.post(
    "/tasks/{task_id}/approve",
    response_model=ApprovalResponse,
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Resolve Pending Action Approval",
)
async def approve_task_step(
    task_id: str,
    request: ApprovalRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Affirmatively confirms or denies a high-risk action blocked on user approval.
    Reuses existing AgentOrchestrator safety gates and emits APPROVAL_GRANTED or
    APPROVAL_DENIED / TASK_CANCELLED events to the EventBus.
    """
    valid_id = validate_task_id(task_id)

    try:
        new_status, msg = await task_manager.handle_approval(
            task_id=valid_id,
            approved=request.approved,
            orchestrator=orchestrator,
            reason=request.reason,
        )
        return ApprovalResponse(
            task_id=valid_id,
            status=new_status,
            message=msg,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Approval handling error on task %s: %s", valid_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve action approval.",
        )
