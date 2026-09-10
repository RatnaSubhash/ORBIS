"""
Intent Continuity Tool for ORBIS Phase 9.
Exposes Intent Continuity Engine (ICE) capabilities via the BaseTool contract,
enabling the Agent Orchestrator to answer "What was I trying to accomplish, and what should I do next?".
"""

from typing import Optional
from pydantic import BaseModel, Field

from core.ice.engine import IntentContinuityEngine
from core.ice.models import ICEResult
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class IntentToolParameters(BaseModel):
    """Input parameters for IntentContinuityTool."""
    query: str = Field(
        default="What was I trying to accomplish, and what should I do next?",
        description="The intent query asking about previous goals, unfinished work, or next actions.",
    )
    time_window_hours: Optional[float] = Field(
        default=None,
        description="Optional filter in hours to constrain continuity lookup.",
    )


class IntentContinuityTool(BaseTool):
    """
    Tool that infers candidate task goals, unfinished work, task continuity states,
    and recommended next actions strictly from grounded workspace evidence.
    Strictly read-only inspection (LOW risk).
    """

    name: str = "intent_continuity"
    description: str = (
        "Infers candidate task goals, unfinished work, task states, and candidate next actions "
        "from recorded workspace sessions, Context DNA, Knowledge Graph, and execution logs."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema = IntentToolParameters

    def __init__(
        self,
        engine: Optional[IntentContinuityEngine] = None,
        db: Optional[DatabaseManager] = None,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
    ) -> None:
        if engine is not None:
            self.engine = engine
        elif db is not None:
            self.engine = IntentContinuityEngine(db=db, graph=graph, vector_memory=vector_memory)
        else:
            temp_db = DatabaseManager(db_path=":memory:")
            temp_db.initialize()
            self.engine = IntentContinuityEngine(db=temp_db)

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes the ICE inference turn with typed parameters."""
        typed_params: IntentToolParameters = (
            params
            if isinstance(params, IntentToolParameters)
            else IntentToolParameters.model_validate(params)
        )

        result: ICEResult = await self.engine.infer_intent(typed_params.query)

        return ToolResult(
            success=True,
            output=result.model_dump(),
            metadata={
                "has_sufficient_evidence": result.has_sufficient_evidence,
                "primary_intent_state": (
                    result.primary_intent.task_state.value if result.primary_intent else None
                ),
                "continuity_score": (
                    result.primary_intent.continuity_score if result.primary_intent else 0.0
                ),
                "model_used": result.model_used,
            },
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that ICE produced a valid, grounded ICEResult structure."""
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Intent continuity tool failed or did not return a dictionary output.",
                discrepancies=["Output is not a valid dictionary."],
            )

        output = result.output
        if "has_sufficient_evidence" not in output or "explainability_summary" not in output:
            return VerificationReport(
                verified=False,
                details="Missing required ICEResult fields in output.",
                discrepancies=["Missing 'has_sufficient_evidence' or 'explainability_summary'."],
            )

        return VerificationReport(
            verified=True,
            details="ICEResult structure successfully verified.",
            discrepancies=[],
        )
