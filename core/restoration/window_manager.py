"""
Window Layout Manager for ORBIS Phase 10 Workspace Restoration.
Provides platform-independent window manipulation and geometry queries,
binding window management strictly to verified process IDs.
"""

from abc import ABC, abstractmethod
import ctypes
import logging
import sys
from typing import Any, Dict, List, Optional
import psutil

from core.restoration.models import (
    VirtualDesktopGeometry,
    WindowBounds,
    WindowPlacementState,
)

logger = logging.getLogger("orbis.restoration.window_manager")

# Windows Win32 Constants
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class BaseWindowManager(ABC):
    """Abstract interface for desktop display geometry and window layout control."""

    @abstractmethod
    def get_virtual_desktop_geometry(self) -> VirtualDesktopGeometry:
        """Returns the total bounding geometry spanning all active displays."""
        pass

    @abstractmethod
    def find_windows_by_pid(self, pid: int) -> List[int]:
        """Enumerates visible top-level window handles belonging strictly to the target PID."""
        pass

    @abstractmethod
    def get_window_title(self, hwnd: int) -> Optional[str]:
        """Retrieves the window title for a given HWND."""
        pass

    @abstractmethod
    def get_window_bounds(self, hwnd: int) -> Optional[WindowBounds]:
        """Queries the current screen rectangle of a given window handle."""
        pass

    @abstractmethod
    def set_window_bounds(self, hwnd: int, bounds: WindowBounds) -> bool:
        """Reposition and resize a specific window handle without changing focus order."""
        pass

    @abstractmethod
    def set_window_placement(self, hwnd: int, placement: WindowPlacementState) -> bool:
        """Sets the show state (normal, maximized, minimized) of a specific window handle."""
        pass

    @abstractmethod
    def is_process_running(self, pid: int) -> bool:
        """Verifies whether the target process is actively running."""
        pass


