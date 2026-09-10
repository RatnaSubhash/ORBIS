"""
Data models for Semantic Vector Memory in ORBIS Phase 4.
"""

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4
from pydantic import BaseModel, Field


class VectorRecord(BaseModel):
    """Metadata record representing an indexed vector in the vector store."""
    vector_id: str = Field(default_factory=lambda: uuid4().hex)
    entity_type: str  # e.g., 'SESSION', 'CONTEXT_DNA'
    entity_id: str    # e.g., session_id or dna_id
    embedding_model: str
    text_representation: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SearchResult(BaseModel):
    """Result of a semantic similarity query."""
    entity_id: str
    entity_type: str
    similarity: float  # Cosine similarity score [-1.0, 1.0]
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    text_representation: str
