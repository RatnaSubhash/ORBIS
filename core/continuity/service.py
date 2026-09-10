"""
Continuity Service for ORBIS Phase 5.
Coordinates continuity query classification, retrieval across SQLite,
Knowledge Graph, and Semantic Vector Memory, and grounded response synthesis.
"""

from typing import Optional, Union

from core.continuity.grounding import GroundedResponseBuilder
from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ContinuityResponse,
    ProvenanceSource,
)
from core.continuity.retriever import ContinuityRetriever
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory


class ContinuityService:
    """
    Main orchestration service for ORBIS Phase 5 continuity requests.
    Decoupled from future general-purpose agent planners.
    """

    def __init__(
        self,
        db: DatabaseManager,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
        retriever: Optional[ContinuityRetriever] = None,
        response_builder: Optional[GroundedResponseBuilder] = None,
    ) -> None:
        self.db = db
        self.graph = graph
        self.vector_memory = vector_memory
        self.retriever = retriever or ContinuityRetriever(
            db=db, graph=graph, vector_memory=vector_memory
        )
        self.response_builder = response_builder or GroundedResponseBuilder()

    @staticmethod
    def is_continuity_query(query: str) -> bool:
        """
        Determines whether a user query is asking for workspace continuity context.
        Uses deterministic pattern matching without relying on ungrounded LLM guessing.
        """
        if not query or not query.strip():
            return False

        q = query.strip().lower()
        patterns = [
            "what was i working on",
            "what was i doing",
            "what did i work on",
            "what am i working on",
            "last session",
            "previous session",
            "recent workspace",
            "recent context",
            "show recent",
            "where did i leave off",
            "where i left off",
            "workspace context",
        ]
        return any(p in q for p in patterns)

    @classmethod
    def classify_query(cls, query_text: str) -> ContinuityQuery:
        """
        Classifies a raw string into a structured ContinuityQuery.
        """
        clean_text = query_text.strip() if query_text else ""
        q_lower = clean_text.lower()

        if "last session" in q_lower or "previous session" in q_lower:
            q_type = ContinuityQueryType.LAST_SESSION
            max_res = 1
        elif "recent context" in q_lower or "recent workspace" in q_lower or "show recent" in q_lower:
            q_type = ContinuityQueryType.RECENT_CONTEXT
            max_res = 3
        elif "working on" in q_lower or "doing" in q_lower or "leave off" in q_lower:
            q_type = ContinuityQueryType.WHAT_WAS_I_WORKING_ON
            max_res = 1
        else:
            q_type = ContinuityQueryType.GENERAL_CONTINUITY
            max_res = 3

        return ContinuityQuery(
            query_text=clean_text,
            query_type=q_type,
            max_results=max_res,
        )

    def execute_query(self, query: Union[str, ContinuityQuery]) -> ContinuityResponse:
        """
        Executes an end-to-end continuity query turn.
        Retrieves evidence and synthesizes a grounded response.
        """
        if isinstance(query, str):
            if not query or not query.strip():
                return ContinuityResponse(
                    query=query or "",
                    has_sufficient_context=False,
                    grounded_summary="Empty continuity query provided. Context is unavailable.",
                    active_applications=[],
                    primary_resources=[],
                    last_activity_timestamp=None,
                    confidence_score=0.0,
                    evidence=[],
                    provenance=ProvenanceSource(),
                )
            structured_query = self.classify_query(query)
        else:
            structured_query = query
            if not structured_query.query_text or not structured_query.query_text.strip():
                return ContinuityResponse(
                    query="",
                    has_sufficient_context=False,
                    grounded_summary="Empty continuity query provided. Context is unavailable.",
                    active_applications=[],
                    primary_resources=[],
                    last_activity_timestamp=None,
                    confidence_score=0.0,
                    evidence=[],
                    provenance=ProvenanceSource(),
                )

        evidence_list = self.retriever.retrieve(structured_query)
        return self.response_builder.build_response(
            query=structured_query.query_text, evidence_list=evidence_list
        )
