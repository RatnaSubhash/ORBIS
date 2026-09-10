"""
Windows Workspace Observer implementation for ORBIS.
Uses Win32 APIs via ctypes and psutil to extract non-invasive, safe metadata
about the active foreground window and process.
"""

import ctypes
import logging
import sys
from typing import Optional

from core.config import get_settings
from core.observation.base import BaseWorkspaceObserver
from core.types import WindowMetadata

logger = logging.getLogger("orbis.observation.windows")


class WindowsWorkspaceObserver(BaseWorkspaceObserver):
    """
    Observer utilizing Windows Win32 APIs and psutil to inspect
    active foreground window and process metadata.
    """

    def __init__(self) -> None:
        self.is_windows = sys.platform == "win32"
        if not self.is_windows:
            logger.warning("WindowsWorkspaceObserver running on non-Windows platform (%s).", sys.platform)

    def get_active_window(self) -> Optional[WindowMetadata]:
        """
        Queries the current active foreground window handle, title, and process.
        Enforces privacy exclusion policies.
        """
        if not self.is_windows:
            return None

        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return None

            # 1. Retrieve Window Title
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value

            # 2. Retrieve Process ID
            pid_val = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
            pid = pid_val.value

            # 3. Retrieve Process Information via psutil
            process_name = None
            executable_path = None
            if pid > 0:
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    process_name = proc.name()
                    try:
                        executable_path = proc.exe()
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        executable_path = None
                except Exception as e:
                    logger.debug("Failed to inspect process %d: %s", pid, e)

            # 4. Check Privacy Exclusion
            settings = get_settings()
            if settings.is_excluded(process_name, title):
                return WindowMetadata(
                    hwnd=hwnd,
                    title=None,
                    process_id=pid,
                    process_name=process_name,
                    executable_path=None,
                    is_excluded=True,
                )

            return WindowMetadata(
                hwnd=hwnd,
                title=title if title else None,
                process_id=pid,
                process_name=process_name,
                executable_path=executable_path,
                is_excluded=False,
            )

        except Exception as e:
            logger.warning("Error querying active Windows window: %s", e)
            return None

    def capture_snapshot(self) -> WindowMetadata:
        """Captures active window metadata or returns a fallback empty record."""
        active = self.get_active_window()
        if active:
            return active
        return WindowMetadata(
            hwnd=None,
            title=None,
            process_id=None,
            process_name=None,
            executable_path=None,
            is_excluded=False,
        )
