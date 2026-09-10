"""
Abstract Base Workspace Observer Interface for ORBIS.
Decouples workspace observation logic from operating-system-specific APIs.
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.types import WindowMetadata


class BaseWorkspaceObserver(ABC):
    """Abstract interface for desktop workspace observation."""

    @abstractmethod
    def get_active_window(self) -> Optional[WindowMetadata]:
        """
        Samples the current active foreground window and returns structured metadata.
        Returns None if no active window can be determined.
        """
        pass

    @abstractmethod
    def capture_snapshot(self) -> WindowMetadata:
        """
        Returns a comprehensive snapshot of the active workspace state.
        """
        pass
