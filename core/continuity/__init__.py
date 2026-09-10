"""
ORBIS Continuity Package.
Provides session segmentation and Context DNA synthesis.
"""

from core.continuity.dna import CURRENT_CONTEXT_DNA_VERSION, ContextDNASynthesizer
from core.continuity.grounding import GroundedResponseBuilder
from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ContinuityResponse,
    ProvenanceSource,
    RetrievedContextEvidence,
)
from core.continuity.retriever import ContinuityRetriever
from core.continuity.service import ContinuityService
from core.continuity.sessionizer import IncrementalSessionBuilder, SessionSegmenter

__all__ = [
    "SessionSegmenter",
    "IncrementalSessionBuilder",
    "ContextDNASynthesizer",
    "CURRENT_CONTEXT_DNA_VERSION",
    "ContinuityQuery",
    "ContinuityQueryType",
    "ContinuityResponse",
    "ProvenanceSource",
    "RetrievedContextEvidence",
    "ContinuityRetriever",
    "GroundedResponseBuilder",
    "ContinuityService",
]
