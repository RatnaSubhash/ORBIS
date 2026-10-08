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
    conversation_id: Optional[str] = Field(default=None, description="Optional conversation session ID to route multi-turn context.")
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


# ============================================================================
# Phase F Continuity & Workspace Models
# ============================================================================

class ContinuityObservationStatus(BaseModel):
    """Observation service operational telemetry."""
    active: bool
    polling_interval: float
    total_events: int


class ContextDnaSummary(BaseModel):
    """Factual summary of a persisted Context DNA snapshot."""
    dna_id: str
    session_id: str
    version: str = "1.0"
    created_at: Optional[datetime] = None
    freshness: str = "persisted"
    task_summary: Optional[str] = None
    focus_score: Optional[float] = None
    project_root: Optional[str] = None
    primary_application: Optional[str] = None
    active_applications: List[str] = Field(default_factory=list)
    primary_resource: Optional[str] = None
    resources_accessed: List[str] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)


class SessionSummaryItem(BaseModel):
    """Factual summary of a segmented workspace session."""
    session_id: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    event_count: int
    applications: List[str] = Field(default_factory=list)
    resources: List[str] = Field(default_factory=list)
    confidence: float = 1.0


class KnowledgeGraphSummary(BaseModel):
    """Summary metrics of the persisted Knowledge Graph."""
    total_nodes: int = 0
    total_edges: int = 0
    application_count: int = 0
    file_count: int = 0
    topic_count: int = 0
    top_entities: List[Dict[str, Any]] = Field(default_factory=list)


class ContinuityContextResponse(BaseModel):
    """Authoritative response payload for GET /api/continuity/context."""
    observation: ContinuityObservationStatus
    current_dna: Optional[ContextDnaSummary] = None
    recent_sessions: List[SessionSummaryItem] = Field(default_factory=list)
    knowledge_graph: KnowledgeGraphSummary


class SessionEventItem(BaseModel):
    """Sanitized individual workspace observation event."""
    event_id: str
    timestamp: datetime
    event_type: str
    process_name: Optional[str] = None
    window_title: Optional[str] = None
    resource_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionEventsResponse(BaseModel):
    """Authoritative response payload for GET /api/continuity/sessions/{session_id}/events."""
    session_id: str
    events: List[SessionEventItem] = Field(default_factory=list)
    total: int = 0


class ToolMetadataItem(BaseModel):
    """User-safe metadata for an authoritative registered tool."""
    id: str = Field(..., description="Stable tool identifier (e.g. 'file', 'terminal_tool')")
    name: str = Field(..., description="Human-readable display name")
    description: str = Field(..., description="Tool purpose and behavior description")
    version: str = Field(default="1.0.0", description="Tool semver version")
    risk_level: str = Field(..., description="Registered risk level (LOW, MEDIUM, HIGH, BLOCKED)")
    category: str = Field(..., description="Deterministic tool domain category")
    status: str = Field(default="AVAILABLE", description="Registry availability state ('AVAILABLE')")
    parameters_schema: Dict[str, Any] = Field(default_factory=dict, description="Sanitized JSON schema of the parameter model")
    actions: List[str] = Field(default_factory=list, description="Supported operations or modes")
    example_prompt: Optional[str] = Field(default=None, description="Suggested prompt for discovery and trial")


class ToolRegistryResponse(BaseModel):
    """Authoritative response payload for GET /api/tools."""
    tools: List[ToolMetadataItem] = Field(default_factory=list, description="List of all registered tools")
    total_tools: int = Field(default=0, description="Total count of registered tools")
    categories: List[str] = Field(default_factory=list, description="Sorted list of unique categories present in the registry")


class ConversationResetRequest(BaseModel):
    """Payload for resetting an active conversation context."""
    conversation_id: Optional[str] = Field(default=None, description="Target conversation session ID to reset. If omitted, resets default/all.")


class ConversationResetResponse(BaseModel):
    """Response returned upon resetting conversation context."""
    status: str = "ok"
    conversation_id: str
    message: str


class ConversationNewResponse(BaseModel):
    """Response returned upon creating a new conversation session."""
    status: str = "ok"
    conversation_id: str
    message: str


class ConversationContextResponse(BaseModel):
    """Details of active conversational continuity state."""
    conversation_id: str
    active_topic: Optional[str] = None
    active_entity: Optional[str] = None
    active_entities: List[str] = Field(default_factory=list)
    turn_count: int = 0
    turns: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Phase 10 / Workspace Restoration Models
# ============================================================================

class RestorationStepPreview(BaseModel):
    """Declarative restoration step details for preflight preview."""
    step_id: str
    app_identifier: str
    display_name: str
    command_executable: Optional[str] = None
    command_args: List[str] = Field(default_factory=list)
    target_type: Optional[str] = None
    target_value: Optional[str] = None
    window_bounds: Optional[Dict[str, int]] = None
    window_placement: Optional[str] = None
    risk_level: str
    status: str
    is_valid: bool
    status_message: Optional[str] = None


class RestorationPlanPreview(BaseModel):
    """Complete preflight-validated restoration plan preview."""
    plan_id: str
    session_id: str
    goal_description: str
    overall_risk: str
    requires_user_approval: bool
    is_preflight_valid: bool
    preflight_errors: List[str] = Field(default_factory=list)
    steps: List[RestorationStepPreview] = Field(default_factory=list)


class RestorationPreviewRequest(BaseModel):
    """Payload for previewing a workspace restoration plan without execution."""
    session_id: Optional[str] = Field(default=None, description="Direct session ID to restore.")
    query: Optional[str] = Field(default=None, description="Natural language query describing the workspace to restore.")
    target_resource: Optional[str] = Field(default=None, description="Explicit project or directory target.")


class RestorationPreviewResponse(BaseModel):
    """Response returned for restoration plan preview."""
    status: str = "ok"
    plan: Optional[RestorationPlanPreview] = None
    message: str


class RestorationExecuteRequest(BaseModel):
    """Payload for executing a validated workspace restoration plan."""
    session_id: Optional[str] = Field(default=None, description="Direct session ID to restore.")
    query: Optional[str] = Field(default=None, description="Natural language query describing workspace.")
    target_resource: Optional[str] = Field(default=None, description="Explicit project or directory target.")
    selected_step_ids: Optional[List[str]] = Field(default=None, description="Optional step IDs to restrict execution to.")
    approved: bool = Field(default=True, description="Policy approval flag.")


class RestorationExecuteResponse(BaseModel):
    """Response returned upon executing a workspace restoration plan."""
    plan_id: str
    session_id: str
    success: bool
    is_partial_restoration: bool
    completed_steps: List[str] = Field(default_factory=list)
    failed_steps: List[str] = Field(default_factory=list)
    skipped_steps: List[str] = Field(default_factory=list)
    step_results: Dict[str, Any] = Field(default_factory=dict)
    verification_reports: Dict[str, Any] = Field(default_factory=dict)
    summary: str


class RestorationSessionsResponse(BaseModel):
    """Response returned for restorable workspace sessions."""
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


