"""
Deterministic Mock Workspace Observer for ORBIS.
Enables reliable, hermetic testing of observation services and tools.
"""

from typing import Optional

from core.observation.base import BaseWorkspaceObserver
from core.types import WindowMetadata


class MockWorkspaceObserver(BaseWorkspaceObserver):
    """Mock observer whose current active window state can be set directly in tests."""

    def __init__(self, initial_window: Optional[WindowMetadata] = None) -> None:
        self.current_window = initial_window

    def set_active_window(
        self,
        title: Optional[str] = "Visual Studio Code",
        process_name: Optional[str] = "Code.exe",
        process_id: Optional[int] = 1234,
        executable_path: Optional[str] = "C:\\Program Files\\VS Code\\Code.exe",
        hwnd: Optional[int] = 1001,
        is_excluded: bool = False,
    ) -> WindowMetadata:
        """Helper to configure the mock active window state."""
        self.current_window = WindowMetadata(
            hwnd=hwnd,
            title=title,
            process_id=process_id,
            process_name=process_name,
            executable_path=executable_path,
            is_excluded=is_excluded,
        )
        return self.current_window

    def clear(self) -> None:
        """Clears the active window state (simulating no foreground window)."""
        self.current_window = None

    def get_active_window(self) -> Optional[WindowMetadata]:
        return self.current_window

    def capture_snapshot(self) -> WindowMetadata:
        if self.current_window:
            return self.current_window
        return WindowMetadata(
            hwnd=None,
            title=None,
            process_id=None,
            process_name=None,
            executable_path=None,
            is_excluded=False,
        )
