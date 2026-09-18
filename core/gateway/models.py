"""
Request, response, and telemetry data models for the ORBIS FastAPI Gateway (Phase 12.8).
Ensures strict type validation, safe serialization, and bounded payloads.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskSubmitRequest(BaseModel):
    """Payload for submitting a new agent task."""
    prompt: str = Field(..., min_length=1, max_length=4000, description="The user prompt or command to execute.")
    correlation_id: Optional[str] = Field(default=None, description="Optional client-provided correlation ID.")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata to attach to task context.")
    sync: bool = Field(default=False, description="If True, blocks until task completion; if False, runs in background.")


class TaskSubmitResponse(BaseModel):
    """Response returned upon task submission."""
    task_id: str
    correlation_id: str
    status: str
    message: str


class PendingApprovalDetails(BaseModel):
    """User-safe details describing an action currently awaiting approval."""
    task_id: str
    blocked_step_id: str
    action_description: str
    tool_name: str
    target_resource: Optional[str] = None
    expires_at: datetime


class TaskStatusResponse(BaseModel):
    """User-safe operational status of an agent task."""
    task_id: str
    correlation_id: str
    status: str
    summary: Optional[str] = None
    answer: Optional[str] = None
    pending_approval: Optional[PendingApprovalDetails] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    error: Optional[str] = None


class ApprovalRequest(BaseModel):
    """Payload for approving or denying a pending task step."""
    approved: bool = Field(..., description="True to approve and resume; False to deny and cancel.")
    reason: Optional[str] = Field(default=None, max_length=500, description="Optional reason for the decision.")


class ApprovalResponse(BaseModel):
    """Response returned upon resolving a pending approval."""
    task_id: str
    status: str
    message: str


class TaskEventsResponse(BaseModel):
    """Response containing bounded historical events for a task."""
    task_id: str
    events: List[Dict[str, Any]]
    total: int


class ErrorResponse(BaseModel):
    """Standardized, machine-readable, user-safe error payload."""
    error: str
    code: str
    details: Optional[Any] = None


class VoiceStartResponse(BaseModel):
    """Response returned when push-to-talk voice capture begins."""
    status: str
    message: str


class VoiceStopResponse(BaseModel):
    """Response returned when push-to-talk voice capture stops and transcribes."""
    transcript: str
    empty: bool
    status: str


class VoiceCancelResponse(BaseModel):
    """Response returned when voice capture is cancelled without transcribing."""
    status: str
    message: str


class VoiceSpeakRequest(BaseModel):
    """Payload for requesting local SAPI5 TTS vocalization."""
    text: str = Field(..., min_length=1, max_length=5000, description="User-facing text to speak via local TTS.")


class VoiceSpeakResponse(BaseModel):
    """Response returned upon completing local TTS vocalization."""
    status: str
    message: str
