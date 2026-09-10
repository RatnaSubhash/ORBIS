"""
Observation Subsystem for ORBIS.
Phase 2: Windows Workspace Observation, deduplication, privacy filtering,
and structured event persistence.
"""

from core.observation.base import BaseWorkspaceObserver
from core.observation.mock import MockWorkspaceObserver
from core.observation.service import WorkspaceObservationService
from core.observation.windows import WindowsWorkspaceObserver

__all__ = [
    "BaseWorkspaceObserver",
    "WindowsWorkspaceObserver",
    "MockWorkspaceObserver",
    "WorkspaceObservationService",
]
