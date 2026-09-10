"""
Demonstration EchoTool for ORBIS Phase 1.
Serves as a harmless, deterministic test tool to validate the
Agent -> Registry -> Tool -> Verification pipeline.
"""

from typing import Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class EchoParameters(BaseModel):
    """Parameter schema for EchoTool."""
    message: str = Field(..., min_length=1, description="The message to echo back.")


class EchoTool(BaseTool):
    """A harmless demonstration tool that echoes back an input message."""

    name: str = "echo"
    description: str = "Echoes back the provided message to test the execution and verification pipeline."
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = EchoParameters

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Echoes the input message back in the ToolResult."""
        if not isinstance(params, EchoParameters):
            params = self.validate_parameters(params.model_dump())

        return ToolResult(
            success=True,
            output=params.message,
            metadata={"character_count": len(params.message)},
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that the output matches the input message."""
        if not isinstance(params, EchoParameters):
            params = self.validate_parameters(params.model_dump())

        if not result.success:
            return VerificationReport(
                verified=False,
                details="Tool execution failed.",
                discrepancies=["Execution status success=False"],
            )

        if result.output != params.message:
            return VerificationReport(
                verified=False,
                details="Output does not match input message.",
                discrepancies=[f"Expected '{params.message}', got '{result.output}'"],
            )

        return VerificationReport(
            verified=True,
            details="Output matches the input message exactly.",
        )
