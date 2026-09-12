"""
Domain Models for ORBIS Phase 6 Multi-Step Agent Brain & Hierarchical Planner.
Defines strongly typed models for task states, task steps, hierarchical plans,
step execution results, verification records, and agent execution results.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field

from core.types import ExecutionContext, RiskLevel


class TaskState(str, Enum):
    """Execution states for task steps and overall plans."""
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    BLOCKED_PENDING_APPROVAL = "BLOCKED_PENDING_APPROVAL"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    """Classification of high-level tasks."""
    SINGLE_STEP = "SINGLE_STEP"
    SEQUENTIAL = "SEQUENTIAL"
    HIERARCHICAL = "HIERARCHICAL"
    CONTINUITY = "CONTINUITY"


class TaskStep(BaseModel):
    """
    A single executable or composite step within a hierarchical task plan.
    """
    step_id: str
    parent_step_id: Optional[str] = None
    title: str
    description: str = ""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    input_bindings: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps parameter keys to previous step output references, e.g. {'path': 'step_1.path'}",
    )
    output_key: Optional[str] = None
    dependencies: List[str] = Field(
        default_factory=list,
        description="List of step_ids that must successfully complete and verify before this step can run.",
    )
    state: TaskState = TaskState.PENDING
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    approval_prompt: Optional[str] = Field(
        default=None,
        description="Specific question presented to the user when step halts for approval.",
    )
    max_attempts: int = Field(default=2, ge=1, le=5)
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskPlan(BaseModel):
    """
    A structured, validated hierarchical task execution plan.
    """
    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    user_prompt: str
    task_type: TaskType = TaskType.SEQUENTIAL
    root_step_ids: List[str] = Field(default_factory=list)
    steps: Dict[str, TaskStep] = Field(default_factory=dict)
    max_steps: int = Field(default=10, ge=1, le=50)
    max_replans: int = Field(default=2, ge=0, le=5)
    replan_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StepResult(BaseModel):
    """Factual execution outcome for an individual task step."""
    step_id: str
    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StepVerification(BaseModel):
    """Independent verification assessment of a step's real-world result."""
    step_id: str
    verified: bool
    details: str
    discrepancies: List[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    """Structured request submitted to the agent brain."""
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    user_prompt: str
    context: ExecutionContext = Field(default_factory=ExecutionContext)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentExecutionResult(BaseModel):
    """
    Final comprehensive result of executing a multi-step task plan.
    Grounds all statements in verifiable step outputs.
    """
    task_id: str
    request: str
    status: TaskState
    summary: str
    plan: TaskPlan
    completed_steps: List[str] = Field(default_factory=list)
    failed_step: Optional[str] = None
    error: Optional[str] = None
    step_results: Dict[str, StepResult] = Field(default_factory=dict)
    verification_reports: Dict[str, StepVerification] = Field(default_factory=dict)
    final_output: Any = None


class PendingApproval(BaseModel):
    """
    Retains a blocked task plan and metadata awaiting human confirmation.
    Scoped strictly to the pending action with a bounded time-to-live.
    """
    task_id: str
    plan: TaskPlan
    blocked_step_id: str
    action_description: str
    tool_name: str
    target_resource: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    step_results: Dict[str, StepResult] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
