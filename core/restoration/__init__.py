"""
ORBIS Phase 10: Multi-App & Window Layout Restoration.
Provides controlled, declarative workspace reconstruction grounded in Phase 9
Intent Continuity Engine evidence and verified system window layouts.
"""

from core.restoration.models import (
    ResourceTarget,
    ResourceTargetType,
    RestorationPlan,
    RestorationResult,
    RestorationStep,
    RestorationStepResult,
    RestorationStepStatus,
    RestorationVerification,
    VirtualDesktopGeometry,
    WindowBounds,
    WindowPlacementState,
    WindowState,
    WorkspaceSnapshot,
)

__all__ = [
    "ResourceTarget",
    "ResourceTargetType",
    "RestorationPlan",
    "RestorationResult",
    "RestorationStep",
    "RestorationStepResult",
    "RestorationStepStatus",
    "RestorationVerification",
    "VirtualDesktopGeometry",
    "WindowBounds",
    "WindowPlacementState",
    "WindowState",
    "WorkspaceSnapshot",
]
