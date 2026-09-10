"""
Core Domain Models and Types for ORBIS.
Provides strongly typed Pydantic models for execution context, tool contracts,
safety risk levels, safe user-visible status events, and Phase 2 workspace observation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    """Execution risk classification for tool operations."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class StatusEventType(str, Enum):
    """
    Safe user-visible status states.
    Strictly forbids raw chain-of-thought or private reasoning leakage.
    """
    UNDERSTANDING_REQUEST = "UNDERSTANDING_REQUEST"
    CALLING_TOOL = "CALLING_TOOL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StatusEvent(BaseModel):
    """Structured status event broadcasted to user/UI."""
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: StatusEventType
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
    risk_level: Optional[RiskLevel] = None


class ToolResult(BaseModel):
    """Result of a tool execution."""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationReport(BaseModel):
    """Independent verification report evaluating real-world state changes."""
    verified: bool
    details: str
    discrepancies: List[str] = Field(default_factory=list)


class ExecutionContext(BaseModel):
    """Contextual metadata passed to tools and agent operations."""
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:8]}")
    session_id: Optional[str] = None
    user_id: str = "local_user"
    dry_run: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """Structured representation of an intended tool invocation."""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Final response returned by the Agent Orchestrator."""
    task_id: str
    status: StatusEventType
    summary: str
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    verification: Optional[VerificationReport] = None
    error: Optional[str] = None


# ==============================================================================
# Phase 2: Workspace Observation Types
# ==============================================================================


class WorkspaceEventType(str, Enum):
    """Types of safe workspace observation events."""
    APPLICATION_ACTIVATED = "APPLICATION_ACTIVATED"
    WINDOW_ACTIVATED = "WINDOW_ACTIVATED"
    OBSERVATION_SNAPSHOT = "OBSERVATION_SNAPSHOT"


class WindowMetadata(BaseModel):
    """Safe metadata snapshot of an active application window."""
    hwnd: Optional[int] = None
    title: Optional[str] = None
    process_id: Optional[int] = None
    process_name: Optional[str] = None
    executable_path: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_excluded: bool = False


class WorkspaceEvent(BaseModel):
    """Structured, minimal workspace observation event for timeline persistence."""
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: WorkspaceEventType
    process_name: Optional[str] = None
    process_id: Optional[int] = None
    window_title: Optional[str] = None
    resource_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ==============================================================================
# Phase 3: Session Segmentation and Context DNA Types
# ==============================================================================


class Session(BaseModel):
    """
    Factual representation of a contiguous workspace activity session.
    Groups temporally and contextually coherent workspace events.
    """
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    event_count: int
    applications: List[str] = Field(default_factory=list)
    process_names: List[str] = Field(default_factory=list)
    resources: List[str] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)
    session_metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class ContextDNA(BaseModel):
    """
    Structured, explainable, deterministic snapshot of a workspace session.
    Composed strictly of factual observed metadata across 6 defined categories.
    """
    dna_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    context_dna_version: str = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    temporal_context: Dict[str, Any]
    application_context: Dict[str, Any]
    resource_context: Dict[str, Any]
    activity_context: Dict[str, Any]
    privacy_context: Dict[str, Any]
    quality_indicators: Dict[str, Any]
    source_event_ids: List[str] = Field(default_factory=list)

