"""
Domain Models for ORBIS Phase 10 Multi-App & Window Layout Restoration.
Defines strongly typed models for window bounds, placement states, resource targets,
workspace snapshots, declarative restoration steps and plans, step outcomes, and verifications.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field

from core.types import RiskLevel


class WindowPlacementState(str, Enum):
    """Windows show/placement state representation."""
    NORMAL = "NORMAL"          # SW_SHOWNORMAL (restored/windowed)
    MAXIMIZED = "MAXIMIZED"    # SW_SHOWMAXIMIZED
    MINIMIZED = "MINIMIZED"    # SW_SHOWMINIMIZED
    UNKNOWN = "UNKNOWN"


class ResourceTargetType(str, Enum):
    """Type of resource to be reopened or navigated to."""
    WORKSPACE_FILE = "WORKSPACE_FILE"
    WEB_URL = "WEB_URL"
    DIRECTORY = "DIRECTORY"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class WindowBounds(BaseModel):
    """
    Safe window geometry rectangle on the virtual desktop display.
    Negative coordinates are valid in multi-monitor configurations (e.g. secondary monitor to the left or above).
    """
    x: int = Field(default=100, ge=-20000, le=40000, description="Virtual desktop X coordinate in pixels")
    y: int = Field(default=100, ge=-20000, le=40000, description="Virtual desktop Y coordinate in pixels")
    width: int = Field(default=1024, ge=200, le=10000, description="Window width in pixels")
    height: int = Field(default=768, ge=200, le=10000, description="Window height in pixels")


class VirtualDesktopGeometry(BaseModel):
    """Geometry bounds of the active Windows virtual desktop spanning all monitors."""
    left: int = 0
    top: int = 0
    width: int = 1920
    height: int = 1080

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains_point(self, x: int, y: int) -> bool:
        """Determines if coordinate point falls within the virtual display rectangle."""
        return self.left <= x < self.right and self.top <= y < self.bottom


class WindowState(BaseModel):
    """Observed or target window state for an application."""
    process_name: str
    window_title: Optional[str] = None
    bounds: Optional[WindowBounds] = None
    placement: WindowPlacementState = WindowPlacementState.NORMAL
    is_active_foreground: bool = False


class ResourceTarget(BaseModel):
    """A validated target resource to be restored within an application."""
    target_type: ResourceTargetType
    raw_target: str
    resolved_target: Optional[str] = None
    is_valid: bool = False
    validation_error: Optional[str] = None


class WorkspaceSnapshot(BaseModel):
    """Declarative snapshot of a multi-application workspace state."""
    snapshot_id: str = Field(default_factory=lambda: f"snap_{uuid4().hex[:8]}")
    session_id: str
    intent_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    applications: List[str] = Field(default_factory=list)
    resources: List[ResourceTarget] = Field(default_factory=list)
    window_states: List[WindowState] = Field(default_factory=list)


class RestorationStepStatus(str, Enum):
    """Execution status of an individual restoration step."""
    PENDING = "PENDING"
    SKIPPED_INVALID = "SKIPPED_INVALID"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RestorationStep(BaseModel):
    """A single discrete, declarative restoration operation."""
    step_id: str = Field(default_factory=lambda: f"rstep_{uuid4().hex[:8]}")
    app_identifier: str
    command_executable: str = Field(description="Fully resolved trusted executable path")
    command_args: List[str] = Field(default_factory=list)
    resource: Optional[ResourceTarget] = None
    target_window_state: Optional[WindowState] = None
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    status: RestorationStepStatus = RestorationStepStatus.PENDING
    status_message: Optional[str] = None


class RestorationPlan(BaseModel):
    """
    A structured, preflight-validated declarative workspace restoration plan.
    Maintains authoritative intrinsic risk and policy-based approval strictly separated.
    """
    plan_id: str = Field(default_factory=lambda: f"rplan_{uuid4().hex[:8]}")
    session_id: str
    goal_description: str
    steps: List[RestorationStep] = Field(default_factory=list)
    overall_risk: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="Authoritative intrinsic risk level (LOW, MEDIUM, HIGH)."
    )
    requires_user_approval: bool = Field(
        default=True,
        description="Policy-based approval flag (e.g. multi-app policy requires approval regardless of risk)."
    )
    is_preflight_valid: bool = False
    preflight_errors: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RestorationStepResult(BaseModel):
    """Outcome of executing a single restoration step."""
    step_id: str
    app_identifier: str
    launched: bool = False
    process_id: Optional[int] = None
    target_hwnd: Optional[int] = None
    window_placed: bool = False
    error: Optional[str] = None
    execution_time_ms: float = 0.0


class RestorationVerification(BaseModel):
    """Independent verification assessment of workspace restoration."""
    step_id: str
    verified: bool
    process_running: bool = False
    window_visible: bool = False
    details: str
    discrepancies: List[str] = Field(default_factory=list)


class RestorationResult(BaseModel):
    """Comprehensive final outcome and audit report for workspace restoration."""
    plan_id: str
    session_id: str
    success: bool
    is_partial_restoration: bool
    completed_steps: List[str] = Field(default_factory=list)
    failed_steps: List[str] = Field(default_factory=list)
    skipped_steps: List[str] = Field(default_factory=list)
    step_results: Dict[str, RestorationStepResult] = Field(default_factory=dict)
    verification_reports: Dict[str, RestorationVerification] = Field(default_factory=dict)
    summary: str
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
