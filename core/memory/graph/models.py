"""
Knowledge Graph Entity and Relationship Models for ORBIS Phase 4.
Defines strongly typed node types, edge types, and graph primitives.
"""

from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """Observable entity types represented in the Workspace Knowledge Graph."""
    SESSION = "SESSION"
    APPLICATION = "APPLICATION"
    PROCESS = "PROCESS"
    RESOURCE = "RESOURCE"
    WORKSPACE_EVENT = "WORKSPACE_EVENT"
    CONTEXT_DNA = "CONTEXT_DNA"


class EdgeType(str, Enum):
    """Explicit, factual relationships between Knowledge Graph entities."""
    CONTAINS = "CONTAINS"          # SESSION -> WORKSPACE_EVENT
    USES = "USES"                  # SESSION -> APPLICATION
    OBSERVES = "OBSERVES"          # SESSION -> RESOURCE
    HAS_CONTEXT = "HAS_CONTEXT"    # SESSION -> CONTEXT_DNA
    OCCURRED_IN = "OCCURRED_IN"    # WORKSPACE_EVENT -> SESSION, WORKSPACE_EVENT -> APPLICATION
    REFERENCES = "REFERENCES"      # WORKSPACE_EVENT -> RESOURCE


class GraphNode(BaseModel):
    """Factual entity node in the Knowledge Graph."""
    node_id: str
    node_type: NodeType
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """Directed edge representing an explicit relationship between two nodes."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    properties: Dict[str, Any] = Field(default_factory=dict)