class WindowsWindowManager(BaseWindowManager):
    """
    Production Win32 window manager using user32.dll APIs via ctypes.
    Strictly correlates windows to target PIDs before layout modification.
    """

    def __init__(self) -> None:
        self.is_windows = sys.platform == "win32"
        if not self.is_windows:
            logger.warning("WindowsWindowManager initialized on non-Windows OS (%s).", sys.platform)

    def get_virtual_desktop_geometry(self) -> VirtualDesktopGeometry:
        """Queries virtual screen metrics across all connected monitors."""
        if not self.is_windows:
            return VirtualDesktopGeometry()

        try:
            user32 = ctypes.windll.user32
            x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            return VirtualDesktopGeometry(left=x, top=y, width=w, height=h)
        except Exception as e:
            logger.warning("Failed to query virtual screen metrics: %s", e)
            return VirtualDesktopGeometry()

    def find_windows_by_pid(self, pid: int) -> List[int]:
        """
        Enumerates top-level windows matching the target PID.
        Guarantees that windows of pre-existing unrelated instances are excluded.
        """
        if not self.is_windows or pid <= 0:
            return []

        user32 = ctypes.windll.user32
        matched_hwnds: List[int] = []

        # WNDENUMPROC callback signature: BOOL (HWND, LPARAM)
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)

        def _enum_proc(hwnd: int, lparam: ctypes.c_void_p) -> bool:
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))

            if window_pid.value == pid:
                # Filter for visible, top-level application windows
                if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                    matched_hwnds.append(hwnd)
            return True

        cb = WNDENUMPROC(_enum_proc)
        try:
            user32.EnumWindows(cb, 0)
        except Exception as e:
            logger.debug("EnumWindows raised exception: %s", e)

        return matched_hwnds

    def get_window_title(self, hwnd: int) -> Optional[str]:
        """Retrieves window text for the handle."""
        if not self.is_windows or not hwnd:
            return None

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        return None

    def get_window_bounds(self, hwnd: int) -> Optional[WindowBounds]:
        """Queries the RECT of the window."""
        if not self.is_windows or not hwnd:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        user32 = ctypes.windll.user32
        r = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            w = max(0, r.right - r.left)
            h = max(0, r.bottom - r.top)
            return WindowBounds(x=r.left, y=r.top, width=w, height=h)
        return None

    def set_window_bounds(self, hwnd: int, bounds: WindowBounds) -> bool:
        """Applies position and size without changing z-order or stealing activation."""
        if not self.is_windows or not hwnd:
            return False

        user32 = ctypes.windll.user32
        res = user32.SetWindowPos(
            hwnd,
            0,
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        return bool(res)

    def set_window_placement(self, hwnd: int, placement: WindowPlacementState) -> bool:
        """Restores, maximizes, or minimizes window show state."""
        if not self.is_windows or not hwnd:
            return False

        user32 = ctypes.windll.user32
        cmd = SW_SHOWNORMAL
        if placement == WindowPlacementState.MAXIMIZED:
            cmd = SW_SHOWMAXIMIZED
        elif placement == WindowPlacementState.MINIMIZED:
            cmd = SW_SHOWMINIMIZED

        res = user32.ShowWindow(hwnd, cmd)
        return bool(res)

    def is_process_running(self, pid: int) -> bool:
        """Verifies process liveness via psutil."""
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False


class MockWindowManager(BaseWindowManager):
    """
    Deterministic, in-memory WindowManager for isolated unit and integration testing.
    Simulates multi-monitor virtual desktop geometry and PID-bound window state.
    """

    def __init__(
        self,
        virtual_geometry: Optional[VirtualDesktopGeometry] = None,
    ) -> None:
        # Default dual-monitor geometry: left monitor (-1920..0), primary monitor (0..1920)
        self.virtual_geometry = virtual_geometry or VirtualDesktopGeometry(
            left=-1920, top=0, width=3840, height=1080
        )
        self.processes: Dict[int, Dict[str, Any]] = {}
        self.windows: Dict[int, Dict[str, Any]] = {}
        self._next_hwnd = 1000

    def register_simulated_process(self, pid: int, name: str) -> None:
        """Registers a live mock process."""
        self.processes[pid] = {"pid": pid, "name": name, "running": True}

    def register_simulated_window(
        self,
        pid: int,
        title: str,
        bounds: Optional[WindowBounds] = None,
        placement: WindowPlacementState = WindowPlacementState.NORMAL,
        hwnd: Optional[int] = None,
    ) -> int:
        """Creates an in-memory window handle strictly bound to a PID."""
        assigned_hwnd = hwnd or self._next_hwnd
        self._next_hwnd += 1

        self.windows[assigned_hwnd] = {
            "hwnd": assigned_hwnd,
            "pid": pid,
            "title": title,
            "bounds": bounds or WindowBounds(),
            "placement": placement,
            "visible": True,
        }
        return assigned_hwnd

    def get_virtual_desktop_geometry(self) -> VirtualDesktopGeometry:
        return self.virtual_geometry

    def find_windows_by_pid(self, pid: int) -> List[int]:
        """Enumerates windows belonging strictly to the PID."""
        return [
            hwnd
            for hwnd, info in self.windows.items()
            if info["pid"] == pid and info.get("visible", True)
        ]

    def get_window_title(self, hwnd: int) -> Optional[str]:
        w = self.windows.get(hwnd)
        return w["title"] if w else None

    def get_window_bounds(self, hwnd: int) -> Optional[WindowBounds]:
        w = self.windows.get(hwnd)
        return w["bounds"] if w else None

    def set_window_bounds(self, hwnd: int, bounds: WindowBounds) -> bool:
        if hwnd in self.windows:
            self.windows[hwnd]["bounds"] = bounds
            return True
        return False

    def set_window_placement(self, hwnd: int, placement: WindowPlacementState) -> bool:
        if hwnd in self.windows:
            self.windows[hwnd]["placement"] = placement
            return True
        return False

    def is_process_running(self, pid: int) -> bool:
        p = self.processes.get(pid)
        return p["running"] if p else False
