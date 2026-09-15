"""
DesktopTool for ORBIS Phase 2.
Provides safe, read-only inspection of active application and window state
via the workspace observer abstraction.
"""

from typing import Any, Dict, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.observation.base import BaseWorkspaceObserver
from core.observation.windows import WindowsWorkspaceObserver
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class DesktopActionParameters(BaseModel):
    """Parameters for DesktopTool actions."""
    action: Literal["get_active_window", "capture_snapshot", "inspect_active_window"] = Field(
        default="get_active_window",
        description="The read-only desktop inspection action to execute.",
    )


class DesktopTool(BaseTool):
    """
    Tool providing safe, read-only inspection of the active Windows desktop workspace.
    Guaranteed not to modify window positions, terminate processes, or manipulate input.
    """

    name: str = "desktop"
    description: str = (
        "Safe read-only inspection of the currently active desktop window and application state."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = DesktopActionParameters

    def __init__(self, observer: Optional[BaseWorkspaceObserver] = None) -> None:
        self.observer = observer or WindowsWorkspaceObserver()

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes the requested read-only desktop inspection action."""
        if not isinstance(params, DesktopActionParameters):
            params = self.validate_parameters(params.model_dump())

        try:
            if params.action in ("get_active_window", "inspect_active_window"):
                window = self.observer.get_active_window()
                output = window.model_dump(mode="json") if window else None
            elif params.action == "capture_snapshot":
                snapshot = self.observer.capture_snapshot()
                output = snapshot.model_dump(mode="json")
            else:
                return ToolResult(success=False, error=f"Unknown desktop action: {params.action}")

            return ToolResult(
                success=True,
                output=output,
                metadata={"action": params.action},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Desktop inspection failed: {str(e)}")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that the inspection produced structured desktop metadata."""
        if not result.success:
            return VerificationReport(
                verified=False,
                details="Tool execution returned failure.",
                discrepancies=[result.error or "Unknown error"],
            )

        if result.output is None:
            # None is a valid output if no active foreground window is present
            return VerificationReport(
                verified=True,
                details="No foreground window detected (valid system state).",
            )

        if not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Output format is invalid; expected dictionary.",
                discrepancies=["Output is not a dictionary"],
            )

        # Confirm essential schema keys exist
        expected_keys = {"process_name", "title", "timestamp", "is_excluded"}
        missing_keys = expected_keys - set(result.output.keys())
        if missing_keys:
            return VerificationReport(
                verified=False,
                details=f"Missing metadata keys: {missing_keys}",
                discrepancies=[f"Missing key: {k}" for k in missing_keys],
            )

        return VerificationReport(
            verified=True,
            details="Desktop inspection output verified successfully.",
        )
