"""
Domain Models for ORBIS Phase 12 Agent Events.
Defines strongly typed, safe, user-visible lifecycle telemetry models.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.types import RiskLevel


class AgentEventType(str, Enum):
    """
    Categorized agent activity and lifecycle milestone event types.
    User-facing telemetry representing real system execution states.
    """
    TASK_STARTED = "TASK_STARTED"
    PLAN_CREATED = "PLAN_CREATED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    SOURCE_FOUND = "SOURCE_FOUND"
    SOURCE_READ = "SOURCE_READ"
    CONTENT_GENERATION_STARTED = "CONTENT_GENERATION_STARTED"
    DOCUMENT_UPDATED = "DOCUMENT_UPDATED"
    APPLICATION_OPENED = "APPLICATION_OPENED"
    SYSTEM_ACTION_STARTED = "SYSTEM_ACTION_STARTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"


class AgentEvent(BaseModel):
    """
    Structured, user-visible real-time activity event.
    Strictly forbids exposing raw chain-of-thought, hidden reasoning,
    system prompts, credentials, or unrestricted tool output.
    """
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    correlation_id: str
    task_id: str
    parent_task_id: Optional[str] = None
    step_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: AgentEventType
    status: str
    safe_user_message: str
    tool_name: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
