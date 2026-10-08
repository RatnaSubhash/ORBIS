"""
Domain Models for ORBIS Phase 5 Continuity Subsystem.
Defines strongly typed models for continuity queries, retrieved evidence,
provenance references, and grounded continuity responses.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class ContinuityQueryType(str, Enum):
    """Classification of continuity requests."""
    WHAT_WAS_I_WORKING_ON = "WHAT_WAS_I_WORKING_ON"
    RECENT_CONTEXT = "RECENT_CONTEXT"
    LAST_SESSION = "LAST_SESSION"
    GENERAL_CONTINUITY = "GENERAL_CONTINUITY"
    APP_QUERY = "APP_QUERY"
    PROJECT_QUERY = "PROJECT_QUERY"
    FILE_QUERY = "FILE_QUERY"
    WHAT_CHANGED_TODAY = "WHAT_CHANGED_TODAY"


class ContinuityQuery(BaseModel):
    """Structured request for workspace continuity context."""
    query_text: str
    query_type: ContinuityQueryType = ContinuityQueryType.GENERAL_CONTINUITY
    time_window_hours: Optional[float] = None
    cutoff_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_results: int = Field(default=3, ge=1, le=20)


class ProvenanceSource(BaseModel):
    """
    Genuinely available source evidence identifiers.
    Preserves strict traceability back to stored ORBIS entities.
    Does not manufacture references for unavailable sources.
    """
    session_ids: List[str] = Field(default_factory=list)
    dna_ids: List[str] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)
    graph_node_ids: List[str] = Field(default_factory=list)
    vector_record_ids: List[str] = Field(default_factory=list)


class RetrievedContextEvidence(BaseModel):
    """Factual evidence retrieved for a specific candidate session."""
    session_id: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    applications: List[str] = Field(default_factory=list)
    primary_application: Optional[str] = None
    resources: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    event_count: int = 0
    vector_similarity: Optional[float] = None
    provenance: ProvenanceSource = Field(default_factory=ProvenanceSource)


class ContinuityResponse(BaseModel):
    """
    Final user-facing and structured response to a continuity query.
    Grounded strictly in observable workspace evidence.
    """
    query: str
    has_sufficient_context: bool
    grounded_summary: str
    active_applications: List[str] = Field(default_factory=list)
    primary_resources: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    last_activity_timestamp: Optional[datetime] = None
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Deterministic evidence-quality and relevance heuristic in [0.0, 1.0], "
            "NOT a statistically calibrated probability."
        ),
    )
    evidence: List[RetrievedContextEvidence] = Field(default_factory=list)
    provenance: ProvenanceSource = Field(default_factory=ProvenanceSource)
