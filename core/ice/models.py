"""
Domain Models for ORBIS Phase 9 Intent Continuity Engine (ICE).
Defines strongly typed models for intent candidates, multi-signal task continuity
states, candidate next actions, unfinished work items, and explainable inference results.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field

from core.types import RiskLevel


class TaskContinuityState(str, Enum):
    """Task execution and lifecycle continuity status."""
    ACTIVE = "ACTIVE"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    UNKNOWN = "UNKNOWN"


class ActionType(str, Enum):
    """Types of recommended candidate next actions."""
    CONTINUE_EDITING = "CONTINUE_EDITING"
    RUN_TESTS = "RUN_TESTS"
    DRAFT_DOCUMENT = "DRAFT_DOCUMENT"
    RESUME_RESEARCH = "RESUME_RESEARCH"
    REVIEW_CHANGES = "REVIEW_CHANGES"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    OTHER = "OTHER"


class IntentEvidenceType(str, Enum):
    """Classification of supporting evidence pieces."""
    WORKSPACE_EVENT = "WORKSPACE_EVENT"
    SESSION = "SESSION"
    CONTEXT_DNA = "CONTEXT_DNA"
    FILE_RESOURCE = "FILE_RESOURCE"
    GRAPH_EDGE = "GRAPH_EDGE"
    VECTOR_MATCH = "VECTOR_MATCH"
    AUDIT_LOG_ACTION = "AUDIT_LOG_ACTION"
    TERMINAL_OUTPUT = "TERMINAL_OUTPUT"
    EXPLICIT_PROMPT = "EXPLICIT_PROMPT"


class IntentEvidenceItem(BaseModel):
    """A single factual evidence point supporting an intent inference."""
    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:8]}")
    evidence_type: IntentEvidenceType
    timestamp: datetime
    description: str
    confidence_contribution: float = Field(
        default=0.1, ge=0.0, le=1.0,
        description="Heuristic contribution score in [0.0, 1.0]"
    )
    source_ref: str = Field(description="Unique ID of source entity, e.g. session_id or audit_log_id")
    is_observed_fact: bool = True


class IntentProvenance(BaseModel):
    """Strict, bidirectional traceability references back to ORBIS entities."""
    session_ids: List[str] = Field(default_factory=list)
    dna_ids: List[str] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)
    graph_node_ids: List[str] = Field(default_factory=list)
    vector_record_ids: List[str] = Field(default_factory=list)
    resource_paths: List[str] = Field(default_factory=list)
    audit_log_ids: List[int] = Field(default_factory=list)


class UnfinishedWorkItem(BaseModel):
    """Specific identifiable work unit that appears incomplete."""
    item_id: str = Field(default_factory=lambda: f"unf_{uuid4().hex[:8]}")
    description: str
    suspected_blocker: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)


class NextActionCandidate(BaseModel):
    """A recommended candidate action to resume continuity."""
    action_id: str = Field(default_factory=lambda: f"act_{uuid4().hex[:8]}")
    action_type: ActionType
    title: str
    description: str
    suggested_parameters: Dict[str, Any] = Field(default_factory=dict)
    estimated_risk: RiskLevel = RiskLevel.LOW
    heuristic_confidence_score: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Heuristic confidence score in [0.0, 1.0], NOT an empirical probability."
    )
    rationale: str
    evidence_ids: List[str] = Field(default_factory=list)
    is_inferred: bool = True


class IntentCandidate(BaseModel):
    """A hypothesized candidate task or goal inferred from evidence."""
    intent_id: str = Field(default_factory=lambda: f"intent_{uuid4().hex[:8]}")
    goal_description: str
    task_state: TaskContinuityState
    state_reasoning: str
    unfinished_work: List[UnfinishedWorkItem] = Field(default_factory=list)
    continuity_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Heuristic multi-signal continuity score in [0.0, 1.0]."
    )
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    evidence: List[IntentEvidenceItem] = Field(default_factory=list)
    provenance: IntentProvenance = Field(default_factory=IntentProvenance)
    candidate_next_actions: List[NextActionCandidate] = Field(default_factory=list)
    is_inferred: bool = True


class ICEQuery(BaseModel):
    """Input query to the Intent Continuity Engine."""
    query_text: str
    workspace_path: Optional[str] = None
    time_window_hours: Optional[float] = None
    max_intents: int = Field(default=3, ge=1, le=10)


class ICEResult(BaseModel):
    """Final explainable response produced by the Intent Continuity Engine."""
    query: str
    has_sufficient_evidence: bool
    primary_intent: Optional[IntentCandidate] = None
    alternative_intents: List[IntentCandidate] = Field(default_factory=list)
    explainability_summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: Optional[str] = None
    is_inferred: bool = True
