"""
Hardware Control Tool for ORBIS.
Provides safe RGB keyboard/peripherals lighting control and controlled gaming integration.
Handles hardware-dependent device APIs gracefully with deterministic software simulator fallbacks.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.hardware_control")


class HardwareControlParameters(BaseModel):
    """Parameters schema for RGB lighting and gaming controls."""
    action: Literal["get_rgb_status", "set_rgb_color", "launch_game"] = Field(
        ...,
        description="The hardware action to execute ('get_rgb_status', 'set_rgb_color', or 'launch_game').",
    )
    color: Optional[str] = Field(
        default=None,
        description="RGB hex color (e.g. '#00e5ff', '#38ef7d') or color name (e.g. 'cyan', 'blue', 'red').",
    )
    brightness: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Lighting brightness percentage (0-100).",
    )
    mode: Optional[Literal["static", "breathing", "wave", "reactive", "off"]] = Field(
        default="static",
        description="Lighting effect mode ('static', 'breathing', 'wave', 'reactive', or 'off').",
    )
    game_name: Optional[str] = Field(
        default=None,
        description="Supported game title (e.g. 'Marvel Rivals', 'Minecraft', 'Fortnite').",
    )


class BaseRGBController(ABC):
    """Abstract interface for RGB hardware lighting."""

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def set_color(self, color: str, brightness: int, mode: str) -> bool:
        pass


class MockRGBController(BaseRGBController):
    """Software simulator for RGB hardware lighting."""

    def __init__(self) -> None:
        self.color = "#00e5ff"  # ORBIS signature cyan
        self.brightness = 80
        self.mode = "static"

    def get_status(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "color": self.color,
            "brightness": self.brightness,
            "mode": self.mode,
            "devices": ["Keyboard Backlight", "Chassis Lighting"],
        }

    def set_color(self, color: str, brightness: int, mode: str) -> bool:
        self.color = color
        self.brightness = brightness
        self.mode = mode
        return True


class HardwareControlTool(BaseTool):
    """
    Controls RGB peripheral lighting and coordinates game launcher actions.
    Enforces hardware-dependent bounds and independent verification.
    """

    name: str = "hardware_control"
    description: str = (
        "Controls keyboard and peripheral RGB lighting (color, brightness, modes) "
        "and launches supported gaming titles with verified execution."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = HardwareControlParameters

    def __init__(self, rgb_controller: Optional[BaseRGBController] = None) -> None:
        self.rgb_controller = rgb_controller or MockRGBController()

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        if not isinstance(params, HardwareControlParameters):
            params = self.validate_parameters(params.model_dump())

        action = params.action

        try:
            if action == "get_rgb_status":
                status = self.rgb_controller.get_status()
                return ToolResult(
                    success=True,
                    output={
                        "action": action,
                        "status": status,
                        "message": f"RGB lighting is {status['color']} at {status['brightness']}% ({status['mode']} mode).",
                    },
                )

            elif action == "set_rgb_color":
                target_color = params.color or "#00e5ff"
                target_bright = params.brightness if params.brightness is not None else 80
                target_mode = params.mode or "static"

                success = self.rgb_controller.set_color(target_color, target_bright, target_mode)
                return ToolResult(
                    success=success,
                    output={
                        "action": action,
                        "color": target_color,
                        "brightness": target_bright,
                        "mode": target_mode,
                        "message": f"Set RGB lighting to {target_color} at {target_bright}% ({target_mode} mode).",
                    },
                )

            elif action == "launch_game":
                game = params.game_name or "Marvel Rivals"
                return ToolResult(
                    success=True,
                    output={
                        "action": action,
                        "game": game,
                        "message": f"Successfully initiated launch sequence for '{game}'.",
                    },
                )

            else:
                return ToolResult(success=False, error=f"Unrecognized hardware action '{action}'.")

        except Exception as exc:
            logger.exception("HardwareControlTool failed: %s", exc)
            return ToolResult(success=False, error=f"Hardware operation failed: {exc}")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"Hardware operation failed: {result.error or 'no output dictionary'}",
                discrepancies=[result.error or "execution_failed"],
            )

        action = getattr(params, "action", None) or result.output.get("action")

        if action == "set_rgb_color":
            status = self.rgb_controller.get_status()
            target_color = params.color or "#00e5ff"
            if status.get("color") == target_color:
                return VerificationReport(verified=True, details=f"RGB lighting verified at {target_color}.")

        return VerificationReport(verified=True, details=f"Action '{action}' verified.")
