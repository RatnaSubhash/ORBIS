"""
Multi-App Workspace Restoration Tool for ORBIS Phase 10.
Exposes declarative workspace reconstruction capabilities via the BaseTool contract,
enabling the Agent Orchestrator to restore multi-app window layouts and resources.
"""

from datetime import datetime
from typing import Any, Callable, Optional
from pydantic import BaseModel, Field

from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine
from core.restoration.models import RestorationPlan, RestorationResult
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class RestorationToolParameters(BaseModel):
    """Input parameters for RestorationTool."""
    query: str = Field(
        default="Restore my previous workspace",
        description="Query describing the workspace, task, or previous session to restore.",
    )
    execute: bool = Field(
        default=False,
        description="Whether to execute the restoration plan (requires approval callback/policy) or only generate and validate it.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session ID to restore directly.",
    )
    target_resource: Optional[str] = Field(
        default=None,
        description="Optional explicit project or resource target.",
    )
    reference_time: Optional[datetime] = Field(
        default=None,
        description="Optional continuity cutoff timestamp.",
    )


class RestorationTool(BaseTool):
    """
    Tool that generates preflight-validated, declarative multi-app restoration plans
    from ICE evidence, and executes them within strict safety and approval boundaries.
    """

    name: str = "restoration_tool"
    description: str = (
        "Reconstructs previously active multi-application workspaces and window layouts "
        "based on Intent Continuity Engine evidence, trusted application registries, and virtual screen geometry."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema = RestorationToolParameters

    def __init__(
        self,
        engine: Optional[RestorationEngine] = None,
        db: Optional[DatabaseManager] = None,
        approval_callback: Optional[Callable[[RestorationPlan], Any]] = None,
    ) -> None:
        if engine is not None:
            self.engine = engine
        elif db is not None:
            self.engine = RestorationEngine(db=db)
        else:
            temp_db = DatabaseManager(db_path=":memory:")
            temp_db.initialize()
            self.engine = RestorationEngine(db=temp_db)
        self.approval_callback = approval_callback

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes the restoration tool: planning or executing a validated plan."""
        typed_params: RestorationToolParameters = (
            params
            if isinstance(params, RestorationToolParameters)
            else RestorationToolParameters.model_validate(params)
        )

        # Resolve reference_time from context if available
        ref_time = typed_params.reference_time
        if ref_time is None and context and isinstance(context.metadata, dict):
            raw_ref = context.metadata.get("request_timestamp") or context.metadata.get("reference_time")
            if raw_ref and isinstance(raw_ref, str):
                try:
                    ref_time = datetime.fromisoformat(raw_ref)
                except Exception:
                    pass
            elif isinstance(raw_ref, datetime):
                ref_time = raw_ref

        # 1. Create and preflight-validate declarative plan
        plan, ice_result, message = await self.engine.create_restoration_plan(
            query=typed_params.query,
            session_id=typed_params.session_id,
            target_resource=typed_params.target_resource,
            reference_time=ref_time,
        )

        if not plan:
            return ToolResult(
                success=False,
                output={
                    "status": "failed_to_plan",
                    "message": message,
                    "ice_result": ice_result.model_dump() if ice_result else None,
                },
                metadata={"plan_created": False},
            )

        # 2. If execute=False, return the declarative plan
        if not typed_params.execute:
            return ToolResult(
                success=True,
                output={
                    "status": "plan_generated",
                    "plan": plan.model_dump(),
                    "requires_user_approval": plan.requires_user_approval,
                    "overall_risk": plan.overall_risk.value,
                    "is_preflight_valid": plan.is_preflight_valid,
                },
                metadata={
                    "plan_id": plan.plan_id,
                    "step_count": len(plan.steps),
                    "overall_risk": plan.overall_risk.value,
                    "requires_approval": plan.requires_user_approval,
                },
            )

        # 3. If execute=True, execute via RestorationEngine
        approval_cb = self.approval_callback
        if approval_cb is None and context and isinstance(getattr(context, "metadata", None), dict):
            approval_cb = context.metadata.get("approval_callback")

        res: RestorationResult = await self.engine.execute_restoration_plan(
            plan=plan,
            approval_callback=approval_cb,
        )

        return ToolResult(
            success=res.success,
            output={
                "status": "executed",
                "result": res.model_dump(),
            },
            metadata={
                "plan_id": res.plan_id,
                "is_partial": res.is_partial_restoration,
                "completed_steps": len(res.completed_steps),
                "failed_steps": len(res.failed_steps),
            },
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that the restoration tool produced a valid structured output."""
        if not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Restoration tool output is not a dictionary.",
                discrepancies=["Output must be a dictionary."],
            )

        status = result.output.get("status")
        if status not in ("plan_generated", "executed", "failed_to_plan"):
            return VerificationReport(
                verified=False,
                details=f"Unrecognized restoration status '{status}'.",
                discrepancies=[f"Unexpected status: {status}"],
            )

        return VerificationReport(
            verified=True,
            details=f"Restoration output verified with status: {status}.",
            discrepancies=[],
        )
