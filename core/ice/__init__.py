"""
ORBIS Phase 9: Intent Continuity Engine (ICE).
Infers candidate task/goal continuity, multi-signal task state, unfinished work,
and candidate next actions from grounded workspace evidence.
"""

from core.ice.models import (
    ActionType,
    ICEQuery,
    ICEResult,
    IntentCandidate,
    IntentEvidenceItem,
    IntentEvidenceType,
    IntentProvenance,
    NextActionCandidate,
    TaskContinuityState,
    UnfinishedWorkItem,
)

__all__ = [
    "ActionType",
    "ICEQuery",
    "ICEResult",
    "IntentCandidate",
    "IntentEvidenceItem",
    "IntentEvidenceType",
    "IntentProvenance",
    "NextActionCandidate",
    "TaskContinuityState",
    "UnfinishedWorkItem",
]
