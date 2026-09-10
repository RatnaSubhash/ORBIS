"""
Continuity Tool for ORBIS Phase 5.
Exposes workspace continuity query capabilities via the BaseTool contract,
enabling the Agent Orchestrator to answer "What was I working on?".
"""

from typing import Optional
from pydantic import BaseModel, Field

from core.continuity.models import ContinuityResponse
from core.continuity.service import ContinuityService
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class ContinuityToolParameters(BaseModel):
    """Input parameters for ContinuityTool."""
    query: str = Field(
        default="What was I working on?",
        description="The continuity question or request about previous workspace activity.",
    )
    time_window_hours: Optional[float] = Field(
        default=None,
        description="Optional filter in hours to constrain continuity lookup.",
    )


class ContinuityTool(BaseTool):
    """
    Tool that answers continuity questions about previous workspace activity
    grounded in recorded Sessions, Context DNA, Knowledge Graph, and Vector Memory.
    """

    name: str = "continuity_tool"
    description: str = (
        "Answers continuity questions about previous workspace sessions, "
        "active applications, opened files, and recent tasks using grounded evidence."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema = ContinuityToolParameters

    def __init__(
        self,
        continuity_service: Optional[ContinuityService] = None,
        db: Optional[DatabaseManager] = None,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
    ) -> None:
        if continuity_service is not None:
            self.service = continuity_service
        elif db is not None:
            self.service = ContinuityService(db=db, graph=graph, vector_memory=vector_memory)
        else:
            # Fallback in-memory DB if none provided
            temp_db = DatabaseManager(db_path=":memory:")
            temp_db.initialize()
            self.service = ContinuityService(db=temp_db)

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes the continuity lookup with typed parameters."""
        typed_params: ContinuityToolParameters = (
            params
            if isinstance(params, ContinuityToolParameters)
            else ContinuityToolParameters.model_validate(params)
        )

        response: ContinuityResponse = self.service.execute_query(typed_params.query)

        return ToolResult(
            success=True,
            output=response.model_dump(),
            metadata={
                "has_sufficient_context": response.has_sufficient_context,
                "confidence_score": response.confidence_score,
                "active_applications": response.active_applications,
                "primary_resources": response.primary_resources,
            },
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that the continuity tool produced a valid, grounded response structure."""
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Tool execution failed or did not return a structured dictionary.",
                discrepancies=["Output is not a valid continuity response dictionary."],
            )

        output = result.output
        if "grounded_summary" not in output or "has_sufficient_context" not in output:
            return VerificationReport(
                verified=False,
                details="Missing required continuity fields in output.",
                discrepancies=["Missing 'grounded_summary' or 'has_sufficient_context'."],
            )

        return VerificationReport(
            verified=True,
            details=(
                f"Grounded continuity response generated successfully. "
                f"Context sufficient: {output.get('has_sufficient_context')}, "
                f"Confidence: {output.get('confidence_score')}."
            ),
        )
